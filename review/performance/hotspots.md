# Hotspots — attributed, with counter evidence

Ordered by VISION stakes. Bytes/seeks from `ByteCounter`/`SeekCounter` via
`enable_measurement()` / CLI `--track-io`; wall attributions from cProfile.
Repro: `measurements.py <section>`.

## H1 — Selective extraction from a solid 7z decodes ~the whole folder (blocker)

The VISION-named trap, reachable from the shipped CLI. Fixture: solid 7z,
one folder, 32 × 64 KiB (2 MiB unpacked). Measured (`measurements.py solid`):

| Access pattern | bytes_decompressed | × needed |
|---|---:|---:|
| `reader.read("m000.bin")` (first member, random access) | 65,536 | 1.0 |
| `stream_members(selector: first only)` | 2,031,617 | **31.0** |
| `extract_all(members: first only)` | 2,031,617 | **31.0** |
| CLI `archivey extract c.7z m000.bin` (`--track-io`) | 2,031,617 | **31.0** |
| `stream_members()`, manual `break` after 1st | 65,536 | 1.0 |

2,031,617 = whole folder minus the last member's body (2,097,152 − 65,536 + 1
EOF-probe byte): the pass decodes everything *except* data nobody ever asks for.

**Attribution.** Two stacking causes:

1. `SevenZipReader._iter_with_data` positions each member *at yield time* —
   `_member_stream_from_solid` calls `solid.open_member(prefix, size)` for every
   member before the consumer has said whether it wants it
   (`sevenzip_reader.py:316,606-615`). Advancing to member *i*+1's prefix
   skip-decodes member *i*'s unread body. The base-class contract explicitly makes
   yielded streams lazy so "a consumer that skips a member … pays nothing for it"
   (`base_reader.py:435-439`) — the 7z override breaks that promise for the
   *positioning* cost, which on solid archives is the whole cost.
2. Extraction (and `stream_members` selector filtering) drains the full generator
   even after the selection is exhausted (`extraction.py:340`,
   `base_reader.py:1238`); the manual-`break` row shows early exit alone already
   fixes the "only early members selected" case.

**Fix shape.** Make solid positioning lazy: defer `solid.open_member` into the
member's lazy stream (first-read time), so iterated-past members cost nothing
until a *later* member is actually read (at which point the skip decode is
genuinely required). Extraction early-exit on exhausted explicit selections is a
complementary cheap win. Neither changes decode-once for full sweeps.

**Gate tie-in.** No harness case covers "selective read from solid" — worth adding
with a `bytes_decompressed ≤ prefix+member+slack` bound once fixed
(`gate-efficacy.md` G7).

## H2 — ZIP member-stream layering ≈ doubles decode cost (blocker, with P2)

Evidence in `budget-table.md` (§ZIP read-all): per 256 KiB member, raw
`zlib.decompress` is ~10 ms/run while the surrounding Python machinery adds
~16 ms/run: `DecompressorStream`'s chunked read loop with `bytearray` staging plus
a full `bytes(buffer[:n])` copy per read (`decompressor_stream.py:360-376`),
`VerifyingStream.read` (`verify.py:219`), and 2–3 nested `ArchiveStream` wrappers
(`archive_stream.py:256` — the chain for a gzip member is
`ArchiveStream > ArchiveStream > [ArchiveStream >] codec`, see
`measurements.py accel` chain dump). Concrete candidates, in impact order:

1. `readall()` fast path: when `n == -1`/`n ≥ remaining` and the buffer is empty,
   return `b"".join(chunks)` instead of staging through the shared `bytearray` and
   re-slicing (kills both `extend` and the copy).
2. Collapse the double `ArchiveStream` wrap on the `_lazy_member_stream` →
   `_open_member` → `_wrap_member_stream` path (the outer lazy shim re-wraps an
   already-wrapped `ArchiveStream`).
3. Larger `_COMPRESSED_READ_SIZE`-driven output chunks for whole-member reads.

Not proposed: touching the safety semantics (verification, translation, leases).

## H3 — Per-`open_archive()` overhead: 0.3 ms/small archive (follow-up)

`budget-table.md` §open+list. Detection (~0.2 ms: magic sniff + extension-map
assembly per open) + per-member `ArchiveMember` build/registration/limit
accounting (~45 µs/member). Million-archive sweep cost: ~5 min vs zipfile ~40 s.
Candidates: cache the registry's extension map (it is rebuilt per `open_archive`
call path today), defer the bidi-controls name scan to first display, and let
`format=` skip the sniff entirely (already supported — worth documenting as the
sweep-mode idiom). Retained memory is fine (~25 KiB/reader).

## H4 — Extraction filesystem safety machinery (attribute, mostly keep)

`budget-table.md` §extract-all: ~5 `lstat`/member, `mkstemp`+rename,
`pathlib.resolve` per member ≈ half the extract gap vs `extractall`. This is the
justified-≤2× category (atomic writes + path safety stdlib doesn't do), but it
only fits the band after H2 shrinks the stream half. One cheap item: `resolve()`
and repeated `lexists` per member could reuse the parent-directory resolution
across members of the same directory (extraction is depth-grouped already).

## H5 — rapidgzip AUTO threshold: safe, but leaves seek-wins on the table (tuning)

`measurements.py accel`, same input both sides of the 1 MiB compressed threshold
(semi-compressible ~2:1 payload, `MemberStreams.SEEKABLE` declared):

| Input | Workload | AUTO | forced ON | forced OFF |
|---|---|---:|---:|---:|
| 0.87 MiB comp (below) | sequential | 5.8–6.9 ms | 6.3–7.1 ms | 5.7–6.9 ms |
| 0.87 MiB comp (below) | seek+reread | 10.3–11.8 ms | **7.1–7.6 ms** | 10.6–11.5 ms |
| 1.11 MiB comp (above) | sequential | 8.9–9.0 ms | 8.6–8.7 ms | 8.6–8.7 ms |
| 1.11 MiB comp (above) | seek+reread | **9.0–9.4 ms** | 8.9–9.6 ms | 14.8–15.0 ms |

- **No pessimization:** at and above the threshold, sequential is parity and the
  seek workload wins ~1.6× — the crossover is real, AUTO engages correctly
  (decoder chain verified: `rapidgzip.RapidgzipFile` under AUTO above, stdlib
  `GzipFile` under OFF/below).
- **Conservative:** just *below* the threshold the seek workload would win
  ~1.5× (7.4 vs 11 ms) but AUTO stays on stdlib. The provenance script
  (`scripts/bench_rapidgzip_auto_threshold.py`) only ever produced compressed
  sizes ≤ ~30 KiB (its SIZES are *uncompressed* and its payload compresses
  ~340:1), so the 1 MiB *compressed* figure was an extrapolation; this-host
  crossovers land at 1.6–30 KiB compressed. A ~256 KiB threshold looks safe for
  seekable single-stream inputs — but only with a re-run of the many-small case,
  which is the real reason the gate exists:
- **The gate's purpose is confirmed:** 1000 × 4 KiB deflate ZIP read-all — AUTO
  111–114 ms ≡ OFF 109 ms; forced ON 591–621 ms (**5.2–5.6× penalty**,
  per-member C++ worker setup). AUTO leaving small members on stdlib is correct.
- **#128/F3 bounded:** `read(1)` on a 7.9 MiB-compressed accelerated gzip:
  peak-RSS delta ≈ 0 MiB; 64 MiB mid-stream seek also bounded
  (`measurements.py rss`).

## Measurement blind spots (record with H-fixes)

- 7z `_password_for_folder` confirm pass decodes whole folders outside
  `_track_decompressed` (`sevenzip_reader.py:550-573`) — encrypted-solid
  benchmarks would under-count.
- RAR bytes are `unrar p` pipe output: solid rewind inside unrar is invisible;
  RAR "decode-once" is unfalsifiable with the current counter
  (`test_measurement.py:166-169`).
