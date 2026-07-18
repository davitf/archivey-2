# Residual ZIP gap after #136/#137 — attribution and next investigations

**Context.** #136 (solid lazy open, nested-`ArchiveStream` collapse, `readall`
join, extension-map cache) and #137 (verify fusion: STORED stack is now
`ArchiveStream → SlicingStream`) implemented the wrapper-side H2 candidates —
and ZIP read-all wall did not move (±2%, within noise, on the PRs' harness
runs and on an independent probe here). So the original H2 attribution
over-weighted the wrapper stack. This file records where the gap *actually*
is, measured at `main` @ `b9cdeac`, and what to investigate next.

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

### 1. Decode granularity (~8 ms of the gap; the actionable lever)

`_COMPRESSED_READ_SIZE = 8192` (`decompressor_stream.py:118`) feeds ~8 KiB
compressed slices into the decoder, so a 256 KiB member takes ~17 trips through
the 5-frame Python loop (`ArchiveStream.read` → verifier → `DecompressorStream.
read` → `_read_decompressed_chunk` → `Decoder.feed`), each staging output
through the shared `bytearray`. `zipfile.read()` reads the member's *entire*
compressed extent and decompresses it in **one** C call. The call census
(`attrib.py census`) shows the same story at the OS boundary: 1220 buffered
`read()` calls per pass vs zipfile's 195.

Sweep (warm, single process, mirrored order):

| compressed feed | wall | ratio vs zipfile |
|---:|---:|---:|
| 8192 (today) | 71.6 ms | 1.38× |
| 65536 | 65.2 ms | 1.25× |
| 262144 | 64.1 ms | 1.23× |
| 1 MiB | 64.9 ms | 1.25× |

The curve is flat past 64 KiB because the fixture's compressed members are
~130 KiB — one or two feeds already reaches single-shot. **A feed of 64 KiB, or
better a known-size fast path (member's `compress_size` is known for ZIP; read
exactly that, decompress with `max_length=declared_size`), puts ZIP read-all
under the 1.3× budget on this host.**

Safety constraints to preserve (why 8 KiB was chosen — see the comment at
`decompressor_stream.py:114-117`):

- Output amplification stays bounded regardless of feed size as long as
  `max_length` keeps being passed — the bomb-tracker guarantees live on the
  *output* side. The feed size only raises peak *compressed* buffer residency
  (64 KiB–1 MiB is trivially fine; the #128-style `read(1)` bound should be
  re-checked with the RSS probe in `measurements.py rss` after any change).
- A single-shot path must not regress `read(small_n)` consumers — gate it on
  `n < 0 or n >= remaining` with an empty buffer, mirroring the (currently
  unreachable, see below) `readall` fast path.

Note: the fused verifier turns a caller's `read(-1)` into a *bounded*
`read(declared_size)` (`verify.py` `MemberVerifier.read`), so
`DecompressorStream.readall()` — where #136 put the join-of-chunks fast path —
is **never reached** on the default (verified) path. Any fast path must live on
the bounded-`read(n)` branch to matter.

### 2. Distributed per-member machinery (~12 ms; ~190 µs/member; no single hotspot)

At the plateau, decompress and CRC are at parity and the rest is spread thin:
2–3 `ArchiveStream` constructions per member (public wrap + codec wrap),
`dataclasses.replace` (~2 calls/member), `_to_member`/`_local_data_region`,
readinto shims, `_wrap_member_stream` argument plumbing. cProfile shows no
item above ~2 ms/pass — this is death-by-a-thousand-cuts, and it is why
per-member cost dominates for *small*-member archives (the many-small regime)
while barely mattering for large members.

## What to investigate next (priority order)

1. **The decode-granularity lever (P2's main remaining lever).** Options, in
   increasing ambition: raise `_COMPRESSED_READ_SIZE` to 64 KiB; scale the feed
   to the remaining `max_length` request; single-shot fast path when the
   compressed extent is known (ZIP always knows `compress_size`). Re-run
   `attrib.py bench`, the `measurements.py rss` bound, and the many-small
   penalty case (`measurements.py accel`) as accept criteria. Expected: ZIP
   read-all ≤ 1.25×, TAR.gz accel-off similarly helped (same stream class).
2. **Reconcile the harness's 2.0× with this probe's 1.38×.** Same operation,
   different fixture/loop — the harness `zip_read_all` ratio should be
   decomposable into (per-member overhead × member count + per-byte overhead ×
   bytes). Fit those two coefficients by sweeping member size at constant total
   bytes (4 KiB / 64 KiB / 256 KiB / 1 MiB members); then the harness ratio for
   any fixture is predictable, and "which regime is the budget about?" (Q1)
   gets an empirical basis.
3. **Per-member fixed cost (many-small regime).** Attack only after (2)
   quantifies its real-world weight. Candidates: fold the codec
   `ArchiveStream` wrap away when the member wrap immediately collapses it
   (construct once, not construct-then-steal), find and batch the
   `dataclasses.replace` call sites, lazy `_to_member` field work. Use paired
   cProfile diffs per candidate; reject any that doesn't move the
   1000×4 KiB fixture by ≥5%.
4. **Extract-all residual (2.4–3.7×, H4).** Untouched by #136/#137. The known
   costs are filesystem safety (~5 `lstat`s/member, `mkstemp`+rename,
   `pathlib.resolve`). Measure with `strace -c` / `syscall` census rather than
   cProfile (the cost is syscalls, not Python), and compare against
   `shutil.unpack_archive` as the honest peer. Decide deliberately what the
   safety floor is (VISION's ~2× band exists for exactly this path).
5. **open+list 5–8× (H3 remainder).** Extension-map cache landed; the rest is
   detection sniff + member-model build (~45 µs/member). Only worth attacking
   if the million-archive sweep is a real workload (Q1); the `format=` idiom
   already bypasses most of it.

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
