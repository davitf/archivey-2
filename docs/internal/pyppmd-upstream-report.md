# pyppmd upstream report — 1.3.x native heap corruption on valid PPMd7 data

Status: **not yet filed** (as of 2026-07-16 there is no matching issue at
<https://github.com/miurahr/pyppmd/issues>). This document is a ready-to-file
issue draft plus the supporting analysis. File it against
[miurahr/pyppmd](https://github.com/miurahr/pyppmd) and attach
`scripts/pyppmd_crash_repro.py` (self-contained: `pyppmd` + stdlib only).

Archivey context (what we ship regardless of the upstream fix) lives in
`docs/internal/known-issues.md` → “Intermittent `pyppmd` native aborts”.

---

## Suggested issue draft

> **Title:** 1.3.x regression: heap corruption / SIGSEGV decoding *valid* PPMd7
> data whenever the decode request exceeds the remaining payload — `-1`,
> after-eof, or plain oversized `max_length` (ThreadDecoder.c input-empty stop
> condition removed in #126)

### Summary

Since pyppmd **1.3.0**, `Ppmd7Decoder.decode` intermittently aborts the
process on **valid** PPMd7 data whenever the requested output materially
exceeds what the stream can still produce — via `max_length=-1`, via any
`decode` call issued after `eof`, or via a plain sized request ≳64 KiB past
the true payload (no `-1` involved). Symptoms: `malloc(): invalid size
(unsorted)` / SIGSEGV / SIGABRT on Linux, `STATUS_HEAP_CORRUPTION`
(`0xC0000374`) on Windows. 1.1.1 and 1.2.0 do not crash on the same inputs
(they have a different bug: short/incorrect output on chunked decodes, which
#126 was fixing).

Measured with the attached single-file repro (fresh subprocess children,
5 encode/decode cycles each):

| mode | pattern | pyppmd 1.3.1 (Linux A) | (Linux B) |
|------|---------|------------------------|-----------|
| `extra-null` | sized decode to `eof`, then `decode(b"\0", -1)` | ~40% of children | **30/30** |
| `overshoot` | single `decode(packed, -1)` (no second call) | ~15–25% | **19/30** |
| `oversized` | single **sized** `decode(packed, len(data) + 65536)` — no `-1` anywhere | — | **10–13/20** |
| `sized-safe` | `decode(packed, len(data))` only | 0% | 0/30 |
| `underfed-sized` | sized decode, half the input, then dealloc | 0% | 0/20 |
| `hostile-tail` | sized decode to eof, then small bounded decode of garbage | 0% | 0/20 |

The `oversized` row is the sharpest datapoint: the crash does **not** require the
`-1` sentinel. Requesting materially more output than the stream's true remaining
payload is what crashes — `len(data) + 64` and `len(data) + 4096` over-requests were
0/20 each, `+65536` crashed 13/20 (and an equivalent shape through a 64 KiB-chunked
wrapper crashed 9/20). Requesting exactly the remaining output is safe at any size
(a 1.2 MB payload decoded in 64 KiB input chunks with the exact total bound: 0/20
across 60 full decodes).

Linux A: the original CI investigation (x86_64, CPython 3.11/3.14).
Linux B: independent re-run (x86_64, CPython 3.11.15, glibc 2.39,
Linux 6.18.5). Rates vary with allocator layout — runs that exercise other
codecs first crash more often — but the safe/unsafe split is stable.

```bash
pip install 'pyppmd==1.3.1'
python pyppmd_crash_repro.py 30 --mode extra-null   # crashes
python pyppmd_crash_repro.py 30 --mode overshoot    # crashes
python pyppmd_crash_repro.py 30 --mode oversized    # crashes, no -1 involved
python pyppmd_crash_repro.py 30 --mode sized-safe   # control, clean
```

`faulthandler` places the abort **inside the `decode` call** (not at object
teardown), typically after the call has already returned thousands of bytes
that were never in the encoded stream.

### Root cause analysis (from the 1.2.0 → 1.3.1 sdist diff)

The regression is the `src/lib/buffer/ThreadDecoder.c` rewrite that shipped in
**1.3.0** (PR #126, “Fix several issues in ThreadDecoder.c”). Three pieces
interact:

1. **The worker no longer stops when input is exhausted.** In 1.2.0,
   `Ppmd7T_decode_run` / `Ppmd8T_decode_run` broke out of the symbol loop on
   `inbuf_empty && size > 0`. 1.3.0 removed that check (“Only stop when output
   buffer is full”), relying on `Ppmd_thread_Reader` to block on the
   `notEmpty` condition when `pos == size`.

2. **`max_length=-1` gives the worker an unbounded budget.** In
   `_ppmdmodule.c`, `remains = length >= 0 ? length : INT_MAX`, and the
   output buffer grows on demand. PPMd7 has **no end mark**, so nothing in the
   data tells the worker where the payload ends. Once the true stream is
   consumed, the worker keeps calling `Ppmd7_DecodeSymbol` on a
   **desynchronized range coder**, walking the PPMd model with garbage — the
   vendored 7-Zip model code is not memory-safe in that state, and the heap
   corrupts. That is the `overshoot` mode. In 1.2.0 the per-symbol
   input-empty check made this window one symbol wide; in 1.3.x it is
   unbounded.

3. **The after-eof guard was only added to the cffi backend.** 1.3.0 added an
   `_eof` early-return to `cffi_ppmd.py`’s `decode`, but `_ppmdmodule.c` (the
   C extension virtually all wheels use — crash dumps show `pyppmd.c._ppmd`)
   has no such guard. `decode(b"\0", -1)` after `eof` therefore starts a fresh
   `INT_MAX`-budget worker on finished range-coder state: pure garbage
   decoding, the hottest trigger (`extra-null`).

Two secondary hazards in the same file (not the observed crash site, but
worth fixing while there):

- **`Ppmd7T_Free` wakes the blocked worker without giving it input.** It sets
  `tc->empty = False` and broadcasts `notEmpty` before `pthread_cancel`. A
  worker parked in `Ppmd_thread_Reader` then leaves the wait loop and executes
  `*((const Byte *)inBuffer->src + inBuffer->pos++)` with `pos == size` — an
  out-of-bounds read of a buffer whose backing memory (the Python-level input)
  may already be released — and, because cancellation is deferred and the
  symbol loop has no cancellation points, it can decode into the previous
  call's finished output block before the cancel lands at the next blocking
  read.
- **`Ppmd_thread_Reader` only blocks on `pos == size`**, so once `pos`
  overshoots by one it never blocks again and free-runs past the buffer.
  The check should be `pos >= size`.

### Why exact-sized decodes are safe (and py7zr never sees this)

With `max_length` equal to the remaining declared output, the worker’s budget
runs out exactly at the payload boundary and it exits cleanly — the model
never runs past the end of stream. py7zr always passes `max_length` to
`decompress`, which is why it doesn't exhibit the crash. Callers can use the
same discipline as a workaround (this is what archivey now does), but the
library-level behavior is still a crash on valid data through a documented
API — and per the `oversized` row, a caller who innocently over-requests
(e.g. asks for a full read-buffer's worth near end of stream) hits the same
corruption with no `-1` involved, so the sized API is only safe when the
caller already knows the exact payload size. For end-markless PPMd7 that
makes safe use impossible without external size information.

### Suggested fixes

1. Restore a stop condition for unbounded decodes: when input is exhausted
   and the range coder would need another byte, park/return instead of
   decoding further symbols (or bound the budget by what the input can
   support).
2. Port the cffi `_eof` guard to `_ppmdmodule.c`: `decode` after `eof`
   returns `b""` without touching native state.
3. In `Ppmd7T_Free` / `Ppmd8T_Free`, signal termination through a dedicated
   flag the reader re-checks after wakeup instead of faking “input available”
   (`tc->empty = False`) with no data.
4. Change the reader’s empty check to `pos >= size`.

---

## Verification checklist for a fixed release

When an upstream fix ships, re-run (all should be 0 crashes):

```bash
python scripts/pyppmd_crash_repro.py 50 --mode extra-null
python scripts/pyppmd_crash_repro.py 50 --mode overshoot
python scripts/pyppmd_crash_repro.py 50 --mode oversized
python scripts/pyppmd_crash_repro.py 50 --mode warmup-overshoot
python scripts/pyppmd_crash_repro.py 30 --mode underfed-sized
python scripts/pyppmd_crash_repro.py 30 --mode hostile-tail
uv run --no-sync python scripts/ppmd_native_stress.py 30 --scenarios warmup_codecs
uv run --no-sync pytest tests/test_ppmd_raw_streams.py -q
```

The non-blocking **PPMd native stress** workflow runs the same scenarios on
every PR (Linux + Windows × py3.11/3.14) and will catch a regression on
either side; the deterministic adversarial shapes (truncation, early close,
hostile tails) are pinned in `tests/test_ppmd_raw_streams.py`.
