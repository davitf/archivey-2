# Benchmark gate — sample results

Re-run with:

```bash
# PR structural gate (small fixtures, fast)
uv run --extra all python -m benchmarks.harness --mode structural --scale ci

# Realistic wall-time vs stdlib (multi-MiB corpora; interleaved medians)
uv run --extra all python -m benchmarks.harness --mode full --scale realistic
```

## Investigation: ZIP “faster than stdlib” and TAR slowdown

### ZIP — yes, caching / methodology artifact

We wrap stdlib `zipfile`, so beating it in steady state is not plausible.

Early harness runs reported ~0.82× because wall timing ran **with measurement
wrappers on** (different ZIP open path: pre-opened `SeekCountingStream` file
obj) and mixed that with log-spam / single-pass noise on ~10 ms samples.

After fixing methodology — **unmeasured** archivey vs stdlib, interleaved
medians, diagnostic logs silenced during the timed window:

| Side | median |
| --- | ---: |
| `zipfile` read-all | ~13.4 ms |
| archivey read-all | ~15.8 ms |
| **ratio** | **~1.18×** |

cProfile shows the same `zlib.decompress` / CRC work on both archivey paths;
we are not winning at decompression — just paying thin wrapper overhead.

### TAR — plain uncompressed, not our codec streams

Harness `tar_read_all` is **raw TAR** (`tarfile` `r:`), not `.tar.gz`. The
slowdown is **not** “we decompress outside tarfile.”

| Case | archivey | peer | ratio |
| --- | ---: | ---: | ---: |
| plain TAR read-all | ~3.6 ms | `tarfile` `r:` ~2.1 ms | **~1.7×** |
| plain TAR list only | ~1.4 ms | `getmembers` ~1.0 ms | ~1.5× |
| already-open member I/O | ~2.0 ms | `extractfile`+read ~1.0 ms | **~2.0×** |
| `.tar.gz` (ad-hoc) | ~21 ms | `tarfile` `r:gz` ~18 ms | **~1.19×** |

So:

- **Plain TAR** pays per-member archivey overhead (`ArchiveMember`,
  `ArchiveStream`, reader-state). Absolute times are tiny; the ratio looks
  loud because stdlib is ~2 ms.
- **`.tar.gz`** via our outer codec is only ~1.2× native `r:gz` — the
  compressed-stream design is **not** the main TAR story vs VISION.

## Realistic scale (corrected harness)

**Corpus:** 64×256 KiB ZIP/TAR (~16 MiB), 32 MiB gzip, solid 7z 64×256 KiB.

### Wall-time vs stdlib (fair)

| Case | ratio | ay | stdlib | vs VISION 1.3× |
| --- | ---: | ---: | ---: | --- |
| zip_read_all | **1.18×** | 15.8 ms | 13.4 ms | within |
| tar_read_all | **1.70×** | 3.6 ms | 2.1 ms | above 1.3×, under 2× safety |
| gzip_read_all | **1.02×** | 31.7 ms | 31.2 ms | within |

### `.tar.gz` / `.tar.bz2` accelerators (off vs on)

Same ~16 MiB multi-member corpus. Stdlib peer is `tarfile` `r:gz` / `r:bz2`
(always stdlib codecs). Archivey forces `use_rapidgzip` /
`use_indexed_bzip2` ON or OFF; ON also sets `MemberStreams.SEEKABLE`.

The bzip2 accelerator is **`rapidgzip.IndexedBzip2File`** (via
`use_indexed_bzip2`), not the separate `indexed_bzip2` package.

| Case | ay | stdlib | vs stdlib | vs accel_off |
| --- | ---: | ---: | ---: | ---: |
| targz accel **off** | 21.3 ms | 18.0 ms | 1.18× | — |
| targz accel **on** | 8.3 ms | 18.2 ms | **0.45×** | **2.57×** faster |
| tarbz2 accel **off** | 375 ms | 366 ms | 1.02× | — |
| tarbz2 accel **on** | 126 ms | 365 ms | **0.35×** | **2.97×** faster |

Takeaway: with accelerators off we track stdlib; with them on, archivey
beats `tarfile` by ~2–3× on this sequential read-all workload — the
interesting comparison the harness now records as
`targz_read_all_accel_{off,on}` / `tarbz2_read_all_accel_{off,on}`.

At **ci** (tiny) scale the same accel_on cases are often *slower* than
accel_off (indexing / thread-pool startup dominates). Do not read CI-scale
wall ratios for accelerators as regressions; use realistic scale.

### Structural / solid

| Check | Result |
| --- | --- |
| ZIP/TAR/gzip sequential bytes | exact match — no silent re-decode |
| solid 7z sequential | decode-once |
| solid 7z random `read()` | ~32.5× re-decode (n=64) — recorded, not gated |

### Gate policy

- PR CI: `--mode structural --scale ci`.
- Wall timing: unmeasured archivey vs stdlib (measurement used only for
  bytes/seeks). VISION band still informational on realistic full mode.
