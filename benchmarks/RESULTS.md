# Benchmark gate — sample results

Captured on the agent host while landing `benchmark-gate` (2026-07-14).
Re-run with:

```bash
uv run --extra all python -m benchmarks.harness --mode structural
uv run --extra all python -m benchmarks.harness --mode full
```

Fixtures: 8×~4 KiB ZIP/TAR members, one ~64 KiB gzip, solid 7z with 32×64 KiB
members (~2.0 MiB unpacked). Solid RAR was skipped here (`rar` builder absent;
`unrar` alone cannot create fixtures).

## Verdict

| Check | Result |
| --- | --- |
| Structural gate (`--mode structural`) | **PASS** (exit 0) |
| Silent re-decode on ZIP/TAR/gzip sequential read | **None** (bytes_decompressed == unpacked) |
| Solid 7z sequential (`stream_members`) | **Decode-once** (2,097,238 == unpacked) |
| Solid 7z random `open()` / `read()` | **Re-decode visible** (16.5× bytes) — recorded, not gated |
| Wall-time vs VISION ≤1.3× on this micro corpus | **Above 1.3×** (3.3–5.4×) — under sanity ceiling 10×; not PR-gated |

## Structural results

| Case | wall (ms) | bytes decompressed | seeks | unpacked | notes |
| --- | ---: | ---: | ---: | ---: | --- |
| zip_open_list | 1.4 | 0 | 4 | — | list only |
| zip_read_all | 1.1 | 32,784 | 28 | 32,784 | exact match |
| zip_extract | 4.1 | 32,784 | 28 | — | |
| tar_open_list | 0.5 | 0 | 8 | — | list only |
| tar_read_all | 0.7 | 32,784 | 16 | 32,784 | exact match |
| gzip_read_all | 0.6 | 65,538 | 1 | 65,538 | exact match |
| sevenzip_solid_sequential | 5.2 | 2,097,238 | 4 | 2,097,238 | ≤ unpacked×2 → PASS |
| sevenzip_solid_random | 56.9 | 34,604,317 | 35 | 2,097,238 | 16.5× re-decode; not gated |

### Solid O(n²) signal (the trap the gate is for)

Random member opens on the solid 7z folder re-decode from the folder start each
time:

- sequential: **2.0 MiB** decoded in ~5 ms
- random (32 members, reverse order): **33.0 MiB** decoded in ~57 ms
- inflation: **16.5× bytes**, **11× wall** (≈ n/2 average prefix for n=32)

A harness case that counted that random pass as “sequential” would fail the
structural gate (`bytes > unpacked×2`). The intentional `*_random` case only
records the cost.

## Wall-time ratios (full mode, micro corpus)

| Case | archivey / stdlib | vs VISION 1.3× | vs sanity 10× |
| --- | ---: | --- | --- |
| zip_read_all | ~4.9–5.0× | above | under |
| tar_read_all | ~3.3–3.6× | above | under |
| gzip_read_all | ~5.0–5.4× | above | under |

These are **tiny** archives (sub-millisecond stdlib baselines), so absolute
ratios are dominated by fixed overhead and are **not** yet the VISION claim
surface. PR CI gates structural invariants only; `--mode full` is for
nightly/on-demand. Tighten corpora and tolerance once runner variance is
measured.

## Not measured this run

- Solid RAR (needs `rar` to build; `unrar` is present for reading)
- Larger common-path corpora that would make ≤1.3× meaningful
- Peak RSS / tracemalloc (deferred fourth axis)
