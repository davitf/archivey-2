# rapidgzip truncation sweep results

Platform: **Darwin arm64** (Python 3.11.15, `macOS-26.4-arm64-arm-64bit`)

## Counts by backend

| backend | raise | silent_zero | silent_short | full | timeout | crash |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| indexed_bzip2:par=0 | 293 | 54 | 0 | 5 | 0 | 0 |
| rapidgzip:par=0 | 592 | 10 | 1 | 7 | 0 | 0 |
| stdlib | 592 | 10 | 1 | 7 | 0 | 0 |
| stdlib_bz2 | 347 | 0 | 0 | 5 | 0 | 0 |

## Silent accelerator cases (the interesting set)

| fixture | cut/size | backend | par | outcome | out_len | expected | exc |
| --- | --- | --- | --- | --- | ---: | ---: | --- |
| gz_empty | 10/20 | rapidgzip | 0 | silent_zero | 0 | 0 |  |
| gz_tiny | 10/21 | rapidgzip | 0 | silent_zero | 0 | 1 |  |
| gz_small | 10/31 | rapidgzip | 0 | silent_zero | 0 | 11 |  |
| gz_medium | 10/55 | rapidgzip | 0 | silent_zero | 0 | 1040 |  |
| gz_large | 10/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_multiblock | 10/164 | rapidgzip | 0 | silent_zero | 0 | 1040 |  |
| gz_multimember | 10/83 | rapidgzip | 0 | silent_zero | 0 | 43 |  |
| gz_multimember | 38/83 | rapidgzip | 0 | silent_short | 18 | 43 |  |
| gz_header_only_10 | 10/10 | rapidgzip | 0 | silent_zero | 0 | None |  |
| gz_header_plus_1 | 10/11 | rapidgzip | 0 | silent_zero | 0 | None |  |
| gz_header_plus_8 | 10/18 | rapidgzip | 0 | silent_zero | 0 | None |  |
| bz_empty | 0/14 | indexed_bzip2 | 0 | silent_zero | 0 | 0 |  |
| bz_empty | 1/14 | indexed_bzip2 | 0 | silent_zero | 0 | 0 |  |
| bz_empty | 2/14 | indexed_bzip2 | 0 | silent_zero | 0 | 0 |  |
| bz_empty | 3/14 | indexed_bzip2 | 0 | silent_zero | 0 | 0 |  |
| bz_empty | 4/14 | indexed_bzip2 | 0 | silent_zero | 0 | 0 |  |
| bz_empty | 5/14 | indexed_bzip2 | 0 | silent_zero | 0 | 0 |  |
| bz_empty | 6/14 | indexed_bzip2 | 0 | silent_zero | 0 | 0 |  |
| bz_empty | 7/14 | indexed_bzip2 | 0 | silent_zero | 0 | 0 |  |
| bz_empty | 8/14 | indexed_bzip2 | 0 | silent_zero | 0 | 0 |  |
| bz_empty | 9/14 | indexed_bzip2 | 0 | silent_zero | 0 | 0 |  |
| bz_empty | 10/14 | indexed_bzip2 | 0 | silent_zero | 0 | 0 |  |
| bz_empty | 11/14 | indexed_bzip2 | 0 | silent_zero | 0 | 0 |  |
| bz_empty | 12/14 | indexed_bzip2 | 0 | silent_zero | 0 | 0 |  |
| bz_empty | 13/14 | indexed_bzip2 | 0 | silent_zero | 0 | 0 |  |
| bz_tiny | 0/37 | indexed_bzip2 | 0 | silent_zero | 0 | 1 |  |
| bz_tiny | 1/37 | indexed_bzip2 | 0 | silent_zero | 0 | 1 |  |
| bz_tiny | 2/37 | indexed_bzip2 | 0 | silent_zero | 0 | 1 |  |
| bz_tiny | 3/37 | indexed_bzip2 | 0 | silent_zero | 0 | 1 |  |
| bz_tiny | 4/37 | indexed_bzip2 | 0 | silent_zero | 0 | 1 |  |
| bz_tiny | 5/37 | indexed_bzip2 | 0 | silent_zero | 0 | 1 |  |
| bz_tiny | 6/37 | indexed_bzip2 | 0 | silent_zero | 0 | 1 |  |
| bz_tiny | 7/37 | indexed_bzip2 | 0 | silent_zero | 0 | 1 |  |
| bz_tiny | 8/37 | indexed_bzip2 | 0 | silent_zero | 0 | 1 |  |
| bz_tiny | 9/37 | indexed_bzip2 | 0 | silent_zero | 0 | 1 |  |
| bz_small | 0/48 | indexed_bzip2 | 0 | silent_zero | 0 | 11 |  |
| bz_small | 1/48 | indexed_bzip2 | 0 | silent_zero | 0 | 11 |  |
| bz_small | 2/48 | indexed_bzip2 | 0 | silent_zero | 0 | 11 |  |
| bz_small | 3/48 | indexed_bzip2 | 0 | silent_zero | 0 | 11 |  |
| bz_small | 4/48 | indexed_bzip2 | 0 | silent_zero | 0 | 11 |  |
| bz_small | 5/48 | indexed_bzip2 | 0 | silent_zero | 0 | 11 |  |
| bz_small | 6/48 | indexed_bzip2 | 0 | silent_zero | 0 | 11 |  |
| bz_small | 7/48 | indexed_bzip2 | 0 | silent_zero | 0 | 11 |  |
| bz_small | 8/48 | indexed_bzip2 | 0 | silent_zero | 0 | 11 |  |
| bz_small | 9/48 | indexed_bzip2 | 0 | silent_zero | 0 | 11 |  |
| bz_medium | 0/92 | indexed_bzip2 | 0 | silent_zero | 0 | 1040 |  |
| bz_medium | 1/92 | indexed_bzip2 | 0 | silent_zero | 0 | 1040 |  |
| bz_medium | 2/92 | indexed_bzip2 | 0 | silent_zero | 0 | 1040 |  |
| bz_medium | 3/92 | indexed_bzip2 | 0 | silent_zero | 0 | 1040 |  |
| bz_medium | 4/92 | indexed_bzip2 | 0 | silent_zero | 0 | 1040 |  |
| bz_medium | 5/92 | indexed_bzip2 | 0 | silent_zero | 0 | 1040 |  |
| bz_medium | 6/92 | indexed_bzip2 | 0 | silent_zero | 0 | 1040 |  |
| bz_medium | 7/92 | indexed_bzip2 | 0 | silent_zero | 0 | 1040 |  |
| bz_medium | 8/92 | indexed_bzip2 | 0 | silent_zero | 0 | 1040 |  |
| bz_medium | 9/92 | indexed_bzip2 | 0 | silent_zero | 0 | 1040 |  |
| bz_large | 0/156 | indexed_bzip2 | 0 | silent_zero | 0 | 36000 |  |
| bz_large | 1/156 | indexed_bzip2 | 0 | silent_zero | 0 | 36000 |  |
| bz_large | 2/156 | indexed_bzip2 | 0 | silent_zero | 0 | 36000 |  |
| bz_large | 3/156 | indexed_bzip2 | 0 | silent_zero | 0 | 36000 |  |
| bz_large | 4/156 | indexed_bzip2 | 0 | silent_zero | 0 | 36000 |  |
| bz_large | 5/156 | indexed_bzip2 | 0 | silent_zero | 0 | 36000 |  |
| bz_large | 6/156 | indexed_bzip2 | 0 | silent_zero | 0 | 36000 |  |
| bz_large | 7/156 | indexed_bzip2 | 0 | silent_zero | 0 | 36000 |  |
| bz_large | 8/156 | indexed_bzip2 | 0 | silent_zero | 0 | 36000 |  |
| bz_large | 9/156 | indexed_bzip2 | 0 | silent_zero | 0 | 36000 |  |

## Fixtures

| name | codec | size | expected_payload | note |
| --- | --- | ---: | ---: | --- |
| gz_empty | gzip | 20 | 0 | empty payload |
| gz_tiny | gzip | 21 | 1 | < 1 block |
| gz_small | gzip | 31 | 11 | < 1 block |
| gz_medium | gzip | 55 | 1040 | single block ~1KiB |
| gz_large | gzip | 187 | 36000 | single-member larger payload |
| gz_multiblock | gzip | 164 | 1040 | single member, multiple deflate blocks (Z_FULL_FLUSH) |
| gz_multimember | gzip | 83 | 43 | concatenated two-member gzip |
| gz_header_only_10 | gzip | 10 | n/a (incomplete) | bare 10-byte gzip header, no deflate/trailer (maintainer silent case) |
| gz_header_plus_1 | gzip | 11 | n/a (incomplete) | header + 1 byte of would-be deflate |
| gz_header_plus_8 | gzip | 18 | n/a (incomplete) | header + 8 bytes (still no valid trailer) |
| bz_empty | bzip2 | 14 | 0 | empty payload |
| bz_tiny | bzip2 | 37 | 1 | tiny |
| bz_small | bzip2 | 48 | 11 | small |
| bz_medium | bzip2 | 92 | 1040 | ~1 KiB |
| bz_large | bzip2 | 156 | 36000 | ~36 KiB |

## Per-fixture rapidgzip vs stdlib (gzip) / IndexedBzip2 vs bz2

### gz_empty

| cut | stdlib | rapidgzip/par=0 | notes |
| ---: | --- | --- | --- |
| 0 | **silent_zero**(len=0) | raise:ValueError | diff (silent_zero vs raise) |
| 1 | raise:BadGzipFile | raise:ValueError |  |
| 2 | raise:EOFError | raise:ValueError |  |
| 9 | raise:EOFError | raise:ValueError |  |
| 10 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 11 | raise:EOFError | raise:RuntimeError |  |
| 17 | raise:EOFError | raise:RuntimeError |  |
| 18 | raise:EOFError | raise:RuntimeError |  |
| 19 | raise:EOFError | raise:RuntimeError |  |
| 20 | full | full |  |
| … | _11 cuts omitted_ | | |

### gz_tiny

| cut | stdlib | rapidgzip/par=0 | notes |
| ---: | --- | --- | --- |
| 0 | **silent_zero**(len=0) | raise:ValueError | diff (silent_zero vs raise) |
| 1 | raise:BadGzipFile | raise:ValueError |  |
| 2 | raise:EOFError | raise:ValueError |  |
| 9 | raise:EOFError | raise:ValueError |  |
| 10 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 11 | raise:EOFError | raise:RuntimeError |  |
| 17 | raise:EOFError | raise:RuntimeError |  |
| 18 | raise:EOFError | raise:RuntimeError |  |
| 19 | raise:EOFError | raise:RuntimeError |  |
| 20 | raise:EOFError | raise:RuntimeError |  |
| 21 | full | full |  |
| … | _11 cuts omitted_ | | |

### gz_small

| cut | stdlib | rapidgzip/par=0 | notes |
| ---: | --- | --- | --- |
| 0 | **silent_zero**(len=0) | raise:ValueError | diff (silent_zero vs raise) |
| 1 | raise:BadGzipFile | raise:ValueError |  |
| 2 | raise:EOFError | raise:ValueError |  |
| 9 | raise:EOFError | raise:ValueError |  |
| 10 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 11 | raise:EOFError | raise:RuntimeError |  |
| 17 | raise:EOFError | raise:RuntimeError |  |
| 18 | raise:EOFError | raise:RuntimeError |  |
| 29 | raise:EOFError | raise:RuntimeError |  |
| 30 | raise:EOFError | raise:RuntimeError |  |
| 31 | full | full |  |
| … | _21 cuts omitted_ | | |

### gz_medium

| cut | stdlib | rapidgzip/par=0 | notes |
| ---: | --- | --- | --- |
| 0 | **silent_zero**(len=0) | raise:ValueError | diff (silent_zero vs raise) |
| 1 | raise:BadGzipFile | raise:ValueError |  |
| 2 | raise:EOFError | raise:ValueError |  |
| 9 | raise:EOFError | raise:ValueError |  |
| 10 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 11 | raise:EOFError | raise:RuntimeError |  |
| 17 | raise:EOFError | raise:RuntimeError |  |
| 18 | raise:EOFError | raise:RuntimeError |  |
| 53 | raise:EOFError | raise:RuntimeError |  |
| 54 | raise:EOFError | raise:RuntimeError |  |
| 55 | full | full |  |
| … | _45 cuts omitted_ | | |

### gz_large

| cut | stdlib | rapidgzip/par=0 | notes |
| ---: | --- | --- | --- |
| 0 | **silent_zero**(len=0) | raise:ValueError | diff (silent_zero vs raise) |
| 1 | raise:BadGzipFile | raise:ValueError |  |
| 2 | raise:EOFError | raise:ValueError |  |
| 9 | raise:EOFError | raise:ValueError |  |
| 10 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 11 | raise:EOFError | raise:RuntimeError |  |
| 17 | raise:EOFError | raise:RuntimeError |  |
| 18 | raise:EOFError | raise:RuntimeError |  |
| 185 | raise:EOFError | raise:RuntimeError |  |
| 186 | raise:EOFError | raise:RuntimeError |  |
| 187 | full | full |  |
| … | _177 cuts omitted_ | | |

### gz_multiblock

| cut | stdlib | rapidgzip/par=0 | notes |
| ---: | --- | --- | --- |
| 0 | **silent_zero**(len=0) | raise:ValueError | diff (silent_zero vs raise) |
| 1 | raise:BadGzipFile | raise:ValueError |  |
| 2 | raise:EOFError | raise:ValueError |  |
| 9 | raise:EOFError | raise:ValueError |  |
| 10 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 11 | raise:EOFError | raise:RuntimeError |  |
| 17 | raise:EOFError | raise:RuntimeError |  |
| 18 | raise:EOFError | raise:RuntimeError |  |
| 162 | raise:EOFError | raise:RuntimeError |  |
| 163 | raise:EOFError | raise:RuntimeError |  |
| 164 | full | full |  |
| … | _154 cuts omitted_ | | |

### gz_multimember

| cut | stdlib | rapidgzip/par=0 | notes |
| ---: | --- | --- | --- |
| 0 | **silent_zero**(len=0) | raise:ValueError | diff (silent_zero vs raise) |
| 1 | raise:BadGzipFile | raise:ValueError |  |
| 2 | raise:EOFError | raise:ValueError |  |
| 9 | raise:EOFError | raise:ValueError |  |
| 10 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 11 | raise:EOFError | raise:RuntimeError |  |
| 17 | raise:EOFError | raise:RuntimeError |  |
| 18 | raise:EOFError | raise:RuntimeError |  |
| 38 | **silent_short**(len=18) | **silent_short**(len=18) |  |
| 81 | raise:EOFError | raise:RuntimeError |  |
| 82 | raise:EOFError | raise:RuntimeError |  |
| 83 | full | full |  |
| … | _72 cuts omitted_ | | |

### gz_header_only_10

| cut | stdlib | rapidgzip/par=0 | notes |
| ---: | --- | --- | --- |
| 0 | **silent_zero**(len=0) | raise:ValueError | diff (silent_zero vs raise) |
| 1 | raise:BadGzipFile | raise:ValueError |  |
| 2 | raise:EOFError | raise:ValueError |  |
| 8 | raise:EOFError | raise:ValueError |  |
| 9 | raise:EOFError | raise:ValueError |  |
| 10 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| … | _5 cuts omitted_ | | |

### gz_header_plus_1

| cut | stdlib | rapidgzip/par=0 | notes |
| ---: | --- | --- | --- |
| 0 | **silent_zero**(len=0) | raise:ValueError | diff (silent_zero vs raise) |
| 1 | raise:BadGzipFile | raise:ValueError |  |
| 2 | raise:EOFError | raise:ValueError |  |
| 9 | raise:EOFError | raise:ValueError |  |
| 10 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 11 | raise:EOFError | raise:RuntimeError |  |
| … | _6 cuts omitted_ | | |

### gz_header_plus_8

| cut | stdlib | rapidgzip/par=0 | notes |
| ---: | --- | --- | --- |
| 0 | **silent_zero**(len=0) | raise:ValueError | diff (silent_zero vs raise) |
| 1 | raise:BadGzipFile | raise:ValueError |  |
| 2 | raise:EOFError | raise:ValueError |  |
| 9 | raise:EOFError | raise:ValueError |  |
| 10 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 11 | raise:EOFError | raise:RuntimeError |  |
| 16 | raise:error | raise:ValueError |  |
| 17 | raise:error | raise:ValueError |  |
| 18 | raise:error | raise:ValueError |  |
| … | _10 cuts omitted_ | | |

### bz_empty

| cut | stdlib | rapidgzip/par=0 | notes |
| ---: | --- | --- | --- |
| 0 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 1 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 2 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 3 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 4 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 5 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 6 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 7 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 8 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 9 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 10 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 11 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 12 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 13 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 14 | full | full |  |

### bz_tiny

| cut | stdlib | rapidgzip/par=0 | notes |
| ---: | --- | --- | --- |
| 0 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 1 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 2 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 3 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 4 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 5 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 6 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 7 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 8 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 9 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 10 | raise:EOFError | raise:RuntimeError |  |
| 11 | raise:EOFError | raise:RuntimeError |  |
| 17 | raise:EOFError | raise:RuntimeError |  |
| 18 | raise:EOFError | raise:RuntimeError |  |
| 35 | raise:EOFError | raise:RuntimeError |  |
| 36 | raise:EOFError | raise:RuntimeError |  |
| 37 | full | full |  |
| … | _21 cuts omitted_ | | |

### bz_small

| cut | stdlib | rapidgzip/par=0 | notes |
| ---: | --- | --- | --- |
| 0 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 1 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 2 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 3 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 4 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 5 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 6 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 7 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 8 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 9 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 10 | raise:EOFError | raise:RuntimeError |  |
| 11 | raise:EOFError | raise:RuntimeError |  |
| 17 | raise:EOFError | raise:RuntimeError |  |
| 18 | raise:EOFError | raise:RuntimeError |  |
| 46 | raise:EOFError | raise:RuntimeError |  |
| 47 | raise:EOFError | raise:RuntimeError |  |
| 48 | full | full |  |
| … | _32 cuts omitted_ | | |

### bz_medium

| cut | stdlib | rapidgzip/par=0 | notes |
| ---: | --- | --- | --- |
| 0 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 1 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 2 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 3 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 4 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 5 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 6 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 7 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 8 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 9 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 10 | raise:EOFError | raise:RuntimeError |  |
| 11 | raise:EOFError | raise:RuntimeError |  |
| 17 | raise:EOFError | raise:RuntimeError |  |
| 18 | raise:EOFError | raise:RuntimeError |  |
| 90 | raise:EOFError | raise:RuntimeError |  |
| 91 | raise:EOFError | raise:RuntimeError |  |
| 92 | full | full |  |
| … | _76 cuts omitted_ | | |

### bz_large

| cut | stdlib | rapidgzip/par=0 | notes |
| ---: | --- | --- | --- |
| 0 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 1 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 2 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 3 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 4 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 5 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 6 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 7 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 8 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 9 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 10 | raise:EOFError | raise:RuntimeError |  |
| 11 | raise:EOFError | raise:RuntimeError |  |
| 17 | raise:EOFError | raise:RuntimeError |  |
| 18 | raise:EOFError | raise:RuntimeError |  |
| 154 | raise:EOFError | raise:RuntimeError |  |
| 155 | raise:EOFError | raise:RuntimeError |  |
| 156 | full | full |  |
| … | _140 cuts omitted_ | | |

