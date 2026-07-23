# PPMd / pyppmd native investigation — results

**Brief:** `docs/internal/ppmd-native-investigation-brief.md`
**Prior work:** `docs/internal/pyppmd-upstream-report.md`,
`docs/internal/ppmd-exit-after-green-exploration.md`,
`docs/internal/known-issues.md`, `scripts/pyppmd_crash_repro.py`
**Date started:** 2026-07-23
**Status:** complete (empirical + source). Findings appended as experiments
finished. Source
citations are against **pyppmd `v1.3.1`** (the pinned wheel; git tag `v1.3.1`,
commit `580b675`). The wheel installed in this env is `pyppmd 1.3.1`, CPython
3.11.15, glibc 2.39, Linux 6.18.5.

This report answers the brief from **native source + minimal repros**, not from
archivey's mitigations. Where the archivey labs already measured a behaviour, it
is cited and the *source-level cause* is added rather than re-measured.

---

## TL;DR verdict

The heap corruption is **not** primarily the Pavlov suballocator — valgrind's
*first* memory error in every crash family is a **use-after-free of pyppmd's own
output buffer** at `ThreadDecoder.c:134`, on a block already freed by
`OutputBuffer_Finish` (`_ppmdmodule.c:552`). Every failure mode below reduces to
that one bug, gated by one condition: **did the worker finish, or is it left
blocked in the reader past logical EOF?**

| Failure mode | 7-Zip / Pavlov PPMd core | pyppmd ThreadDecoder / binding | Caller contract |
|--------------|--------------------------|--------------------------------|-----------------|
| overshoot `max_length` (`-1` or `+65536`) | Not the fault site. The desynced model only *generates the garbage symbols*; it does not write outside its own `p->Base` arena first. | **Root cause (pyppmd):** worker left blocked-in-reader (1.3.0 `#126` removed the input-empty stop) while `OutputBuffer_Finish` frees the output block → later wake ⇒ **UAF write, free-running** (reader `pos==size` bug). Valgrind: 33 423 writes at `ThreadDecoder.c:134`. | Never request more output than the true remaining payload. |
| post-eof empty decode toward `unpack_size` | Not the fault site. | Same UAF. No after-eof guard in the **C extension** `decode` (guard is cffi-only) restarts a runaway worker. | Do not `decode` after `eof` unless pack delivery is known complete. |
| half-pack + large NUL / after-eof NUL | Not the fault site. | Same UAF (valgrind: 59 522 writes at `ThreadDecoder.c:134`, same single context). | Cap the synthetic-NUL budget; do not chase `unpack_size` on short pack. |
| `Ppmd7T_Free` while worker blocked | Not applicable (7-Zip is non-threaded here). | **pyppmd-only:** `Ppmd7T_Free` fakes "input available" (`tc->empty=False`) then cancels; that wake is *what drives the UAF above*, and the reader also does an OOB `src[pos++]` at `pos==size`. | n/a (teardown bug). |
| omit-trailing-null / "extra byte" | **Container/reader convention**, not an encoder action. | pyppmd's reader **blocks** at EOF instead of returning 0, so the caller must feed `b"\0"` to emulate 7-Zip's over-read-zero. | Keep bounded NUL recovery for members produced by real 7-Zip. |

Control that pins it: **exact-sized `decode(packed, len(data))` is 0 valgrind
errors** — the worker's budget runs out exactly at the payload boundary, it
returns and is joined, `finished=True`, and `Ppmd7T_Free` never wakes it. Any
overshoot leaves it blocked-in-reader instead, and the freed output block is then
written by the resumed worker.

Bottom line: this is a **pyppmd binding bug** — three compounding defects, all in
`ThreadDecoder.c` / `_ppmdmodule.c` / `blockoutput.h` (Section D), introduced by
the 1.3.0 `#126` rewrite. Igor Pavlov's `Ppmd7.c` is *memory-unsafe if driven
past EOF*, but that is a latent property the caller must respect (real 7-Zip
always decodes to the exact known unpack size and never trips it); it is **not**
the first corrupting write here. The regression and the fix both belong to
pyppmd.

---

## A. Code map (which file owns what)

Vendored tree under `pyppmd/src/`:

| File | Provenance | Role |
|------|-----------|------|
| `lib/ppmd/Ppmd7.c`, `Ppmd7.h` | Igor Pavlov / 7-Zip (`2017-04-03 : Igor Pavlov : Public domain`, "based on PPMd var.H (2001): Dmitry Shkarin") | Model + **suballocator** (`InsertNode`/`RemoveNode`/`GlueFreeBlocks`/`AllocUnits`), range-dec vtable |
| `lib/ppmd/Ppmd7Enc.c` | Pavlov / 7-Zip | Range encoder + `Ppmd7z_RangeEnc_FlushData`, `Ppmd7_EncodeSymbol` |
| `lib/ppmd/Ppmd7Dec.c` | Pavlov / 7-Zip | Range decoder + `Ppmd7_DecodeSymbol` |
| `lib/ppmd/Ppmd8*.c` | Pavlov / 7-Zip (var.I, RAR) | PPMd8 model/enc/dec (has a real end mark) |
| `lib/buffer/ThreadDecoder.c/.h` | **pyppmd original** (`Created by miurahr 2021/08/07`) | Worker-thread wrapper, blocking reader, `Ppmd7T_decode`, `Ppmd7T_Free` |
| `lib/buffer/Buffer.c/.h` | pyppmd original | `BufferReader`/`BufferWriter`, `InBuffer`/`OutBuffer`, growable output |
| `ext/_ppmdmodule.c` | pyppmd original | Python `Ppmd7Encoder`/`Ppmd7Decoder` (+ Ppmd8), `eof`/`needs_input` state machine, `remains` budget |

So the **algorithm** files (`Ppmd7*.c`) are untouched Pavlov code; the
**threading, budget, and lifecycle** live entirely in pyppmd's own
`ThreadDecoder.c` + `_ppmdmodule.c`. This division is what makes the verdict
crisp.

### How a decode byte flows

`_ppmdmodule.c:335` wires the range decoder's input stream to the **blocking**
reader: `bufferReader->Read = Ppmd_thread_Reader`. Every
`IByteIn_Read(p->Stream)` inside `Ppmd7Dec.c` (`Range_Normalize`,
`Ppmd7z_RangeDec_Init`) therefore calls `Ppmd_thread_Reader`
(`ThreadDecoder.c:65`), which **blocks on `notEmpty` when input is exhausted**
(`pos == size`) instead of returning a byte. The actual symbol loop runs on a
separate worker thread (`Ppmd7T_decode_run`), decoupled from the Python call by
condition variables.

---

## B. "Omit last null" / the extra input byte

### What the encoder actually does

`Ppmd7Encoder.flush()` (`_ppmdmodule.c:851`) optionally encodes an end-mark
symbol **only if `endmark=True`** (default `False`), then unconditionally calls
`Ppmd7z_RangeEnc_FlushData` (`Ppmd7Enc.c:67`):

```c
void Ppmd7z_RangeEnc_FlushData(CPpmd7z_RangeEnc *p) {
  unsigned i;
  for (i = 0; i < 5; i++)
    RangeEnc_ShiftLow(p);   // always emits 5 range-coder bytes
}
```

**There is no code that omits a trailing `0x00`.** The encoder writes exactly 5
flush bytes, unconditionally. The README's

> "The encoder will omit a last null (`b"\0"`) byte when last byte is `b"\0"`."

is therefore **not a description of `Ppmd7Enc.c`** — pyppmd's own encoder omits
nothing. This matches the archivey labs: `encode()+flush()` round-trips on 1.3.1
need the synthetic NUL in **0/60768** trials.

### Where the "extra byte" convention really comes from

It is a **reader convention**, not an encoder action. Two facts from source:

1. The PPMd7 range decoder does a **1-byte lookahead** at the tail. `Range_Normalize`
   (`Ppmd7Dec.c:26`) reads a fresh byte whenever `Range < kTopValue`; near the
   end of a stream it can demand one more byte than the payload strictly needed.
2. pyppmd's reader **blocks** at EOF (`ThreadDecoder.c:70-81`) rather than
   synthesising that lookahead byte.

In stock **7-Zip**, the PPMd stream is read through an in-byte wrapper
(`CByteInBufWrap`-style) whose `ReadByte` **returns `0` and bumps an
"extra bytes" counter once the buffer is exhausted**, so the decoder's tail
lookahead transparently reads zeros past end-of-input. That over-read-zero
behaviour is exactly what lets 7-Zip *store* a PPMd pack stream with a trailing
`0x00` trimmed — "omit last null" is a **container storage optimisation that
depends on the decoder reader returning 0 past EOF**, not on the encoder
dropping a byte.

pyppmd's blocking reader does **not** return 0 past EOF, so the binding pushes
that responsibility onto the caller: the README's
`decode(b"\0", length - len(result))` is the caller **manually feeding the zero
byte 7-Zip's reader would have synthesised**. Confirmation to run (Section G):
feeding `b"\0"` unblocks a stream that `needs_input` at the tail, whereas the
same stream stalls forever if you never feed it.

**Implication for archivey:** keep bounded NUL recovery — it is required for
members produced by real 7-Zip that were stored with a trimmed trailing zero,
even though pyppmd's *own* encoder never produces such a stream. The cap must be
bounded (not `unpack_size`) because the same `decode(b"\0", big)` call is the
overshoot crash primitive (Section D).

---

## C. `max_length` and `eof` semantics

### The budget

`_ppmdmodule.c:521`:

```c
int remains = length >= 0 ? length : INT_MAX;   // <-- -1 becomes INT_MAX
```

The Python `length` (`max_length`) is a plain symbol budget. `-1` is not "decode
to end mark" (PPMd7 has none) — it is "decode up to INT_MAX symbols". The output
buffer grows on demand (`OutputBuffer_Grow`, `_ppmdmodule.c:534`), so nothing
bounds the run except (a) the budget and (b) the worker's `outbuf_full` check.

### The worker's stop condition (the #126 regression)

`Ppmd7T_decode_run` (`ThreadDecoder.c:110-137`):

```c
while (i < max_length) {
    Bool outbuf_full = threadInfo->out->size == threadInfo->out->pos;
    /* Only stop when output buffer is full. Do NOT stop just because
       input buffer appears empty ... */
    if (outbuf_full) break;
    int c = Ppmd7_DecodeSymbol(cPpmd7, rc);   // may block in reader, or run on stale state
    ...
}
```

Pre-1.3.0, the loop also broke when input was exhausted. #126 removed that,
commenting that the reader will block instead. That is true **only while more
real input is coming**. When the whole pack has been fed in one `decode` call
and `max_length` exceeds the payload, the range coder still has its 5 flush
bytes + code register, so `Ppmd7_DecodeSymbol` keeps **returning symbols without
reading input** — it walks the model on a desynchronised range coder, emitting
garbage, until it either finally demands a byte (reader blocks) or the model
throws `-1`/`-2`. With `INT_MAX`/oversized budget that window is effectively
unbounded — and, crucially, it usually ends with the worker **blocked in the
reader** rather than finished, which is the precondition for the output-buffer
UAF (Section D). This is the mechanism behind `overshoot` and `oversized`
(`scripts/pyppmd_crash_repro.py`), and behind half-pack + large NUL.

### Premature `eof`

`_ppmdmodule.c:553`:

```c
if (Ppmd7z_RangeDec_IsFinishedOK(self->rangeDec)) self->eof = True;
```

and `Ppmd7.h:107`:

```c
#define Ppmd7z_RangeDec_IsFinishedOK(p) ((p)->Code == 0)
```

`eof` is set **whenever the range-coder `Code` register is 0 at the end of a
decode call** — not when the stream is genuinely finished. On highly
compressible data decoded with a small `max_length`, the `Code` register
transiently hits 0 after the first ~64 output bytes, so `eof=True` is reported
even though `unpack_size` is far larger. That is the "premature eof" the brief
asks about, and it is a direct consequence of using `Code == 0` as an
end-of-stream proxy. The register returning to a non-zero value on the next
symbol is why *ignoring* the premature eof and continuing with `decode(b"", n)`
can still complete a **valid** stream (the archivey chunked-drain observation) —
and why the same continuation on a **truncated** stream runs the overshoot
primitive into the ground.

`needs_input` is set (`_ppmdmodule.c:541`) when the worker returned `0`
(reader blocked / input exhausted), i.e. `Ppmd7T_decode` took the `inempty`
path (`ThreadDecoder.c:189`). So the state machine is:

- worker fills output → `result = i > 0`, loop continues / stops on budget;
- reader blocks (input empty) → `result = 0` → `needs_input = True`;
- `Ppmd7_DecodeSymbol` returns `-1` → `result = -1` → `eof = True`;
- returns `-2` → `ValueError("Corrupted input data")`;
- after any call, `Code == 0` → `eof = True` (the premature-eof path).

---

## D. Corruption mechanism (root cause — valgrind-confirmed)

The heap-corruption abort (`corrupted size vs. prev_size` / `malloc(): invalid
size` / SIGABRT / SIGSEGV, Windows `STATUS_HEAP_CORRUPTION 0xC0000374`) is a
**use-after-free of pyppmd's output buffer**, *not* (primarily) a Pavlov
suballocator wild-write. Valgrind (memcheck) reports the same single error
context for `overshoot`, `oversized`, and after-eof `extra-null`:

```
Invalid write of size 1
   at 0x...: Ppmd7T_decode_run (ThreadDecoder.c:134)
   by ...: start_thread (pthread_create.c:447)
 Address 0x... is 1,838 bytes inside a block of size 32,801 free'd
   at ...: free
   by ...: OutputBuffer_Finish (blockoutput.h:253)
   by ...: Ppmd7Decoder_decode (_ppmdmodule.c:552)
 Block was alloc'd at
   by ...: OutputBuffer_InitAndGrow (blockoutput.h:79) / Ppmd7Decoder_decode:504
```

`ThreadDecoder.c:134` is the worker's output write
`*((Byte *)threadInfo->out->dst + threadInfo->out->pos++) = (Byte) c;`. The block
it writes into was freed by `OutputBuffer_Finish`'s `Py_DECREF(buffer->list)`
(`blockoutput.h:253`, reached from `_ppmdmodule.c:552`). So a **worker thread is
still alive and writing after the Python `decode` call freed the output block.**

### The exact causal chain (three compounding defects, all pyppmd-original)

1. **`#126` removed the input-empty stop** (`ThreadDecoder.c`, 1.3.0). In 1.2.0,
   `Ppmd7T_decode_run` broke out of the symbol loop when the input buffer was
   consumed:

   ```c
   // v1.2.0 src/lib/buffer/ThreadDecoder.c
   Bool inbuf_empty = reader->inBuffer->size == reader->inBuffer->pos;
   ...
   if (inbuf_empty && reader->inBuffer->size > 0) { break; }  // <-- REMOVED in 1.3.0
   ```

   With the whole pack fed in one `decode` call and `max_length` exceeding the
   payload, the 1.3.x worker no longer stops at input-empty; it keeps decoding
   (garbage) symbols until the range coder finally needs a byte, then **blocks in
   the reader** (`pthread_cond_wait(notEmpty)`, `ThreadDecoder.c:77`). The
   controller's `inempty` path (`ThreadDecoder.c:189`) returns 0 to Python
   **without joining the still-blocked worker**.

2. **`OutputBuffer_Finish` frees the output block while the worker holds a raw
   pointer into it.** Back in Python land, `Ppmd7Decoder_decode` breaks its loop
   on `result == 0` (`_ppmdmodule.c:530`), sets `needs_input`, and calls
   `OutputBuffer_Finish` (`:552`), whose `Py_DECREF(buffer->list)` frees the
   PyBytes blocks — including the one `out->dst` still points at. There is **no
   worker-quiescence step** before the free.

3. **The blocked worker is later resumed with no new input, and free-runs.** The
   only things that broadcast `notEmpty` are the next `decode` call or
   `Ppmd7T_Free` at teardown (`:198`). When woken, the reader
   (`Ppmd_thread_Reader`, `ThreadDecoder.c:81`) executes
   `return *((const Byte *)inBuffer->src + inBuffer->pos++)` with `pos == size`
   — an OOB read — advancing `pos` to `size+1`. Because the empty check is
   `pos == size` (`:70`) and **not** `pos >= size`, the reader **never blocks
   again**, so the worker free-runs: each iteration decodes a garbage symbol and
   writes it to the freed `out->dst` (`:134`) — thousands of UAF writes
   (valgrind counted 33 423 / 59 522 in 6-cycle runs) until the corrupted glibc
   metadata aborts, a wild read SIGSEGVs, or the deferred `pthread_cancel` lands.

### Why the controls are clean

Exact-sized `decode(packed, len(data))` is **0 valgrind errors**: the worker's
budget (`i < max_length`) runs out at exactly the payload boundary, the worker
returns `i`, sets `finished = True`, and is `pthread_join`-ed by the controller's
`finished` path (`ThreadDecoder.c:185`). No worker is ever left blocked, so
`Ppmd7T_Free` sees `finished` and does nothing, and `out->dst` is never written
after the free. This is precisely the measured safe/unsafe split (sized-safe 0%,
overshoot/oversized/extra-null high-rate).

### Where the Pavlov suballocator fits

The desynced model (`Ppmd7_DecodeSymbol` on a range coder past EOF) is what
*produces* the garbage symbols the worker then writes, and in principle its
suballocator (`InsertNode`/`GlueFreeBlocks` via `NODE(offs)`, `Ppmd7.c:120/145/55`)
can also compute out-of-`Base` offsets. But valgrind's **first** error is always
the output-buffer UAF in pyppmd's own `ThreadDecoder.c:134`, not a write inside
`Ppmd7.c`. So the corruption the earlier `pyppmd-upstream-report.md` attributed
to "walking the native model on a desynchronized range coder" is more precisely a
**pyppmd output-buffer lifetime bug**; the model walk is the garbage *source*, the
UAF is the corrupting *write*. Both are enabled by the same removed stop + oversized
budget.

---

## E. Ppmd8 parity (measured)

PPMd8 (var.I / RAR) **does** have a real end mark: `Ppmd8_DecodeSymbol` returns
`-1` at the EndMarker (`Ppmd8.h:124`), and `Ppmd8T_decode_run` translates it to
`PPMD_RESULT_EOF` (`ThreadDecoder.c:235`), which the module maps to `eof=True`.

Measured (this env, 1.3.1): an **unsized** PPMd8 decode of `b"a"*2000`,
`b"\x00"*2000`, and `"hello world "*100` terminates on the end mark with
`eof=True` and **overshoot = 0** in each case (drove `decode(b"", 4096)` to
completion; no fabricated trailing bytes). This confirms archivey's decision to
perform **no** post-eof drain for unsized PPMd8: the end mark flushes the final
symbols with no residual buffered output, so skipping the drain cannot truncate a
valid member (brief gap #8).

The `Ppmd8T_*` wrapper shares the Ppmd7 structure exactly — same removed
input-empty stop, same `INT_MAX` budget (`_ppmdmodule.c:1264`), same
`OutputBuffer_Finish` free, same `Ppmd8T_Free` fake-input-then-cancel (`:302`) —
so **overshooting past the end mark** (an oversized budget on truncated/corrupt
PPMd8, or empty drains past `eof` toward an inflated `unpack_size`) reproduces the
identical output-buffer UAF. The end mark only protects *valid* unsized streams
by giving the worker a clean stop; it does not protect against a caller that
requests more than the member contains.

---

## F. Answers to the brief's four questions (interim)

1. **Omit-null:** pyppmd's encoder omits nothing (5 unconditional flush bytes,
   `Ppmd7Enc.c:67`); the "extra byte" is a 7-Zip *reader* convention (return 0
   past EOF) that pyppmd's *blocking* reader does not implement, so the caller
   must feed `b"\0"`. Archivey must keep bounded NUL recovery for real-7-Zip
   members, but never with an `unpack_size` budget.
2. **`max_length`/`eof`:** `-1 → INT_MAX` (`:521`); `eof` is `Code == 0`
   (`Ppmd7.h:107`), a proxy that fires prematurely on compressible data under a
   small cap; retained-input semantics let a valid stream continue past that
   premature eof but let a truncated stream run the overshoot primitive.
3. **Corruption:** use-after-free of pyppmd's output block at
   `ThreadDecoder.c:134` (freed by `OutputBuffer_Finish`, `_ppmdmodule.c:552`),
   because the overshooting worker is left blocked-in-reader and later resumed to
   free-run (reader `pos==size` bug). Valgrind-confirmed as the first error in
   overshoot / oversized / after-eof families; the Pavlov suballocator supplies
   the garbage symbols but is not the first faulting write.
4. **Separability:** the whole crash family is **pyppmd-only** — it is a binding
   lifetime/threading bug (`ThreadDecoder.c` + `_ppmdmodule.c` + `blockoutput.h`),
   introduced by `#126` (the input-empty stop existed in 1.2.0, source-confirmed).
   The Pavlov core's memory-unsafety-past-EOF is real but latent: real 7-Zip
   decodes to the exact known unpack size and never trips it, and it is not the
   first corrupting write in pyppmd either.

---

## G. Empirical results (this env: pyppmd 1.3.1, CPython 3.11.15, glibc 2.39)

- [x] **Premature-eof:** `decode(b"a"*4096, 64)` → `out=64`, `eof=True`,
      `needs_input=False` after only 64 of 4096 bytes. `eof` here is the
      `Ppmd7z_RangeDec_IsFinishedOK` (`Code == 0`, `Ppmd7.h:107`) path at
      `_ppmdmodule.c:553` (not an end mark — PPMd7 has none). Ignoring it and
      continuing with `decode(b"", 64)` recovered all 4096 bytes over 63 calls
      (`match_full=True`). Confirms premature eof is a `Code == 0` proxy artefact,
      and that continued empty decode is valid *on a complete stream*.
- [x] **Corruption:** valgrind pins the first error to the output-buffer UAF at
      `ThreadDecoder.c:134` for `overshoot` (`-1`), `oversized`, and after-eof
      `extra-null`; exact-sized decode is 0 errors (Section D). gdb on the raw
      `overshoot` repro aborts with `corrupted size vs. prev_size` at
      `Ppmd7Decoder_dealloc` (`_ppmdmodule.c:222`) — i.e. corrupted metadata is
      only *detected* at teardown free, consistent with an earlier UAF.
      `scripts/pyppmd_crash_repro.py 20 --mode overshoot` → 16/20 crashes here.
- [x] **Omit-null / reader-blocks:** no `encode()+flush()` stream reports
      tail-`needs_input` (encoder omits nothing; `Ppmd7Enc.c:67` writes 5
      unconditional flush bytes). Flushed packs commonly end in `0x00` (that byte
      is load-bearing compressed data). The "extra byte" is the caller manually
      feeding the zero that 7-Zip's over-read-zero reader would synthesise, which
      pyppmd's blocking reader (`ThreadDecoder.c:70-81`) does not.
- [x] **Ppmd8 parity:** unsized decode terminates on the end mark with
      overshoot = 0 (Section E). Structural overshoot-past-end-mark shares the
      Ppmd7 UAF.
- [x] **Version delta (source-confirmed):** the input-empty stop
      `if (inbuf_empty && reader->inBuffer->size > 0) break;` is present in
      `v1.2.0:src/lib/buffer/ThreadDecoder.c` and **absent** in `v1.3.1`
      (removed by `#126`). 1.2.0's own trade-off bug: that same early break
      truncates chunked decodes that must keep producing without new input —
      which is what `#126` set out to fix, swapping a correctness bug for this
      memory-safety bug.

---

## H. Concrete upstream fix (refines `pyppmd-upstream-report.md`)

The prior report's four fixes still apply, but the root-cause finding sharpens
the priority: **the corrupting write is the output-buffer UAF, so the primary fix
is worker/output lifetime, not (only) budget clamping.**

1. **Never free the output block while a worker may still write to it
   (primary).** Before `OutputBuffer_Finish` / any `Py_DECREF(buffer->list)` in
   `Ppmd7Decoder_decode` (`_ppmdmodule.c:552`) and `Ppmd8Decoder_decode`
   (`:~1300`), ensure the worker is quiescent — either it `finished`, or it is
   parked in the reader wait with `out->dst` guaranteed not to be dereferenced
   until re-entry. Equivalently, do not hand the worker a raw pointer into a
   Python-owned block that the controller can free while the worker is only
   *paused*. This alone removes the UAF that valgrind flags.
2. **Restore a stop condition for input-exhausted decodes.** Re-add the 1.2.0
   input-empty break *without* reintroducing its chunked-truncation bug: stop the
   symbol loop when the range coder would need a byte that is not available
   (park), rather than spinning on stale range state. Bound the loop by what the
   input can actually support instead of `INT_MAX` (`_ppmdmodule.c:521/1264`).
3. **Fix the reader's empty check:** `pos >= size` (not `== size`) in
   `Ppmd_thread_Reader` (`ThreadDecoder.c:70`), so an overshoot of one cannot
   turn into an unbounded free-run.
4. **Signal termination explicitly in `Ppmd7T_Free`/`Ppmd8T_Free`.** Use a
   dedicated "terminate" flag the reader re-checks after wakeup instead of faking
   `tc->empty = False` with no data (`ThreadDecoder.c:198/307`); the reader must
   return/park on termination rather than reading `src[pos++]`.
5. **Port the cffi `_eof` guard to the C extension:** `decode` after `eof`
   returns `b""` without starting a worker (`_ppmdmodule.c:396`, add the early
   return the cffi backend already has).

Fixes 2–5 are defence-in-depth; **fix 1 is the one the valgrind evidence
demands**. Until a fixed release ships, archivey's discipline (never overshoot;
require `unpack_size`/`pack_size`; bounded single NUL; no post-eof drain unless
pack delivery is known complete) is the correct in-process mitigation, and the
`Ppmd7T_Free` residual remains the reason PPMd adversarial tests are
subprocess-isolated.

### Verification when a fixed release ships

Re-run all `scripts/pyppmd_crash_repro.py` modes (`extra-null`, `overshoot`,
`oversized`, `warmup-overshoot`) plus the valgrind driver of Section D; all must
report **0 memory errors** and 0 crashes, including under teardown (`del dec`).
