# Investigative brief: PPMd / pyppmd truncation, NUL quirk, and heap corruption

**Audience:** an investigative agent (or human) diving into **native PPMd source**
and **pyppmd bindings**, not archivey API design.  
**Date:** 2026-07-23  
**Repo context:** `archivey-2` PR #188 / branch work around
`docs/internal/ppmd-exit-after-green-exploration.md` and
`docs/internal/pyppmd-upstream-report.md`.  
**Pinned wheel in labs:** `pyppmd==1.3.1` (C extension `pyppmd.c._ppmd`).

---

## Mission

Answer these questions with evidence from **source code + minimal repros**, not
from archivey’s mitigations alone:

1. **Compression / bitstream end:** How does PPMd7 (var.H) finalize a stream?
   What does pyppmd’s `Ppmd7Encoder.flush()` emit? When does the README claim
   “encoder omits a last null (`b"\0"`)” apply in the C code? Is that omit a
   7-Zip/PPMd algorithm property or a pyppmd encoder quirk?

2. **Decompression / native decoder:** What does `Ppmd7Decoder.decode(data, max_length)`
   do when:
   - `max_length` is less than the symbols still coded in the remaining input?
   - input is exhausted but more symbols are requested?
   - a synthetic `b"\0"` is fed after the flushed bitstream?
   - native `eof` / `needs_input` flip (especially **premature `eof=True`** after
     a small `max_length` on highly compressible data)?

3. **Heap corruption:** Exact mechanism on pyppmd 1.3.x when the output request
   overshoots true remaining payload (or when empty-decoding past true EOF toward
   a large `unpack_size`). Is the bug in:
   - **Igor Pavlov / 7-Zip PPMd** reference code (range coder / model),
   - **pyppmd’s ThreadDecoder / worker-thread wrapper** (especially the 1.3.0
     rewrite, upstream PR miurahr/pyppmd#126),
   - or **interaction** (binding asks the native core to do something 7-Zip never
     does in-process)?

4. **Separability:** Does the same overshoot crash (or UB) show up when driving
   **7-Zip / p7zip / py7zr’s native paths** without pyppmd’s thread decoder? Or
   only via `pyppmd.c._ppmd`?

Deliver a short written report (can append to this doc or
`docs/internal/ppmd-exit-after-green-exploration.md`) with: file/line citations,
minimal C or Python repros, and a clear “7-Zip vs pyppmd” verdict per failure mode.

---

## Background the agent must not re-discover from scratch

### Archivey position (mitigations already shipped — do not redo)

Archivey is a Python archive library. PPMd7 is used for 7z var.H (via `pyppmd`).
Relevant adapter: `src/archivey/internal/streams/decompress.py` (`PpmdDecoder`).

Already in place after PR #188 labs:

- Never pass `max_length=-1` to PPMd7; require `unpack_size`.
- Extra-NUL recovery: **at most one** `decode(b"\0", …)` with per-call cap
  `_PPMD_EXTRA_NUL_MAX_OUTPUT = 64`.
- Optional **`pack_size`**: track fed compressed bytes; refuse post-`eof` empty
  drains that chase `unpack_size` when `fed < pack_size` (stops near-EOF
  `MemoryError` on ~95% pack cuts). When pack is fully delivered, chunked
  `decode(b"", 64)` drains may finish past premature native `eof`.
- Adversarial unfinished-decoder tests run in subprocesses (`Ppmd7T_Free` race).

These are **workarounds**. The investigation should explain *why* they are needed
and what a correct upstream fix would be.

### Prior archivey write-ups (read these)

| Doc | Contents |
|-----|----------|
| `docs/internal/pyppmd-upstream-report.md` | Ready-to-file upstream issue draft; 1.3.0 `ThreadDecoder.c` rewrite (#126); overshoot / `-1` / after-eof crash tables; suggested C-level fixes |
| `docs/internal/ppmd-exit-after-green-exploration.md` | Exit-after-green lab notebook: truncated NUL flush, A/B natives, trailing `0x00`, last-byte isolation, chunked drains, pack-size gate |
| `docs/internal/known-issues.md` | “Intermittent pyppmd native aborts” + “exit-after-green abort” sections |
| `scripts/pyppmd_crash_repro.py` | Self-contained repro (`pyppmd` + stdlib): modes `extra-null`, `overshoot`, `oversized`, `sized-safe`, … |

### Upstream / reference trees to clone

```bash
# pyppmd (bindings + vendored PPMd C)
git clone https://github.com/miurahr/pyppmd.git /tmp/pyppmd
# Known regression window: 1.3.0 ThreadDecoder rewrite
#   https://github.com/miurahr/pyppmd/pull/126

# 7-Zip / PPMd reference (for “is this Pavlov’s code?”)
# pyppmd historically derived from p7zip / 7zip PPMd; check LicenseNotices +
# which C files are vendored under pyppmd’s native tree (Ppmd7*, Ppmd8*,
# ThreadDecoder*, RangeCoder*, etc.).
```

Also useful: installed wheel sources under the env’s
`site-packages/pyppmd/` (Python API) plus the `.so` — but **C sources in the
git tree** are authoritative for behavior.

py7zr’s wrapper (for comparison of *call* patterns, not the crash itself):

```text
py7zr.compressor.PpmdDecompressor.decompress:
  if len(data) == 0 and decoder.needs_input:
      return decoder.decode(b"\0", max_length)
  return decoder.decode(data, max_length)
```

pyppmd README “Extra input byte”:

> PPMd algorithm … Extra input byte. The encoder will omit a last null (`b"\0"`)
> when last byte is `b"\0"`. You may need to provide an extra null byte when you
> don't get expected size …

Fuzzer pattern (`tests/test_fuzzer.py` in pyppmd): after `encode+flush`, if
`len(result) < length` and `needs_input`, `decode(b"\0", remaining)`.

---

## Hard facts already measured (pyppmd 1.3.1, Ppmd7 only)

Treat these as constraints; confirm or refute with source, do not ignore.

### Crash / corruption shapes

| Shape | Result (approx.) |
|-------|------------------|
| `decode(packed, len)` only (exact remaining) | Clean |
| `decode(packed, -1)` or `len+65536` overshoot | High-rate SIGSEGV / malloc abort |
| Half pack, then `decode(b"\0", remaining≈thousands)` | **~85/100** abort (archivey flush bug shape) |
| Same with NUL `max_length≤64` | **0/100** abort |
| Near-complete pack cut (~95%), then empty drains toward full unpack while ignoring premature `eof` | **MemoryError / malloc** (even with 64-byte chunks); archivey stream did this before pack-size gate |
| Mid-truncation (~25–90%), one NUL@64, stop if `needs_input` again | Clean, ~1 byte from NUL |
| Unfinished decoder + `Ppmd7T_Free` while worker blocked | Low-rate exit-after-green / teardown abort |

Version note from upstream report: **1.1.1 / 1.2.0** do not show this crash family
the same way (different bugs); **1.3.0+** after ThreadDecoder rewrite does.
That strongly biases toward **pyppmd threading/binding**, but verify against
raw 7-Zip PPMd if possible.

### “Extra NUL” / trailing `0x00` (often misunderstood)

| Observation | Implication |
|-------------|-------------|
| Exact fuzzer recovery after `encode+flush`: **0/60k** needs_input+NUL hits on 1.3.1 | Docs path is rare or absent with proper flush on this wheel |
| ~70% of flushed streams **end with compressed `0x00`** (rand ~100%) | Trailing zero is often **load-bearing bitstream**, not padding |
| Strip present trailing `0x00` → usually `eof` + **short** output, not successful NUL recovery | Stripping zeros corrupts; ≠ README “omit” case |
| Last compressed byte alone can emit **hundreds–thousands** of symbols (`a*16384` → last_out≈2594; `HDR+Z*4096` → ≈1639) | One input byte ↔ many output symbols |
| Replace real last non-zero byte with synthetic NUL → `extra≈1`, mismatch | Synthetic NUL ≠ “replay last byte” |
| `decode(full_packed, 64)` then `decode(b"", 64)` **ignoring premature eof** → can recover full `a*1024` correctly | Premature `eof` + continued empty decode is a real binding/core quirk; needed for completeness on some streams |
| Same ignore-eof empty drains on **truncated** pack toward large unpack → heap death | Completeness path and crash path share the same API surface |

### Theoretical bridge still open

If README “omit trailing null when last byte would be `0x00`” means flush dropped a
final compressed `0x00` that would have unlocked a long run, then a legitimate
recovery NUL might need to emit **>>64** symbols. We never observed that after
`flush()` on 1.3.1. **Confirm in encoder C whether omit happens, when, and what
the decoder expects.**

---

## Investigation plan (suggested order)

### A. Map the code

1. Locate vendored PPMd7 encode/decode + range coder in pyppmd’s native tree.
2. Locate **ThreadDecoder** / worker thread (1.3.0 #126): how `max_length`, input
   buffers, `eof`, `needs_input`, and teardown (`Ppmd7T_Free`) interact.
3. Diff 1.2.x → 1.3.0 for the “input empty / stop” condition called out in
   `pyppmd-upstream-report.md`.
4. Identify which files are untouched Pavlov/7-Zip vs pyppmd-original.

### B. Encoder / “omit last null”

1. Trace `Ppmd7Encoder.encode` + `flush` (and any `endmark` flag).
2. Find the code path that skips writing a trailing `0x00`.
3. Produce a **minimal bitstream** where flush omits the null and decoder
   requires `decode(b"\0", n)` to finish — or prove the README is stale for 1.3.1.
4. Compare with 7-Zip’s own PPMd7 writer if available (does 7z.exe omit the same way?).

### C. Decoder semantics of `max_length` and `eof`

1. When `max_length` caps output mid-symbol-run from already-consumed input, is
   remaining coding capacity **preserved** for `decode(b"", …)` or **discarded**?
   Labs suggest: often `eof=True` after a small cap, yet empty decodes can still
   emit more (correct on complete streams, garbage/crash on truncated).
2. Document the intended state machine: `needs_input` / `eof` / retained input.
3. Explain why highly compressible payloads flip `eof` after the first 64-byte
   request even though `unpack_size` is much larger.

### D. Corruption mechanism

1. With ASAN/ASan or `PYTHONMALLOC=debug` / `MALLOC_CHECK_=3`, catch the first
   bad access on:
   - `decode(b"\0", large_rem)` after half pack;
   - ignore-eof empty drains after ~95% pack toward full unpack.
2. Attribute the faulting code to ThreadDecoder vs Ppmd7 core vs allocator metadata
   from a prior overflow.
3. Check whether overshoot writes past an output buffer sized to `max_length`,
   or corrupts the PPMd model/context heap, or races the worker thread.

### E. 7-Zip vs pyppmd verdict

Minimum bar for a solid answer:

- **Same overshoot** against stock 7-Zip PPMd decode API (or p7zip) with an
  intentionally large output request after short input — crash or defined error?
- **Same overshoot** against pyppmd 1.2.x vs 1.3.1.
- If only 1.3.x+pyppmd crashes: binding/thread bug. If 7-Zip also UB: algorithm
  contract issue (callers must never overshoot) and pyppmd still needs hardening.

### F. `Ppmd7T_Free` race (secondary)

When a decoder is destroyed while the worker still waits on input: confirm the
race in ThreadDecoder teardown; note whether 7-Zip’s non-threaded use has the
same issue (likely not).

---

## Repro commands (start here)

```bash
# Archivey env already has pyppmd 1.3.1
cd /path/to/archivey-2

# Classic crash families (subprocess children)
uv run --no-sync python scripts/pyppmd_crash_repro.py 30 --mode extra-null
uv run --no-sync python scripts/pyppmd_crash_repro.py 30 --mode overshoot
uv run --no-sync python scripts/pyppmd_crash_repro.py 20 --mode oversized
uv run --no-sync python scripts/pyppmd_crash_repro.py 20 --mode sized-safe

# Half-pack then large NUL (exit-after-green / flush shape)
# (inline or extend crash_repro — see exploration doc E4)

# Clone upstream for reading
git clone https://github.com/miurahr/pyppmd.git /tmp/pyppmd
rg -n "needs_input|eof|max_length|ThreadDecoder|Ppmd7" /tmp/pyppmd -g '*.c' -g '*.h' -g '*.py' | head
```

Bare Python sketch for last-byte / premature-eof (spawn-isolate if aborting):

```python
import pyppmd
enc = pyppmd.Ppmd7Encoder(6, 1 << 20)
payload = b"a" * 1024
packed = enc.encode(payload) + enc.flush()
d = pyppmd.Ppmd7Decoder(6, 1 << 20)
out = d.decode(packed, 64)
print(len(out), d.eof, d.needs_input)
# then decode(b"", 64) in a loop to unpack_size — complete vs trunc pack
```

---

## Gaps to close in this investigation (review addendum)

Re-center deliverables on **clean teardown**, not only overshoot:

1. **Deterministic dispose:** Can a blocked-on-input worker be joined/cancelled
   from `PpmdDecoder` / `DecompressorStream.close()` so `Ppmd7T_Free` never races
   GC? Spike: explicit `del self._decomp` / native close before return from a
   truncated-flush child — does the ~15% SIGSEGV after `ok` disappear?
2. **Archivey lifecycle vs upstream:** If dispose removes the segfault, document
   the call sequence archivey must use; if not, the fix is upstream ThreadDecoder.
3. **PPMd8 parity:** Reproduce overshoot, post-eof empty drains, and Free-race
   teardown on `Ppmd8Decoder` (archivey’s unsized PPMd8 path uses the same empty
   loop). Hard facts above are Ppmd7-only today.
4. **Interpreter / GIL mode:** Failures cluster on 3.12+ and Windows; free-threaded
   CI exists — does race rate track GC/GIL mode?
5. **Deterministic seed:** Find order/mem/content that triggers teardown abort
   reliably so archivey can write a red–green test instead of a probabilistic soak.

### py7zr note (do not copy a myth)

py7zr decodes PPMd **in-process** via the same pyppmd binding
(`PpmdDecompressor.decompress` → `decode(b"\0", max_length)`). It is not
process-isolated and is not crash-immune; it typically passes full-remaining
`max_length` on generally-complete archives and lacks archivey’s hostile
truncation surface. Real process isolation remains Option D in the exploration
doc (opt-in / CLI worker), not a v1 library default.

A written answer that states, with citations:

1. **Omit-null:** exact encoder condition; bitstream examples; whether archivey
   must keep NUL recovery for real 7z members.
2. **`max_length` / `eof`:** precise semantics; why premature eof happens; when
   empty decode is valid vs UB.
3. **Corruption:** root cause function + why large `max_length` or post-eof empty
   loops destroy the heap; whether ASAN points at ThreadDecoder or Ppmd7 core.
4. **Verdict table:**

   | Failure mode | 7-Zip/PPMd core | pyppmd ThreadDecoder | Caller contract |
   |--------------|-----------------|----------------------|-----------------|
   | overshoot `max_length` | ? | ? | ? |
   | post-eof empty toward unpack | ? | ? | ? |
   | half-pack + large NUL | ? | ? | ? |
   | `Ppmd7T_Free` while blocked | ? | ? | ? |

5. **Recommended upstream fix** (concrete): restore stop condition, clamp output,
   reject overshoot, fix teardown — aligned with or refining
   `pyppmd-upstream-report.md`.

Out of scope unless needed for the verdict: changing archivey’s public API,
re-running full CI soaks, or implementing further mitigations (pack_size gate
already landed).

---

## Prompt (copy-paste for the investigative agent)

```text
You are investigating native PPMd / pyppmd heap corruption and bitstream-end
behavior for the archivey project. Read and follow the investigative brief at:

  docs/internal/ppmd-native-investigation-brief.md

Also read:
  docs/internal/pyppmd-upstream-report.md
  docs/internal/ppmd-exit-after-green-exploration.md
  (relevant sections of) docs/internal/known-issues.md
  scripts/pyppmd_crash_repro.py

Clone miurahr/pyppmd and study the C sources (especially ThreadDecoder and
Ppmd7 encode/decode). Determine whether the omit-trailing-null quirk, premature
eof under small max_length, and heap corruption on overshoot / post-eof empty
drains are properties of Igor Pavlov’s PPMd, of pyppmd’s 1.3.x thread wrapper
(PR #126), or both.

Produce a written report with file/line citations, minimal repros, a
7-Zip-vs-pyppmd verdict table, and a concrete upstream fix recommendation.
Do not spend time redesigning archivey’s Python API; mitigations are already
documented in the brief.
```
