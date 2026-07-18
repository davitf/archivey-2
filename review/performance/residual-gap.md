# Residual ZIP gap after #136/#137 — attribution and next investigations

**Context.** #136 (solid lazy open, nested-`ArchiveStream` collapse, `readall`
join, extension-map cache) and #137 (verify fusion: STORED stack is now
`ArchiveStream → SlicingStream`) implemented the wrapper-side H2 candidates —
and ZIP read-all wall did not move (±2%, within noise, on the PRs' harness
runs and on an independent probe here). So the original H2 attribution
over-weighted the wrapper stack. This file records where the gap *actually*
is, measured at `main` @ `b9cdeac`, and what to investigate next.

> **Follow-up (this PR):** tracks 1 and 5 from the list below are implemented
> and verified — see `investigation-report.md`. Decode feed is now 64 KiB
> (scaled for large bounded reads); gate G3/G4/G5 are closed on the structural
> path. Tracks 2–4 conclusions are in that report (regime split explained;
> per-member and extract left open / product).

Fixture for all numbers: 64 × 256 KiB DEFLATE members, ~2:1-compressible
payload; medians of 21 warm in-process rounds. Repro: `attrib.py <section>`.

## Where the gap is (measured)

Component parity from paired cProfile (`attrib.py profile`) plus the feed-size
sweep (`attrib.py sweep`), per full read-all pass:

| Component | zipfile | archivey today | archivey at plateau |
|---|---:|---:|---:|
| `zlib.decompress` (C) | ~46 ms | ~46 ms total, but split across 17 calls/member | ~46 ms, ~1 call/member |
| `zlib.crc32` (C) | ~4 ms | ~4 ms (fused verifier) | ~4 ms |
| everything else (Python) | ~2 ms | **~20 ms** | **~12 ms** |
| **total** | **52 ms** | **72 ms (1.38×)** | **64 ms (1.23×)** |

Two distinct causes, in order:

### 1. Decode granularity (~8 ms of the gap; the actionable lever) — DONE

`_COMPRESSED_READ_SIZE` is now 64 KiB with scale-up for large bounded `read(n)`.
On the follow-up host the review probe measures ~1.37× with OS-read census at
parity with zipfile (196 vs 195). See `investigation-report.md` Track 1.

### 2. Distributed per-member machinery (~12 ms; ~190 µs/member; no single hotspot)

At the plateau, decompress and CRC are at parity and the rest is spread thin:
2–3 `ArchiveStream` constructions per member (public wrap + codec wrap),
`dataclasses.replace` (~2 calls/member), `_to_member`/`_local_data_region`,
readinto shims, `_wrap_member_stream` argument plumbing. cProfile shows no
item above ~2 ms/pass — this is death-by-a-thousand-cuts, and it is why
per-member cost dominates for *small*-member archives (the many-small regime)
while barely mattering for large members. **Follow-up:** profiled; no ≥5% safe
win found — deferred (Track 3).

## What to investigate next (priority order)

1. ~~**The decode-granularity lever**~~ **Done** (64 KiB feed + scaled bounded read).
2. ~~**Reconcile the harness's 2.0× with this probe's 1.38×.**~~ **Explained:**
   CI harness is 8×4 KiB (many-small → ~4×); probe/realistic are 256 KiB members
   (~1.4–1.8×). See investigation-report Track 2.
3. **Per-member fixed cost (many-small regime).** Still open — needs a dedicated
   change if Q1 treats many-small as in-budget.
4. **Extract-all residual (2.4–3.7×, H4).** Census confirms safety syscalls
   (mkstemp+rename, lstat tax). Realistic ~1.9× already in the ~2× band; no
   code change pending Q1.
5. **open+list 5–8× (H3 remainder).** Still open; ZIP open_list now has a
   stdlib peer in the harness so the ratio is visible.

## Methodology notes (hard-won on this host)

- **Never compare wall numbers across processes.** The same
  archivey read-all measured 71–94 ms across separate invocations (CPU
  frequency/cache state), while within one warmed process the spread was
  ±2 ms. Structure every A/B as one process, warm both sides first, interleave
  or mirror the order (`attrib.py sweep` does this), and distrust any
  conclusion the mirrored order doesn't reproduce.
- **Use component parity, not just totals.** Paired cProfile with the *same*
  fixture lets you check that the C-level work (decompress, crc32 tottime) is
  equal on both sides; only then does the "everything else" subtraction
  attribute the gap to interpreter overhead. If C time differs, the problem is
  work volume (chunking, re-decode), not dispatch.
- **Count calls, not just time.** The read()-call census at the OS boundary
  (1220 vs 195) found the granularity problem instantly and is immune to
  timer noise. `ncalls` ratios in cProfile (17 decompress calls/member vs 1)
  pinpoint loop granularity without any wall measurement.
- **Ablate by monkeypatch before designing.** The whole chunk-size conclusion
  came from one-line `ds._COMPRESSED_READ_SIZE = N` patches — establish the
  ceiling of a lever *before* writing the real fast path.
- **cProfile distorts Python-heavy code** (~20% overhead here, concentrated in
  exactly the layers under investigation). Use it for attribution shape, and
  confirm any win with plain `perf_counter` medians; for C-level or syscall
  costs use sampling (`py-spy record --native`, `perf`) or `strace -c`.
