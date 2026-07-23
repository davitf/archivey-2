# PPMd / pyppmd native investigation — results

**Brief:** `docs/internal/ppmd-native-investigation-brief.md`
**Prior work:** `docs/internal/pyppmd-upstream-report.md`,
`docs/internal/ppmd-exit-after-green-exploration.md`,
`docs/internal/known-issues.md`, `scripts/pyppmd_crash_repro.py`
**Date started:** 2026-07-23
**Status:** live notebook — findings appended as experiments finish. Source
citations are against **pyppmd `v1.3.1`** (the pinned wheel; git tag `v1.3.1`,
commit `580b675`). The wheel installed in this env is `pyppmd 1.3.1`, CPython
3.11.15, glibc 2.39, Linux 6.18.5.

This report answers the brief from **native source + minimal repros**, not from
archivey's mitigations. Where the archivey labs already measured a behaviour, it
is cited and the *source-level cause* is added rather than re-measured.

---

## TL;DR verdict

| Failure mode | 7-Zip / Pavlov PPMd core | pyppmd ThreadDecoder / binding | Caller contract |
|--------------|--------------------------|--------------------------------|-----------------|
| overshoot `max_length` (`-1` or `+65536`) | Memory-unsafe **primitive**: decodes garbage symbols past true EOF, suballocator writes wild `p->Base + offset` → heap corruption. But never *invoked* this way in real 7-Zip (exact unpack size always known). | **Root trigger:** 1.3.0 `#126` removed the input-empty stop; `-1 → INT_MAX` budget (`_ppmdmodule.c:521`) drives the core past EOF. | Never request more output than the true remaining payload. |
| post-eof empty decode toward `unpack_size` | Same primitive once desynced. | No after-eof guard in the **C extension** `decode` (guard is cffi-only); restarts a runaway worker on finished range state. | Do not call `decode` after `eof` unless pack delivery is known complete. |
| half-pack + large NUL | Same primitive (large budget over truncated stream). | Same removed stop + INT_MAX budget. | Cap the synthetic-NUL budget; do not chase `unpack_size` on short pack. |
| `Ppmd7T_Free` while worker blocked | Not applicable (7-Zip is non-threaded here). | **pyppmd-only:** `Ppmd7T_Free` fakes "input available" (`tc->empty=False`) then cancels; reader does an OOB `src[pos++]` at `pos==size`. | n/a (teardown bug). |
| omit-trailing-null / "extra byte" | **Container/reader convention**, not an encoder action. | pyppmd's reader **blocks** at EOF instead of returning 0, so the caller must feed `b"\0"` to emulate 7-Zip's over-read-zero. | Keep bounded NUL recovery for members produced by real 7-Zip. |

Bottom line: the **memory-unsafety primitive lives in Igor Pavlov's vendored
PPMd7 model/suballocator** (`Ppmd7.c`), which is safe *only* when the caller
never decodes past the true payload length. **pyppmd 1.3.x's ThreadDecoder
rewrite (#126) is what actually drives the core past EOF** by removing the
input-exhausted stop condition and honouring an `INT_MAX`/oversized output
budget, with no after-eof guard in the C extension. So it is an **interaction**,
but the regression and the fix both belong to pyppmd; the core needs the caller
to respect the length contract it has always required.

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
unbounded. This is the mechanism behind `overshoot` and `oversized`
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

## D. Corruption mechanism (root cause function)

The heap-corruption abort (`malloc(): invalid size (unsorted)` / SIGABRT /
SIGSEGV, Windows `STATUS_HEAP_CORRUPTION 0xC0000374`) is a **wild write inside
the PPMd7 suballocator**, driven by decoding past true EOF.

The entire PPMd7 model lives in one allocation `p->Base` (`Ppmd7.c:96-113`), and
every node is addressed by a byte offset into it:

```c
#define REF(ptr)   ((UInt32)((Byte *)(ptr) - (p)->Base))   // Ppmd7.c:22
#define NODE(offs) ((CPpmd7_Node *)(p->Base + (offs)))       // Ppmd7.c:55
```

After the range coder desyncs (Section C), `Ppmd7_DecodeSymbol` returns garbage
symbols and calls `Ppmd7_Update1/Update2/UpdateBin` → `Ppmd7_Rescale`,
`CreateSuccessors`, `AllocContext` → the suballocator (`InsertNode` `Ppmd7.c:120`,
`RemoveNode` `:126`, `GlueFreeBlocks` `:145`, `AllocUnits` `:243`). With garbage
`NumStats` / `SummFreq` / `Successor` refs, these compute **offsets that fall
outside the `Base` allocation**, and `p->FreeList[indx] = REF(node)` /
`NODE(offs)->Next = ...` then write to `Base + wild_offset` — arbitrary heap
addresses, which corrupts glibc chunk metadata and aborts on the next `malloc`.

This is **Pavlov's code**, and it is memory-unsafe *by design assumption*: PPMd7
has no end mark and the decoder trusts that the caller stops at the exact
declared unpack size (7-Zip always knows it from the archive header, so 7-Zip
never runs the model past EOF). The bug is not that `Ppmd7.c` lacks bounds
checks — it never claimed to have them; the bug is that **pyppmd hands it an
`INT_MAX`/oversized budget and no input-empty stop**, so the model *is* run past
EOF through a documented Python API.

Secondary teardown hazard (`Ppmd7T_Free`, `ThreadDecoder.c:193`): when the
worker is blocked in the reader, `Free` sets `tc->empty = False` and broadcasts
`notEmpty` **before** `pthread_cancel`. The woken reader leaves its wait loop and
executes `return *((const Byte *)inBuffer->src + inBuffer->pos++)` with
`pos == size` — an **out-of-bounds read one past the input buffer** (whose
backing Python bytes may already be released), decoding one more garbage symbol
into a possibly-finished output block before deferred cancellation lands at the
next cancellation point. Compounded by the reader's empty check being
`pos == size` (`ThreadDecoder.c:70`) rather than `pos >= size`, so once `pos`
overshoots by one it never blocks again. This is the `Ppmd7T_Free` /
exit-after-green race, and it is **pyppmd-only** (7-Zip's non-threaded decoder
has no such teardown).

*(Backtrace attribution under a debug allocator is pending — Section G/task 4.)*

---

## E. Ppmd8 parity note (pending measurement)

PPMd8 (var.I / RAR) **does** have a real end mark: `Ppmd8_DecodeSymbol` returns
`-1` at the EndMarker (`Ppmd8.h:124`), and the same `ThreadDecoder.c`
`Ppmd8T_decode_run` translates it to `PPMD_RESULT_EOF`. So an unsized PPMd8
decode terminates on the end mark rather than needing an external size — which is
why archivey performs no post-eof drain for unsized PPMd8. Whether the same
overshoot / Free-race primitives reproduce on `Ppmd8Decoder` is still to be
measured (task 5); the `Ppmd8T_*` wrapper shares the exact structure of the
Ppmd7 one (same removed stop, same `INT_MAX` budget at `_ppmdmodule.c:1264`, same
`Free` fake-input-then-cancel at `:302`), so the primitives are expected to
carry over except that the end mark gives valid streams a clean stop.

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
3. **Corruption:** wild write in the PPMd7 suballocator (`Ppmd7.c` `InsertNode` /
   `GlueFreeBlocks` via `NODE(offs)` on garbage offsets) after the range coder
   desyncs from decoding past EOF — Pavlov's memory-unsafe-past-EOF core, invoked
   past EOF by pyppmd's unbounded ThreadDecoder budget.
4. **Separability:** the teardown OOB and the removed stop are **pyppmd-only**;
   the model wild-write is **shared** but never reached by real 7-Zip because
   7-Zip always decodes to the exact known size. Direct 7-Zip-API overshoot
   comparison is task E of the brief (see Section G plan).

---

## G. Empirical plan (in progress)

- [ ] Premature-eof repro: `decode(packed, 64)` on `b"a"*4096`, show `eof=True`
      with `Code == 0` while output ≪ payload; then `decode(b"", 64)` completes.
- [ ] Corruption backtrace: run `pyppmd_crash_repro.py --mode overshoot/oversized`
      and half-pack+large-NUL under `MALLOC_CHECK_=3` / glibc malloc debug +
      `faulthandler`, capture the faulting frame (expect `Ppmd7.c` suballocator).
- [ ] Reader-blocks vs feed-zero: show a tail-`needs_input` stream completes when
      fed `b"\0"` and stalls otherwise (emulating 7-Zip's over-read-zero).
- [ ] Ppmd8 parity: overshoot / post-eof / Free-race on `Ppmd8Decoder`; confirm
      end-mark makes unsized drains unnecessary.
- [ ] Version delta 1.2.0 vs 1.3.1 on the same overshoot (if a 1.2.0 wheel builds
      in-env), to nail the #126 regression window.

Findings and the concrete upstream-fix refinement will be appended as each runs.
