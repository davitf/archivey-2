# Before/after benchmarks — verify fusion (#137)

Host: shared x86_64 runner, CPython 3.11, `[all]` extras.
Compared trees via `PYTHONPATH=<tree>/src` against a shared fixture dir.

- **main** `@ 38f7b99`
- **#136** `@ 2a6b91b` (immediate before — nested AS collapse, no verify fusion)
- **fusion** (this PR) `@ fba9f69`

## Stack (STORED ZIP, after first read)

| Tree | Stack |
|------|-------|
| main / #136 | `ArchiveStream → VerifyingStream → ArchiveStream → SlicingStream` |
| fusion | `ArchiveStream → SlicingStream` (verifier fused; codec AS collapsed) |

## STORED / DEFLATE microbench (64 × 256 KiB, 41 warm rounds)

`stream_members` read-all medians (ms); ratio vs stdlib `zipfile`.

| | main | #136 | fusion | fusion vs #136 |
|--|-----:|-----:|-------:|---------------:|
| STORED archivey | 7.76 | 7.53 | 7.67 | **+1.8%** |
| STORED zipfile | 4.45 | 4.43 | 4.46 | — |
| STORED ratio | 1.74× | 1.70× | 1.72× | — |
| STORED `open()`+read | 7.61 | 7.53 | 7.62 | +1.2% |
| DEFLATE archivey | 30.4 | 30.7 | 31.6 | **+2.7%** |
| DEFLATE ratio | 1.92× | 1.96× | 2.00× | — |

## Harness `--mode full --scale realistic --warmup` (#136 → fusion)

Shared fixtures. Wall medians (ms); Δ% = fusion vs #136.

| Case | #136 | fusion | Δ | #136× | fusion× |
|------|-----:|-------:|--:|------:|--------:|
| `zip_open_list` | 0.78 | 0.76 | −2.3% | — | — |
| `zip_read_all` | 27.22 | 27.21 | −0.0% | 2.00 | 2.03 |
| `zip_extract` | 53.47 | 55.47 | +3.7% | — | — |
| `tar_open_list` | 1.54 | 1.69 | +9.5% | — | — |
| `tar_read_all` | 3.94 | 4.03 | +2.4% | 1.76 | 1.77 |
| `gzip_read_all` | 36.47 | 36.27 | −0.6% | 1.02 | 1.04 |
| `targz_read_all_accel_off` | 23.21 | 22.10 | −4.8% | 1.24 | 1.22 |
| `targz_read_all_accel_on` | 10.89 | 9.19 | −15.7%* | 0.43 | 0.51 |
| `tarbz2_read_all_accel_off` | 381.87 | 381.66 | −0.1% | 1.03 | 1.03 |
| `tarbz2_read_all_accel_on` | 129.31 | 129.23 | −0.1% | 0.35 | 0.35 |
| `sevenzip_solid_sequential` | 8.90 | 8.65 | −2.8% | — | — |
| `sevenzip_solid_random` | 155.18 | 150.55 | −3.0% | — | — |

\*accel-on rows are noisier (accelerator startup); treat as flat.

## Takeaway

Wall times are **within noise (~±5%)** of #136. Fusion is a **structural** win
(one fewer wrapper; codec `ArchiveStream` collapse finishes) and fixes F1/F2; it
does **not** close the ZIP ≤1.3× gap (still ~2.0× on `zip_read_all`). Matches the
review's measured ~5% upper bound on the wrapper share.
