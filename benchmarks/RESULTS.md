# Benchmark gate — sample results

Re-run with:

```bash
# PR structural gate (small fixtures, fast)
uv run --extra all python -m benchmarks.harness --mode structural --scale ci

# Realistic wall-time vs stdlib (multi-MiB corpora; warmup on by default)
uv run --extra all python -m benchmarks.harness --mode full --scale realistic
```

## Realistic scale (2026-07-14, agent host)

**Corpus:** 64×256 KiB ZIP/TAR members (~16 MiB unpacked), 32 MiB gzip, solid 7z
64×256 KiB (~16 MiB unpacked). Semi-compressible patterned payloads. One warmup
pass discarded before each timed op. Solid RAR skipped (`rar` builder absent).

### Verdict

| Check | Result |
| --- | --- |
| Structural (bytes / solid invariant) | **PASS** |
| Silent re-decode ZIP/TAR/gzip sequential | **None** (exact byte match) |
| Solid 7z sequential | **Decode-once** (16,777,216 == unpacked) |
| Solid 7z random `read()` | **32.5× re-decode** (expected ≈ n/2 for n=64) — recorded, not gated |
| ZIP wall vs `zipfile` | **~0.82×** — within VISION 1.3× (faster than stdlib here) |
| gzip wall vs `gzip` | **~1.23×** — within VISION 1.3× |
| TAR wall vs `tarfile` | **~2.0–2.8×** — at/above VISION ~2× safety band |

### Wall-time ratios (3 consecutive runs)

| Case | run 1 | run 2 | run 3 | archivey (typ.) | stdlib (typ.) |
| --- | ---: | ---: | ---: | ---: | ---: |
| zip_read_all | 0.821× | 0.820× | 0.824× | ~11 ms | ~13 ms |
| tar_read_all | 2.605× | 1.987× | 2.225× | ~4–5 ms | ~2.1 ms |
| gzip_read_all | 1.229× | 1.227× | 1.273× | ~38 ms | ~31 ms |

ZIP and gzip look healthy against VISION. Plain TAR is the outlier: stable
~2×–2.6× with low absolute times (stdlib ~2 ms), so fixed per-member overhead
still matters. Treat as a real signal to investigate / annotate, not noise from
a tiny corpus — but absolute times are still small enough that further larger
TAR corpora (or fewer members / bigger members) would refine the ratio.

### Structural / solid (representative run)

| Case | wall | bytes decompressed | seeks | unpacked |
| --- | ---: | ---: | ---: | ---: |
| zip_read_all | 11 ms | 16,777,216 | 196 | 16,777,216 |
| tar_read_all | 5 ms | 16,777,216 | 128 | 16,777,216 |
| gzip_read_all | 37 ms | 33,554,432 | 16 | 33,554,432 |
| sevenzip_solid_sequential | 8.5 ms | 16,777,216 | 4 | 16,777,216 |
| sevenzip_solid_random | 155 ms | 545,259,520 | 67 | 16,777,216 |

Solid random inflation: **32.5× bytes**, **~18× wall** — the O(n²) trap the
gate exists to catch. Sequential path does not re-decode.

### Gate policy

- PR CI: `--mode structural --scale ci` (bytes/seeks only).
- Realistic `--mode full`: sanity ceiling 10× hard-fails; VISION 1.3×/2×
  overshoots print as informational until TAR (and variance) are settled.

---

## Earlier ci-scale sample (micro corpus)

Tiny 8×4 KiB / 64 KiB gzip fixtures produced 3–5× wall ratios dominated by fixed
overhead — not usable for VISION claims. Retained below for contrast; prefer the
realistic table above.

| Case | wall ratio (ci) |
| --- | ---: |
| zip_read_all | ~4.9× |
| tar_read_all | ~3.5× |
| gzip_read_all | ~5.2× |
| solid sequential | decode-once (~2 MiB) |
| solid random | ~16.5× re-decode (n=32) |
