# Benchmark gate — sample results

**Blocking PR gate = structural invariants only** (seek-count baselines + solid
decode-once). Wall-time / VISION ≤1.3× runs off the PR path as a **change-guarded
nightly** (`benchmark-wall.yml`): a daily schedule whose expensive realistic run is
skipped unless the default branch changed since the previous run (this project is
bursty/dormant, and per-PR taxed every PR). When it runs it:

- records drift (JSON + markdown report artifacts, job summary table);
- hard-fails on the ~10× sanity ceiling **or** on **wall-ratio drift** vs the
  previous successful nightly's JSON (relative regression gate — debt-ledger Q1 /
  perf Q2 option (a));
- prints absolute VISION / Q1 listing bands as informational only.

`workflow_dispatch` can pass `skip_drift=true` to accept the current ratios as a
new baseline after an intentional slowdown.

Re-run with:

```bash
# PR structural gate (small fixtures, fast)
uv run --extra all python -m benchmarks.harness --mode structural --scale ci

# Realistic wall-time vs stdlib (multi-MiB corpora; interleaved medians)
uv run --extra all python -m benchmarks.harness --mode full --scale realistic
```

**Measurement host for tables below:** 4-core Intel Xeon (KVM), Linux x86_64.
rapidgzip parallelises across cores — absolute speedups scale with core count;
treat the figures as directional, not portable.

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

On a quieter/smaller runner these drift (e.g. zip ~1.18×→~1.55×) — hence wall
ratios are not a PR gate.

### `.tar.gz` / `.tar.bz2` accelerators (off vs on)

Same ~16 MiB multi-member corpus on the **4-core** host above. Stdlib peer is
`tarfile` `r:gz` / `r:bz2` (always stdlib codecs). Archivey forces
`use_rapidgzip` / `use_indexed_bzip2` ON or OFF; ON also sets
`MemberStreams.SEEKABLE`.

The bzip2 accelerator is **`rapidgzip.IndexedBzip2File`** (via
`use_indexed_bzip2`), not the separate `indexed_bzip2` package.

| Case | ay | stdlib | vs stdlib | vs accel_off |
| --- | ---: | ---: | ---: | ---: |
| targz accel **off** | 21.3 ms | 18.0 ms | 1.18× | — |
| targz accel **on** | 8.3 ms | 18.2 ms | **0.45×** | **2.57×** faster |
| tarbz2 accel **off** | 375 ms | 366 ms | 1.02× | — |
| tarbz2 accel **on** | 126 ms | 365 ms | **0.35×** | **2.97×** faster |

**Speedup scales with cores** (rapidgzip is parallel). On a 2-core box the same
workload reproduced ~0.88× / ~0.50× vs stdlib — direction holds, absolute
multipliers do not. At **ci** (tiny) scale accel_on is often *slower* than
accel_off (indexing / thread-pool startup dominates).

### Structural / solid

| Check | Result |
| --- | --- |
| ZIP/TAR/gzip seek counts | within committed baseline bound (silent re-open churn) |
| ZIP/TAR/gzip `bytes == unpacked` | tautological at member-output layer — under-decode guard only |
| solid 7z sequential | decode-once (also pinned in `test_measurement.py`) |
| solid 7z random `read()` | ~32.5× re-decode (n=64) — recorded, not gated |
| solid RAR decode-once | unit-tested against committed fixtures (`unrar` only) |

ISO / directory: measurement is wired; harness cases are out of scope (ISO
lock baseline lives in `benchmarks/tar_iso_lock_baseline.py`).

### Gate policy

- **PR CI (blocking):** `--mode structural --scale ci` + unit decode-once tests.
- **Change-guarded nightly (off the PR path, `benchmark-wall.yml`):** `--mode full
  --scale realistic` with JSON + markdown report artifact upload (the markdown is also
  written to the Actions job summary so it is readable without downloading) — a daily
  schedule that skips its expensive run unless the default branch changed since the previous
  run (bursty/dormant project; per-PR was rejected for taxing every PR). `workflow_dispatch`
  forces a run (`skip_drift` re-seeds after intentional regressions).
- Wall timing: unmeasured archivey vs stdlib; absolute VISION bands informational;
  nightly hard-fails on wall-ratio *drift* vs the previous successful artifact.
