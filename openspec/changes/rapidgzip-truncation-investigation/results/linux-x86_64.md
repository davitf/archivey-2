# rapidgzip truncation sweep results

Platform: **Linux x86_64** (Python 3.11.15, `Linux-6.12.94+-x86_64-with-glibc2.39`)

## Counts by backend

| backend | raise | silent_zero | silent_short | full | timeout | crash |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| indexed_bzip2:par=0 | 293 | 54 | 0 | 5 | 0 | 0 |
| indexed_bzip2:par=1 | 342 | 5 | 0 | 5 | 0 | 0 |
| rapidgzip:par=0 | 177 | 283 | 134 | 16 | 0 | 0 |
| rapidgzip:par=1 | 177 | 283 | 134 | 16 | 0 | 0 |
| stdlib | 592 | 10 | 1 | 7 | 0 | 0 |
| stdlib_bz2 | 347 | 0 | 0 | 5 | 0 | 0 |

## Silent accelerator cases (the interesting set)

| fixture | cut/size | backend | par | outcome | out_len | expected | exc |
| --- | --- | --- | --- | --- | ---: | ---: | --- |
| gz_empty | 10/20 | rapidgzip | 0 | silent_zero | 0 | 0 |  |
| gz_empty | 10/20 | rapidgzip | 1 | silent_zero | 0 | 0 |  |
| gz_empty | 11/20 | rapidgzip | 0 | silent_zero | 0 | 0 |  |
| gz_empty | 11/20 | rapidgzip | 1 | silent_zero | 0 | 0 |  |
| gz_tiny | 10/21 | rapidgzip | 0 | silent_zero | 0 | 1 |  |
| gz_tiny | 10/21 | rapidgzip | 1 | silent_zero | 0 | 1 |  |
| gz_tiny | 11/21 | rapidgzip | 0 | silent_zero | 0 | 1 |  |
| gz_tiny | 11/21 | rapidgzip | 1 | silent_zero | 0 | 1 |  |
| gz_tiny | 12/21 | rapidgzip | 0 | silent_zero | 0 | 1 |  |
| gz_tiny | 12/21 | rapidgzip | 1 | silent_zero | 0 | 1 |  |
| gz_small | 10/31 | rapidgzip | 0 | silent_zero | 0 | 11 |  |
| gz_small | 10/31 | rapidgzip | 1 | silent_zero | 0 | 11 |  |
| gz_small | 11/31 | rapidgzip | 0 | silent_zero | 0 | 11 |  |
| gz_small | 11/31 | rapidgzip | 1 | silent_zero | 0 | 11 |  |
| gz_small | 12/31 | rapidgzip | 0 | silent_zero | 0 | 11 |  |
| gz_small | 12/31 | rapidgzip | 1 | silent_zero | 0 | 11 |  |
| gz_small | 13/31 | rapidgzip | 0 | silent_zero | 0 | 11 |  |
| gz_small | 13/31 | rapidgzip | 1 | silent_zero | 0 | 11 |  |
| gz_small | 14/31 | rapidgzip | 0 | silent_zero | 0 | 11 |  |
| gz_small | 14/31 | rapidgzip | 1 | silent_zero | 0 | 11 |  |
| gz_small | 15/31 | rapidgzip | 0 | silent_zero | 0 | 11 |  |
| gz_small | 15/31 | rapidgzip | 1 | silent_zero | 0 | 11 |  |
| gz_small | 16/31 | rapidgzip | 0 | silent_zero | 0 | 11 |  |
| gz_small | 16/31 | rapidgzip | 1 | silent_zero | 0 | 11 |  |
| gz_small | 17/31 | rapidgzip | 0 | silent_zero | 0 | 11 |  |
| gz_small | 17/31 | rapidgzip | 1 | silent_zero | 0 | 11 |  |
| gz_small | 18/31 | rapidgzip | 0 | silent_zero | 0 | 11 |  |
| gz_small | 18/31 | rapidgzip | 1 | silent_zero | 0 | 11 |  |
| gz_small | 19/31 | rapidgzip | 0 | silent_zero | 0 | 11 |  |
| gz_small | 19/31 | rapidgzip | 1 | silent_zero | 0 | 11 |  |
| gz_small | 20/31 | rapidgzip | 0 | silent_zero | 0 | 11 |  |
| gz_small | 20/31 | rapidgzip | 1 | silent_zero | 0 | 11 |  |
| gz_small | 21/31 | rapidgzip | 0 | silent_zero | 0 | 11 |  |
| gz_small | 21/31 | rapidgzip | 1 | silent_zero | 0 | 11 |  |
| gz_small | 22/31 | rapidgzip | 0 | silent_zero | 0 | 11 |  |
| gz_small | 22/31 | rapidgzip | 1 | silent_zero | 0 | 11 |  |
| gz_medium | 10/55 | rapidgzip | 0 | silent_zero | 0 | 1040 |  |
| gz_medium | 10/55 | rapidgzip | 1 | silent_zero | 0 | 1040 |  |
| gz_medium | 11/55 | rapidgzip | 0 | silent_zero | 0 | 1040 |  |
| gz_medium | 11/55 | rapidgzip | 1 | silent_zero | 0 | 1040 |  |
| gz_medium | 12/55 | rapidgzip | 0 | silent_zero | 0 | 1040 |  |
| gz_medium | 12/55 | rapidgzip | 1 | silent_zero | 0 | 1040 |  |
| gz_medium | 13/55 | rapidgzip | 0 | silent_zero | 0 | 1040 |  |
| gz_medium | 13/55 | rapidgzip | 1 | silent_zero | 0 | 1040 |  |
| gz_medium | 14/55 | rapidgzip | 0 | silent_zero | 0 | 1040 |  |
| gz_medium | 14/55 | rapidgzip | 1 | silent_zero | 0 | 1040 |  |
| gz_medium | 15/55 | rapidgzip | 0 | silent_zero | 0 | 1040 |  |
| gz_medium | 15/55 | rapidgzip | 1 | silent_zero | 0 | 1040 |  |
| gz_medium | 16/55 | rapidgzip | 0 | silent_zero | 0 | 1040 |  |
| gz_medium | 16/55 | rapidgzip | 1 | silent_zero | 0 | 1040 |  |
| gz_medium | 17/55 | rapidgzip | 0 | silent_zero | 0 | 1040 |  |
| gz_medium | 17/55 | rapidgzip | 1 | silent_zero | 0 | 1040 |  |
| gz_medium | 18/55 | rapidgzip | 0 | silent_zero | 0 | 1040 |  |
| gz_medium | 18/55 | rapidgzip | 1 | silent_zero | 0 | 1040 |  |
| gz_medium | 19/55 | rapidgzip | 0 | silent_zero | 0 | 1040 |  |
| gz_medium | 19/55 | rapidgzip | 1 | silent_zero | 0 | 1040 |  |
| gz_medium | 20/55 | rapidgzip | 0 | silent_zero | 0 | 1040 |  |
| gz_medium | 20/55 | rapidgzip | 1 | silent_zero | 0 | 1040 |  |
| gz_medium | 21/55 | rapidgzip | 0 | silent_zero | 0 | 1040 |  |
| gz_medium | 21/55 | rapidgzip | 1 | silent_zero | 0 | 1040 |  |
| gz_medium | 22/55 | rapidgzip | 0 | silent_zero | 0 | 1040 |  |
| gz_medium | 22/55 | rapidgzip | 1 | silent_zero | 0 | 1040 |  |
| gz_medium | 23/55 | rapidgzip | 0 | silent_zero | 0 | 1040 |  |
| gz_medium | 23/55 | rapidgzip | 1 | silent_zero | 0 | 1040 |  |
| gz_medium | 24/55 | rapidgzip | 0 | silent_zero | 0 | 1040 |  |
| gz_medium | 24/55 | rapidgzip | 1 | silent_zero | 0 | 1040 |  |
| gz_medium | 25/55 | rapidgzip | 0 | silent_zero | 0 | 1040 |  |
| gz_medium | 25/55 | rapidgzip | 1 | silent_zero | 0 | 1040 |  |
| gz_medium | 26/55 | rapidgzip | 0 | silent_zero | 0 | 1040 |  |
| gz_medium | 26/55 | rapidgzip | 1 | silent_zero | 0 | 1040 |  |
| gz_medium | 27/55 | rapidgzip | 0 | silent_zero | 0 | 1040 |  |
| gz_medium | 27/55 | rapidgzip | 1 | silent_zero | 0 | 1040 |  |
| gz_medium | 28/55 | rapidgzip | 0 | silent_zero | 0 | 1040 |  |
| gz_medium | 28/55 | rapidgzip | 1 | silent_zero | 0 | 1040 |  |
| gz_medium | 29/55 | rapidgzip | 0 | silent_zero | 0 | 1040 |  |
| gz_medium | 29/55 | rapidgzip | 1 | silent_zero | 0 | 1040 |  |
| gz_medium | 30/55 | rapidgzip | 0 | silent_zero | 0 | 1040 |  |
| gz_medium | 30/55 | rapidgzip | 1 | silent_zero | 0 | 1040 |  |
| gz_medium | 31/55 | rapidgzip | 0 | silent_zero | 0 | 1040 |  |
| gz_medium | 31/55 | rapidgzip | 1 | silent_zero | 0 | 1040 |  |
| gz_medium | 32/55 | rapidgzip | 0 | silent_zero | 0 | 1040 |  |
| gz_medium | 32/55 | rapidgzip | 1 | silent_zero | 0 | 1040 |  |
| gz_medium | 33/55 | rapidgzip | 0 | silent_zero | 0 | 1040 |  |
| gz_medium | 33/55 | rapidgzip | 1 | silent_zero | 0 | 1040 |  |
| gz_medium | 34/55 | rapidgzip | 0 | silent_zero | 0 | 1040 |  |
| gz_medium | 34/55 | rapidgzip | 1 | silent_zero | 0 | 1040 |  |
| gz_medium | 35/55 | rapidgzip | 0 | silent_zero | 0 | 1040 |  |
| gz_medium | 35/55 | rapidgzip | 1 | silent_zero | 0 | 1040 |  |
| gz_medium | 36/55 | rapidgzip | 0 | silent_zero | 0 | 1040 |  |
| gz_medium | 36/55 | rapidgzip | 1 | silent_zero | 0 | 1040 |  |
| gz_medium | 37/55 | rapidgzip | 0 | silent_zero | 0 | 1040 |  |
| gz_medium | 37/55 | rapidgzip | 1 | silent_zero | 0 | 1040 |  |
| gz_medium | 38/55 | rapidgzip | 0 | silent_zero | 0 | 1040 |  |
| gz_medium | 38/55 | rapidgzip | 1 | silent_zero | 0 | 1040 |  |
| gz_medium | 39/55 | rapidgzip | 0 | silent_zero | 0 | 1040 |  |
| gz_medium | 39/55 | rapidgzip | 1 | silent_zero | 0 | 1040 |  |
| gz_medium | 40/55 | rapidgzip | 0 | silent_zero | 0 | 1040 |  |
| gz_medium | 40/55 | rapidgzip | 1 | silent_zero | 0 | 1040 |  |
| gz_medium | 41/55 | rapidgzip | 0 | silent_zero | 0 | 1040 |  |
| gz_medium | 41/55 | rapidgzip | 1 | silent_zero | 0 | 1040 |  |
| gz_medium | 42/55 | rapidgzip | 0 | silent_zero | 0 | 1040 |  |
| gz_medium | 42/55 | rapidgzip | 1 | silent_zero | 0 | 1040 |  |
| gz_medium | 43/55 | rapidgzip | 0 | silent_zero | 0 | 1040 |  |
| gz_medium | 43/55 | rapidgzip | 1 | silent_zero | 0 | 1040 |  |
| gz_medium | 44/55 | rapidgzip | 0 | silent_zero | 0 | 1040 |  |
| gz_medium | 44/55 | rapidgzip | 1 | silent_zero | 0 | 1040 |  |
| gz_medium | 45/55 | rapidgzip | 0 | silent_zero | 0 | 1040 |  |
| gz_medium | 45/55 | rapidgzip | 1 | silent_zero | 0 | 1040 |  |
| gz_medium | 46/55 | rapidgzip | 0 | silent_zero | 0 | 1040 |  |
| gz_medium | 46/55 | rapidgzip | 1 | silent_zero | 0 | 1040 |  |
| gz_large | 10/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 10/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 11/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 11/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 12/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 12/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 13/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 13/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 14/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 14/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 15/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 15/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 16/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 16/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 17/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 17/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 18/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 18/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 19/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 19/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 20/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 20/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 21/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 21/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 22/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 22/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 23/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 23/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 24/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 24/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 25/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 25/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 26/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 26/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 27/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 27/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 28/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 28/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 29/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 29/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 30/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 30/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 31/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 31/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 32/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 32/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 33/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 33/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 34/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 34/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 35/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 35/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 36/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 36/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 37/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 37/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 38/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 38/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 39/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 39/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 40/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 40/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 41/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 41/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 42/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 42/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 43/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 43/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 44/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 44/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 45/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 45/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 46/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 46/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 47/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 47/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 48/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 48/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 49/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 49/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 50/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 50/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 51/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 51/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 52/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 52/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 53/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 53/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 54/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 54/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 55/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 55/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 56/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 56/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 57/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 57/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 58/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 58/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 59/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 59/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 60/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 60/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 61/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 61/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 62/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 62/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 63/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 63/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 64/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 64/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 65/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 65/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 66/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 66/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 67/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 67/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 68/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 68/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 69/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 69/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 70/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 70/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 71/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 71/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 72/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 72/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 73/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 73/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 74/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 74/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 75/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 75/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 76/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 76/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 77/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 77/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 78/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 78/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 79/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 79/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 80/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 80/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 81/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 81/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 82/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 82/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 83/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 83/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 84/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 84/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 85/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 85/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 86/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 86/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 87/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 87/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 88/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 88/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 89/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 89/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 90/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 90/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 91/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 91/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 92/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 92/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 93/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 93/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 94/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 94/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 95/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 95/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 96/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 96/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 97/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 97/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 98/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 98/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 99/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 99/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 100/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 100/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 101/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 101/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 102/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 102/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 103/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 103/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 104/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 104/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 105/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 105/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 106/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 106/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 107/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 107/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 108/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 108/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 109/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 109/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 110/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 110/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 111/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 111/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 112/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 112/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 113/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 113/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 114/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 114/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 115/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 115/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 116/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 116/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 117/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 117/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 118/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 118/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 119/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 119/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 120/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 120/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 121/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 121/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 122/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 122/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 123/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 123/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 124/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 124/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 125/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 125/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 126/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 126/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 127/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 127/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 128/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 128/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 129/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 129/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 130/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 130/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 131/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 131/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 132/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 132/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 133/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 133/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 134/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 134/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 135/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 135/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 136/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 136/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 137/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 137/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 138/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 138/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 139/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 139/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 140/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 140/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 141/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 141/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 142/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 142/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 143/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 143/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 144/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 144/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 145/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 145/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 146/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 146/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 147/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 147/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 148/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 148/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 149/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 149/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 150/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 150/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 151/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 151/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 152/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 152/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 153/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 153/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 154/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 154/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 155/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 155/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 156/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 156/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 157/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 157/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 158/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 158/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 159/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 159/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 160/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 160/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 161/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 161/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 162/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 162/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 163/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 163/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 164/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 164/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 165/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 165/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 166/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 166/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 167/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 167/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 168/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 168/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 169/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 169/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 170/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 170/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 171/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 171/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 172/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 172/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 173/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 173/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 174/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 174/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 175/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 175/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 176/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 176/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 177/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 177/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_large | 178/187 | rapidgzip | 0 | silent_zero | 0 | 36000 |  |
| gz_large | 178/187 | rapidgzip | 1 | silent_zero | 0 | 36000 |  |
| gz_multiblock | 10/164 | rapidgzip | 0 | silent_zero | 0 | 1040 |  |
| gz_multiblock | 10/164 | rapidgzip | 1 | silent_zero | 0 | 1040 |  |
| gz_multiblock | 11/164 | rapidgzip | 0 | silent_zero | 0 | 1040 |  |
| gz_multiblock | 11/164 | rapidgzip | 1 | silent_zero | 0 | 1040 |  |
| gz_multiblock | 12/164 | rapidgzip | 0 | silent_zero | 0 | 1040 |  |
| gz_multiblock | 12/164 | rapidgzip | 1 | silent_zero | 0 | 1040 |  |
| gz_multiblock | 13/164 | rapidgzip | 0 | silent_zero | 0 | 1040 |  |
| gz_multiblock | 13/164 | rapidgzip | 1 | silent_zero | 0 | 1040 |  |
| gz_multiblock | 14/164 | rapidgzip | 0 | silent_zero | 0 | 1040 |  |
| gz_multiblock | 14/164 | rapidgzip | 1 | silent_zero | 0 | 1040 |  |
| gz_multiblock | 15/164 | rapidgzip | 0 | silent_zero | 0 | 1040 |  |
| gz_multiblock | 15/164 | rapidgzip | 1 | silent_zero | 0 | 1040 |  |
| gz_multiblock | 16/164 | rapidgzip | 0 | silent_zero | 0 | 1040 |  |
| gz_multiblock | 16/164 | rapidgzip | 1 | silent_zero | 0 | 1040 |  |
| gz_multiblock | 17/164 | rapidgzip | 0 | silent_zero | 0 | 1040 |  |
| gz_multiblock | 17/164 | rapidgzip | 1 | silent_zero | 0 | 1040 |  |
| gz_multiblock | 18/164 | rapidgzip | 0 | silent_zero | 0 | 1040 |  |
| gz_multiblock | 18/164 | rapidgzip | 1 | silent_zero | 0 | 1040 |  |
| gz_multiblock | 19/164 | rapidgzip | 0 | silent_zero | 0 | 1040 |  |
| gz_multiblock | 19/164 | rapidgzip | 1 | silent_zero | 0 | 1040 |  |
| gz_multiblock | 20/164 | rapidgzip | 0 | silent_zero | 0 | 1040 |  |
| gz_multiblock | 20/164 | rapidgzip | 1 | silent_zero | 0 | 1040 |  |
| gz_multiblock | 21/164 | rapidgzip | 0 | silent_zero | 0 | 1040 |  |
| gz_multiblock | 21/164 | rapidgzip | 1 | silent_zero | 0 | 1040 |  |
| gz_multiblock | 22/164 | rapidgzip | 0 | silent_zero | 0 | 1040 |  |
| gz_multiblock | 22/164 | rapidgzip | 1 | silent_zero | 0 | 1040 |  |
| gz_multiblock | 23/164 | rapidgzip | 0 | silent_zero | 0 | 1040 |  |
| gz_multiblock | 23/164 | rapidgzip | 1 | silent_zero | 0 | 1040 |  |
| gz_multiblock | 24/164 | rapidgzip | 0 | silent_zero | 0 | 1040 |  |
| gz_multiblock | 24/164 | rapidgzip | 1 | silent_zero | 0 | 1040 |  |
| gz_multiblock | 25/164 | rapidgzip | 0 | silent_zero | 0 | 1040 |  |
| gz_multiblock | 25/164 | rapidgzip | 1 | silent_zero | 0 | 1040 |  |
| gz_multiblock | 26/164 | rapidgzip | 0 | silent_zero | 0 | 1040 |  |
| gz_multiblock | 26/164 | rapidgzip | 1 | silent_zero | 0 | 1040 |  |
| gz_multiblock | 27/164 | rapidgzip | 0 | silent_zero | 0 | 1040 |  |
| gz_multiblock | 27/164 | rapidgzip | 1 | silent_zero | 0 | 1040 |  |
| gz_multiblock | 28/164 | rapidgzip | 0 | silent_zero | 0 | 1040 |  |
| gz_multiblock | 28/164 | rapidgzip | 1 | silent_zero | 0 | 1040 |  |
| gz_multiblock | 29/164 | rapidgzip | 0 | silent_zero | 0 | 1040 |  |
| gz_multiblock | 29/164 | rapidgzip | 1 | silent_zero | 0 | 1040 |  |
| gz_multiblock | 30/164 | rapidgzip | 0 | silent_zero | 0 | 1040 |  |
| gz_multiblock | 30/164 | rapidgzip | 1 | silent_zero | 0 | 1040 |  |
| gz_multiblock | 31/164 | rapidgzip | 0 | silent_zero | 0 | 1040 |  |
| gz_multiblock | 31/164 | rapidgzip | 1 | silent_zero | 0 | 1040 |  |
| gz_multiblock | 32/164 | rapidgzip | 0 | silent_zero | 0 | 1040 |  |
| gz_multiblock | 32/164 | rapidgzip | 1 | silent_zero | 0 | 1040 |  |
| gz_multiblock | 33/164 | rapidgzip | 0 | silent_zero | 0 | 1040 |  |
| gz_multiblock | 33/164 | rapidgzip | 1 | silent_zero | 0 | 1040 |  |
| gz_multiblock | 34/164 | rapidgzip | 0 | silent_zero | 0 | 1040 |  |
| gz_multiblock | 34/164 | rapidgzip | 1 | silent_zero | 0 | 1040 |  |
| gz_multiblock | 35/164 | rapidgzip | 0 | silent_zero | 0 | 1040 |  |
| gz_multiblock | 35/164 | rapidgzip | 1 | silent_zero | 0 | 1040 |  |
| gz_multiblock | 36/164 | rapidgzip | 0 | silent_zero | 0 | 1040 |  |
| gz_multiblock | 36/164 | rapidgzip | 1 | silent_zero | 0 | 1040 |  |
| gz_multiblock | 37/164 | rapidgzip | 0 | silent_zero | 0 | 1040 |  |
| gz_multiblock | 37/164 | rapidgzip | 1 | silent_zero | 0 | 1040 |  |
| gz_multiblock | 38/164 | rapidgzip | 0 | silent_zero | 0 | 1040 |  |
| gz_multiblock | 38/164 | rapidgzip | 1 | silent_zero | 0 | 1040 |  |
| gz_multiblock | 39/164 | rapidgzip | 0 | silent_zero | 0 | 1040 |  |
| gz_multiblock | 39/164 | rapidgzip | 1 | silent_zero | 0 | 1040 |  |
| gz_multiblock | 40/164 | rapidgzip | 0 | silent_zero | 0 | 1040 |  |
| gz_multiblock | 40/164 | rapidgzip | 1 | silent_zero | 0 | 1040 |  |
| gz_multiblock | 41/164 | rapidgzip | 0 | silent_short | 260 | 1040 |  |
| gz_multiblock | 41/164 | rapidgzip | 1 | silent_short | 260 | 1040 |  |
| gz_multiblock | 42/164 | rapidgzip | 0 | silent_short | 260 | 1040 |  |
| gz_multiblock | 42/164 | rapidgzip | 1 | silent_short | 260 | 1040 |  |
| gz_multiblock | 43/164 | rapidgzip | 0 | silent_short | 260 | 1040 |  |
| gz_multiblock | 43/164 | rapidgzip | 1 | silent_short | 260 | 1040 |  |
| gz_multiblock | 44/164 | rapidgzip | 0 | silent_short | 260 | 1040 |  |
| gz_multiblock | 44/164 | rapidgzip | 1 | silent_short | 260 | 1040 |  |
| gz_multiblock | 45/164 | rapidgzip | 0 | silent_short | 260 | 1040 |  |
| gz_multiblock | 45/164 | rapidgzip | 1 | silent_short | 260 | 1040 |  |
| gz_multiblock | 46/164 | rapidgzip | 0 | silent_short | 260 | 1040 |  |
| gz_multiblock | 46/164 | rapidgzip | 1 | silent_short | 260 | 1040 |  |
| gz_multiblock | 47/164 | rapidgzip | 0 | silent_short | 260 | 1040 |  |
| gz_multiblock | 47/164 | rapidgzip | 1 | silent_short | 260 | 1040 |  |
| gz_multiblock | 48/164 | rapidgzip | 0 | silent_short | 261 | 1040 |  |
| gz_multiblock | 48/164 | rapidgzip | 1 | silent_short | 261 | 1040 |  |
| gz_multiblock | 49/164 | rapidgzip | 0 | silent_short | 262 | 1040 |  |
| gz_multiblock | 49/164 | rapidgzip | 1 | silent_short | 262 | 1040 |  |
| gz_multiblock | 50/164 | rapidgzip | 0 | silent_short | 263 | 1040 |  |
| gz_multiblock | 50/164 | rapidgzip | 1 | silent_short | 263 | 1040 |  |
| gz_multiblock | 51/164 | rapidgzip | 0 | silent_short | 264 | 1040 |  |
| gz_multiblock | 51/164 | rapidgzip | 1 | silent_short | 264 | 1040 |  |
| gz_multiblock | 52/164 | rapidgzip | 0 | silent_short | 265 | 1040 |  |
| gz_multiblock | 52/164 | rapidgzip | 1 | silent_short | 265 | 1040 |  |
| gz_multiblock | 53/164 | rapidgzip | 0 | silent_short | 266 | 1040 |  |
| gz_multiblock | 53/164 | rapidgzip | 1 | silent_short | 266 | 1040 |  |
| gz_multiblock | 54/164 | rapidgzip | 0 | silent_short | 267 | 1040 |  |
| gz_multiblock | 54/164 | rapidgzip | 1 | silent_short | 267 | 1040 |  |
| gz_multiblock | 55/164 | rapidgzip | 0 | silent_short | 268 | 1040 |  |
| gz_multiblock | 55/164 | rapidgzip | 1 | silent_short | 268 | 1040 |  |
| gz_multiblock | 56/164 | rapidgzip | 0 | silent_short | 269 | 1040 |  |
| gz_multiblock | 56/164 | rapidgzip | 1 | silent_short | 269 | 1040 |  |
| gz_multiblock | 57/164 | rapidgzip | 0 | silent_short | 270 | 1040 |  |
| gz_multiblock | 57/164 | rapidgzip | 1 | silent_short | 270 | 1040 |  |
| gz_multiblock | 58/164 | rapidgzip | 0 | silent_short | 271 | 1040 |  |
| gz_multiblock | 58/164 | rapidgzip | 1 | silent_short | 271 | 1040 |  |
| gz_multiblock | 59/164 | rapidgzip | 0 | silent_short | 272 | 1040 |  |
| gz_multiblock | 59/164 | rapidgzip | 1 | silent_short | 272 | 1040 |  |
| gz_multiblock | 60/164 | rapidgzip | 0 | silent_short | 273 | 1040 |  |
| gz_multiblock | 60/164 | rapidgzip | 1 | silent_short | 273 | 1040 |  |
| gz_multiblock | 61/164 | rapidgzip | 0 | silent_short | 274 | 1040 |  |
| gz_multiblock | 61/164 | rapidgzip | 1 | silent_short | 274 | 1040 |  |
| gz_multiblock | 62/164 | rapidgzip | 0 | silent_short | 275 | 1040 |  |
| gz_multiblock | 62/164 | rapidgzip | 1 | silent_short | 275 | 1040 |  |
| gz_multiblock | 63/164 | rapidgzip | 0 | silent_short | 276 | 1040 |  |
| gz_multiblock | 63/164 | rapidgzip | 1 | silent_short | 276 | 1040 |  |
| gz_multiblock | 64/164 | rapidgzip | 0 | silent_short | 277 | 1040 |  |
| gz_multiblock | 64/164 | rapidgzip | 1 | silent_short | 277 | 1040 |  |
| gz_multiblock | 65/164 | rapidgzip | 0 | silent_short | 278 | 1040 |  |
| gz_multiblock | 65/164 | rapidgzip | 1 | silent_short | 278 | 1040 |  |
| gz_multiblock | 66/164 | rapidgzip | 0 | silent_short | 279 | 1040 |  |
| gz_multiblock | 66/164 | rapidgzip | 1 | silent_short | 279 | 1040 |  |
| gz_multiblock | 67/164 | rapidgzip | 0 | silent_short | 280 | 1040 |  |
| gz_multiblock | 67/164 | rapidgzip | 1 | silent_short | 280 | 1040 |  |
| gz_multiblock | 68/164 | rapidgzip | 0 | silent_short | 281 | 1040 |  |
| gz_multiblock | 68/164 | rapidgzip | 1 | silent_short | 281 | 1040 |  |
| gz_multiblock | 69/164 | rapidgzip | 0 | silent_short | 282 | 1040 |  |
| gz_multiblock | 69/164 | rapidgzip | 1 | silent_short | 282 | 1040 |  |
| gz_multiblock | 70/164 | rapidgzip | 0 | silent_short | 283 | 1040 |  |
| gz_multiblock | 70/164 | rapidgzip | 1 | silent_short | 283 | 1040 |  |
| gz_multiblock | 71/164 | rapidgzip | 0 | silent_short | 284 | 1040 |  |
| gz_multiblock | 71/164 | rapidgzip | 1 | silent_short | 284 | 1040 |  |
| gz_multiblock | 72/164 | rapidgzip | 0 | silent_short | 285 | 1040 |  |
| gz_multiblock | 72/164 | rapidgzip | 1 | silent_short | 285 | 1040 |  |
| gz_multiblock | 73/164 | rapidgzip | 0 | silent_short | 286 | 1040 |  |
| gz_multiblock | 73/164 | rapidgzip | 1 | silent_short | 286 | 1040 |  |
| gz_multiblock | 74/164 | rapidgzip | 0 | silent_short | 287 | 1040 |  |
| gz_multiblock | 74/164 | rapidgzip | 1 | silent_short | 287 | 1040 |  |
| gz_multiblock | 75/164 | rapidgzip | 0 | silent_short | 287 | 1040 |  |
| gz_multiblock | 75/164 | rapidgzip | 1 | silent_short | 287 | 1040 |  |
| gz_multiblock | 76/164 | rapidgzip | 0 | silent_short | 520 | 1040 |  |
| gz_multiblock | 76/164 | rapidgzip | 1 | silent_short | 520 | 1040 |  |
| gz_multiblock | 77/164 | rapidgzip | 0 | silent_short | 520 | 1040 |  |
| gz_multiblock | 77/164 | rapidgzip | 1 | silent_short | 520 | 1040 |  |
| gz_multiblock | 78/164 | rapidgzip | 0 | silent_short | 520 | 1040 |  |
| gz_multiblock | 78/164 | rapidgzip | 1 | silent_short | 520 | 1040 |  |
| gz_multiblock | 79/164 | rapidgzip | 0 | silent_short | 520 | 1040 |  |
| gz_multiblock | 79/164 | rapidgzip | 1 | silent_short | 520 | 1040 |  |
| gz_multiblock | 80/164 | rapidgzip | 0 | silent_short | 520 | 1040 |  |
| gz_multiblock | 80/164 | rapidgzip | 1 | silent_short | 520 | 1040 |  |
| gz_multiblock | 81/164 | rapidgzip | 0 | silent_short | 520 | 1040 |  |
| gz_multiblock | 81/164 | rapidgzip | 1 | silent_short | 520 | 1040 |  |
| gz_multiblock | 82/164 | rapidgzip | 0 | silent_short | 520 | 1040 |  |
| gz_multiblock | 82/164 | rapidgzip | 1 | silent_short | 520 | 1040 |  |
| gz_multiblock | 83/164 | rapidgzip | 0 | silent_short | 520 | 1040 |  |
| gz_multiblock | 83/164 | rapidgzip | 1 | silent_short | 520 | 1040 |  |
| gz_multiblock | 84/164 | rapidgzip | 0 | silent_short | 521 | 1040 |  |
| gz_multiblock | 84/164 | rapidgzip | 1 | silent_short | 521 | 1040 |  |
| gz_multiblock | 85/164 | rapidgzip | 0 | silent_short | 522 | 1040 |  |
| gz_multiblock | 85/164 | rapidgzip | 1 | silent_short | 522 | 1040 |  |
| gz_multiblock | 86/164 | rapidgzip | 0 | silent_short | 523 | 1040 |  |
| gz_multiblock | 86/164 | rapidgzip | 1 | silent_short | 523 | 1040 |  |
| gz_multiblock | 87/164 | rapidgzip | 0 | silent_short | 524 | 1040 |  |
| gz_multiblock | 87/164 | rapidgzip | 1 | silent_short | 524 | 1040 |  |
| gz_multiblock | 88/164 | rapidgzip | 0 | silent_short | 525 | 1040 |  |
| gz_multiblock | 88/164 | rapidgzip | 1 | silent_short | 525 | 1040 |  |
| gz_multiblock | 89/164 | rapidgzip | 0 | silent_short | 526 | 1040 |  |
| gz_multiblock | 89/164 | rapidgzip | 1 | silent_short | 526 | 1040 |  |
| gz_multiblock | 90/164 | rapidgzip | 0 | silent_short | 527 | 1040 |  |
| gz_multiblock | 90/164 | rapidgzip | 1 | silent_short | 527 | 1040 |  |
| gz_multiblock | 91/164 | rapidgzip | 0 | silent_short | 528 | 1040 |  |
| gz_multiblock | 91/164 | rapidgzip | 1 | silent_short | 528 | 1040 |  |
| gz_multiblock | 92/164 | rapidgzip | 0 | silent_short | 529 | 1040 |  |
| gz_multiblock | 92/164 | rapidgzip | 1 | silent_short | 529 | 1040 |  |
| gz_multiblock | 93/164 | rapidgzip | 0 | silent_short | 530 | 1040 |  |
| gz_multiblock | 93/164 | rapidgzip | 1 | silent_short | 530 | 1040 |  |
| gz_multiblock | 94/164 | rapidgzip | 0 | silent_short | 531 | 1040 |  |
| gz_multiblock | 94/164 | rapidgzip | 1 | silent_short | 531 | 1040 |  |
| gz_multiblock | 95/164 | rapidgzip | 0 | silent_short | 532 | 1040 |  |
| gz_multiblock | 95/164 | rapidgzip | 1 | silent_short | 532 | 1040 |  |
| gz_multiblock | 96/164 | rapidgzip | 0 | silent_short | 533 | 1040 |  |
| gz_multiblock | 96/164 | rapidgzip | 1 | silent_short | 533 | 1040 |  |
| gz_multiblock | 97/164 | rapidgzip | 0 | silent_short | 534 | 1040 |  |
| gz_multiblock | 97/164 | rapidgzip | 1 | silent_short | 534 | 1040 |  |
| gz_multiblock | 98/164 | rapidgzip | 0 | silent_short | 535 | 1040 |  |
| gz_multiblock | 98/164 | rapidgzip | 1 | silent_short | 535 | 1040 |  |
| gz_multiblock | 99/164 | rapidgzip | 0 | silent_short | 536 | 1040 |  |
| gz_multiblock | 99/164 | rapidgzip | 1 | silent_short | 536 | 1040 |  |
| gz_multiblock | 100/164 | rapidgzip | 0 | silent_short | 537 | 1040 |  |
| gz_multiblock | 100/164 | rapidgzip | 1 | silent_short | 537 | 1040 |  |
| gz_multiblock | 101/164 | rapidgzip | 0 | silent_short | 538 | 1040 |  |
| gz_multiblock | 101/164 | rapidgzip | 1 | silent_short | 538 | 1040 |  |
| gz_multiblock | 102/164 | rapidgzip | 0 | silent_short | 539 | 1040 |  |
| gz_multiblock | 102/164 | rapidgzip | 1 | silent_short | 539 | 1040 |  |
| gz_multiblock | 103/164 | rapidgzip | 0 | silent_short | 540 | 1040 |  |
| gz_multiblock | 103/164 | rapidgzip | 1 | silent_short | 540 | 1040 |  |
| gz_multiblock | 104/164 | rapidgzip | 0 | silent_short | 541 | 1040 |  |
| gz_multiblock | 104/164 | rapidgzip | 1 | silent_short | 541 | 1040 |  |
| gz_multiblock | 105/164 | rapidgzip | 0 | silent_short | 542 | 1040 |  |
| gz_multiblock | 105/164 | rapidgzip | 1 | silent_short | 542 | 1040 |  |
| gz_multiblock | 106/164 | rapidgzip | 0 | silent_short | 543 | 1040 |  |
| gz_multiblock | 106/164 | rapidgzip | 1 | silent_short | 543 | 1040 |  |
| gz_multiblock | 107/164 | rapidgzip | 0 | silent_short | 544 | 1040 |  |
| gz_multiblock | 107/164 | rapidgzip | 1 | silent_short | 544 | 1040 |  |
| gz_multiblock | 108/164 | rapidgzip | 0 | silent_short | 545 | 1040 |  |
| gz_multiblock | 108/164 | rapidgzip | 1 | silent_short | 545 | 1040 |  |
| gz_multiblock | 109/164 | rapidgzip | 0 | silent_short | 546 | 1040 |  |
| gz_multiblock | 109/164 | rapidgzip | 1 | silent_short | 546 | 1040 |  |
| gz_multiblock | 110/164 | rapidgzip | 0 | silent_short | 547 | 1040 |  |
| gz_multiblock | 110/164 | rapidgzip | 1 | silent_short | 547 | 1040 |  |
| gz_multiblock | 111/164 | rapidgzip | 0 | silent_short | 547 | 1040 |  |
| gz_multiblock | 111/164 | rapidgzip | 1 | silent_short | 547 | 1040 |  |
| gz_multiblock | 112/164 | rapidgzip | 0 | silent_short | 780 | 1040 |  |
| gz_multiblock | 112/164 | rapidgzip | 1 | silent_short | 780 | 1040 |  |
| gz_multiblock | 113/164 | rapidgzip | 0 | silent_short | 780 | 1040 |  |
| gz_multiblock | 113/164 | rapidgzip | 1 | silent_short | 780 | 1040 |  |
| gz_multiblock | 114/164 | rapidgzip | 0 | silent_short | 780 | 1040 |  |
| gz_multiblock | 114/164 | rapidgzip | 1 | silent_short | 780 | 1040 |  |
| gz_multiblock | 115/164 | rapidgzip | 0 | silent_short | 780 | 1040 |  |
| gz_multiblock | 115/164 | rapidgzip | 1 | silent_short | 780 | 1040 |  |
| gz_multiblock | 116/164 | rapidgzip | 0 | silent_short | 780 | 1040 |  |
| gz_multiblock | 116/164 | rapidgzip | 1 | silent_short | 780 | 1040 |  |
| gz_multiblock | 117/164 | rapidgzip | 0 | silent_short | 780 | 1040 |  |
| gz_multiblock | 117/164 | rapidgzip | 1 | silent_short | 780 | 1040 |  |
| gz_multiblock | 118/164 | rapidgzip | 0 | silent_short | 780 | 1040 |  |
| gz_multiblock | 118/164 | rapidgzip | 1 | silent_short | 780 | 1040 |  |
| gz_multiblock | 119/164 | rapidgzip | 0 | silent_short | 780 | 1040 |  |
| gz_multiblock | 119/164 | rapidgzip | 1 | silent_short | 780 | 1040 |  |
| gz_multiblock | 120/164 | rapidgzip | 0 | silent_short | 781 | 1040 |  |
| gz_multiblock | 120/164 | rapidgzip | 1 | silent_short | 781 | 1040 |  |
| gz_multiblock | 121/164 | rapidgzip | 0 | silent_short | 782 | 1040 |  |
| gz_multiblock | 121/164 | rapidgzip | 1 | silent_short | 782 | 1040 |  |
| gz_multiblock | 122/164 | rapidgzip | 0 | silent_short | 783 | 1040 |  |
| gz_multiblock | 122/164 | rapidgzip | 1 | silent_short | 783 | 1040 |  |
| gz_multiblock | 123/164 | rapidgzip | 0 | silent_short | 784 | 1040 |  |
| gz_multiblock | 123/164 | rapidgzip | 1 | silent_short | 784 | 1040 |  |
| gz_multiblock | 124/164 | rapidgzip | 0 | silent_short | 785 | 1040 |  |
| gz_multiblock | 124/164 | rapidgzip | 1 | silent_short | 785 | 1040 |  |
| gz_multiblock | 125/164 | rapidgzip | 0 | silent_short | 786 | 1040 |  |
| gz_multiblock | 125/164 | rapidgzip | 1 | silent_short | 786 | 1040 |  |
| gz_multiblock | 126/164 | rapidgzip | 0 | silent_short | 787 | 1040 |  |
| gz_multiblock | 126/164 | rapidgzip | 1 | silent_short | 787 | 1040 |  |
| gz_multiblock | 127/164 | rapidgzip | 0 | silent_short | 788 | 1040 |  |
| gz_multiblock | 127/164 | rapidgzip | 1 | silent_short | 788 | 1040 |  |
| gz_multiblock | 128/164 | rapidgzip | 0 | silent_short | 789 | 1040 |  |
| gz_multiblock | 128/164 | rapidgzip | 1 | silent_short | 789 | 1040 |  |
| gz_multiblock | 129/164 | rapidgzip | 0 | silent_short | 790 | 1040 |  |
| gz_multiblock | 129/164 | rapidgzip | 1 | silent_short | 790 | 1040 |  |
| gz_multiblock | 130/164 | rapidgzip | 0 | silent_short | 791 | 1040 |  |
| gz_multiblock | 130/164 | rapidgzip | 1 | silent_short | 791 | 1040 |  |
| gz_multiblock | 131/164 | rapidgzip | 0 | silent_short | 792 | 1040 |  |
| gz_multiblock | 131/164 | rapidgzip | 1 | silent_short | 792 | 1040 |  |
| gz_multiblock | 132/164 | rapidgzip | 0 | silent_short | 793 | 1040 |  |
| gz_multiblock | 132/164 | rapidgzip | 1 | silent_short | 793 | 1040 |  |
| gz_multiblock | 133/164 | rapidgzip | 0 | silent_short | 794 | 1040 |  |
| gz_multiblock | 133/164 | rapidgzip | 1 | silent_short | 794 | 1040 |  |
| gz_multiblock | 134/164 | rapidgzip | 0 | silent_short | 795 | 1040 |  |
| gz_multiblock | 134/164 | rapidgzip | 1 | silent_short | 795 | 1040 |  |
| gz_multiblock | 135/164 | rapidgzip | 0 | silent_short | 796 | 1040 |  |
| gz_multiblock | 135/164 | rapidgzip | 1 | silent_short | 796 | 1040 |  |
| gz_multiblock | 136/164 | rapidgzip | 0 | silent_short | 797 | 1040 |  |
| gz_multiblock | 136/164 | rapidgzip | 1 | silent_short | 797 | 1040 |  |
| gz_multiblock | 137/164 | rapidgzip | 0 | silent_short | 798 | 1040 |  |
| gz_multiblock | 137/164 | rapidgzip | 1 | silent_short | 798 | 1040 |  |
| gz_multiblock | 138/164 | rapidgzip | 0 | silent_short | 799 | 1040 |  |
| gz_multiblock | 138/164 | rapidgzip | 1 | silent_short | 799 | 1040 |  |
| gz_multiblock | 139/164 | rapidgzip | 0 | silent_short | 800 | 1040 |  |
| gz_multiblock | 139/164 | rapidgzip | 1 | silent_short | 800 | 1040 |  |
| gz_multiblock | 140/164 | rapidgzip | 0 | silent_short | 801 | 1040 |  |
| gz_multiblock | 140/164 | rapidgzip | 1 | silent_short | 801 | 1040 |  |
| gz_multiblock | 141/164 | rapidgzip | 0 | silent_short | 802 | 1040 |  |
| gz_multiblock | 141/164 | rapidgzip | 1 | silent_short | 802 | 1040 |  |
| gz_multiblock | 142/164 | rapidgzip | 0 | silent_short | 803 | 1040 |  |
| gz_multiblock | 142/164 | rapidgzip | 1 | silent_short | 803 | 1040 |  |
| gz_multiblock | 143/164 | rapidgzip | 0 | silent_short | 804 | 1040 |  |
| gz_multiblock | 143/164 | rapidgzip | 1 | silent_short | 804 | 1040 |  |
| gz_multiblock | 144/164 | rapidgzip | 0 | silent_short | 805 | 1040 |  |
| gz_multiblock | 144/164 | rapidgzip | 1 | silent_short | 805 | 1040 |  |
| gz_multiblock | 145/164 | rapidgzip | 0 | silent_short | 806 | 1040 |  |
| gz_multiblock | 145/164 | rapidgzip | 1 | silent_short | 806 | 1040 |  |
| gz_multiblock | 146/164 | rapidgzip | 0 | silent_short | 807 | 1040 |  |
| gz_multiblock | 146/164 | rapidgzip | 1 | silent_short | 807 | 1040 |  |
| gz_multiblock | 147/164 | rapidgzip | 0 | silent_short | 807 | 1040 |  |
| gz_multiblock | 147/164 | rapidgzip | 1 | silent_short | 807 | 1040 |  |
| gz_multimember | 10/83 | rapidgzip | 0 | silent_zero | 0 | 43 |  |
| gz_multimember | 10/83 | rapidgzip | 1 | silent_zero | 0 | 43 |  |
| gz_multimember | 11/83 | rapidgzip | 0 | silent_zero | 0 | 43 |  |
| gz_multimember | 11/83 | rapidgzip | 1 | silent_zero | 0 | 43 |  |
| gz_multimember | 12/83 | rapidgzip | 0 | silent_zero | 0 | 43 |  |
| gz_multimember | 12/83 | rapidgzip | 1 | silent_zero | 0 | 43 |  |
| gz_multimember | 13/83 | rapidgzip | 0 | silent_zero | 0 | 43 |  |
| gz_multimember | 13/83 | rapidgzip | 1 | silent_zero | 0 | 43 |  |
| gz_multimember | 14/83 | rapidgzip | 0 | silent_zero | 0 | 43 |  |
| gz_multimember | 14/83 | rapidgzip | 1 | silent_zero | 0 | 43 |  |
| gz_multimember | 15/83 | rapidgzip | 0 | silent_zero | 0 | 43 |  |
| gz_multimember | 15/83 | rapidgzip | 1 | silent_zero | 0 | 43 |  |
| gz_multimember | 16/83 | rapidgzip | 0 | silent_zero | 0 | 43 |  |
| gz_multimember | 16/83 | rapidgzip | 1 | silent_zero | 0 | 43 |  |
| gz_multimember | 17/83 | rapidgzip | 0 | silent_zero | 0 | 43 |  |
| gz_multimember | 17/83 | rapidgzip | 1 | silent_zero | 0 | 43 |  |
| gz_multimember | 18/83 | rapidgzip | 0 | silent_zero | 0 | 43 |  |
| gz_multimember | 18/83 | rapidgzip | 1 | silent_zero | 0 | 43 |  |
| gz_multimember | 19/83 | rapidgzip | 0 | silent_zero | 0 | 43 |  |
| gz_multimember | 19/83 | rapidgzip | 1 | silent_zero | 0 | 43 |  |
| gz_multimember | 20/83 | rapidgzip | 0 | silent_zero | 0 | 43 |  |
| gz_multimember | 20/83 | rapidgzip | 1 | silent_zero | 0 | 43 |  |
| gz_multimember | 21/83 | rapidgzip | 0 | silent_zero | 0 | 43 |  |
| gz_multimember | 21/83 | rapidgzip | 1 | silent_zero | 0 | 43 |  |
| gz_multimember | 22/83 | rapidgzip | 0 | silent_zero | 0 | 43 |  |
| gz_multimember | 22/83 | rapidgzip | 1 | silent_zero | 0 | 43 |  |
| gz_multimember | 23/83 | rapidgzip | 0 | silent_zero | 0 | 43 |  |
| gz_multimember | 23/83 | rapidgzip | 1 | silent_zero | 0 | 43 |  |
| gz_multimember | 24/83 | rapidgzip | 0 | silent_zero | 0 | 43 |  |
| gz_multimember | 24/83 | rapidgzip | 1 | silent_zero | 0 | 43 |  |
| gz_multimember | 25/83 | rapidgzip | 0 | silent_zero | 0 | 43 |  |
| gz_multimember | 25/83 | rapidgzip | 1 | silent_zero | 0 | 43 |  |
| gz_multimember | 26/83 | rapidgzip | 0 | silent_zero | 0 | 43 |  |
| gz_multimember | 26/83 | rapidgzip | 1 | silent_zero | 0 | 43 |  |
| gz_multimember | 27/83 | rapidgzip | 0 | silent_zero | 0 | 43 |  |
| gz_multimember | 27/83 | rapidgzip | 1 | silent_zero | 0 | 43 |  |
| gz_multimember | 28/83 | rapidgzip | 0 | silent_zero | 0 | 43 |  |
| gz_multimember | 28/83 | rapidgzip | 1 | silent_zero | 0 | 43 |  |
| gz_multimember | 29/83 | rapidgzip | 0 | silent_zero | 0 | 43 |  |
| gz_multimember | 29/83 | rapidgzip | 1 | silent_zero | 0 | 43 |  |
| gz_multimember | 38/83 | rapidgzip | 0 | silent_short | 18 | 43 |  |
| gz_multimember | 38/83 | rapidgzip | 1 | silent_short | 18 | 43 |  |
| gz_multimember | 48/83 | rapidgzip | 0 | silent_short | 18 | 43 |  |
| gz_multimember | 48/83 | rapidgzip | 1 | silent_short | 18 | 43 |  |
| gz_multimember | 49/83 | rapidgzip | 0 | silent_short | 18 | 43 |  |
| gz_multimember | 49/83 | rapidgzip | 1 | silent_short | 18 | 43 |  |
| gz_multimember | 50/83 | rapidgzip | 0 | silent_short | 19 | 43 |  |
| gz_multimember | 50/83 | rapidgzip | 1 | silent_short | 19 | 43 |  |
| gz_multimember | 51/83 | rapidgzip | 0 | silent_short | 20 | 43 |  |
| gz_multimember | 51/83 | rapidgzip | 1 | silent_short | 20 | 43 |  |
| gz_multimember | 52/83 | rapidgzip | 0 | silent_short | 21 | 43 |  |
| gz_multimember | 52/83 | rapidgzip | 1 | silent_short | 21 | 43 |  |
| gz_multimember | 53/83 | rapidgzip | 0 | silent_short | 22 | 43 |  |
| gz_multimember | 53/83 | rapidgzip | 1 | silent_short | 22 | 43 |  |
| gz_multimember | 54/83 | rapidgzip | 0 | silent_short | 23 | 43 |  |
| gz_multimember | 54/83 | rapidgzip | 1 | silent_short | 23 | 43 |  |
| gz_multimember | 55/83 | rapidgzip | 0 | silent_short | 24 | 43 |  |
| gz_multimember | 55/83 | rapidgzip | 1 | silent_short | 24 | 43 |  |
| gz_multimember | 56/83 | rapidgzip | 0 | silent_short | 25 | 43 |  |
| gz_multimember | 56/83 | rapidgzip | 1 | silent_short | 25 | 43 |  |
| gz_multimember | 57/83 | rapidgzip | 0 | silent_short | 26 | 43 |  |
| gz_multimember | 57/83 | rapidgzip | 1 | silent_short | 26 | 43 |  |
| gz_multimember | 58/83 | rapidgzip | 0 | silent_short | 27 | 43 |  |
| gz_multimember | 58/83 | rapidgzip | 1 | silent_short | 27 | 43 |  |
| gz_multimember | 59/83 | rapidgzip | 0 | silent_short | 28 | 43 |  |
| gz_multimember | 59/83 | rapidgzip | 1 | silent_short | 28 | 43 |  |
| gz_multimember | 60/83 | rapidgzip | 0 | silent_short | 29 | 43 |  |
| gz_multimember | 60/83 | rapidgzip | 1 | silent_short | 29 | 43 |  |
| gz_multimember | 61/83 | rapidgzip | 0 | silent_short | 30 | 43 |  |
| gz_multimember | 61/83 | rapidgzip | 1 | silent_short | 30 | 43 |  |
| gz_multimember | 62/83 | rapidgzip | 0 | silent_short | 31 | 43 |  |
| gz_multimember | 62/83 | rapidgzip | 1 | silent_short | 31 | 43 |  |
| gz_multimember | 63/83 | rapidgzip | 0 | silent_short | 32 | 43 |  |
| gz_multimember | 63/83 | rapidgzip | 1 | silent_short | 32 | 43 |  |
| gz_multimember | 64/83 | rapidgzip | 0 | silent_short | 33 | 43 |  |
| gz_multimember | 64/83 | rapidgzip | 1 | silent_short | 33 | 43 |  |
| gz_multimember | 65/83 | rapidgzip | 0 | silent_short | 34 | 43 |  |
| gz_multimember | 65/83 | rapidgzip | 1 | silent_short | 34 | 43 |  |
| gz_multimember | 66/83 | rapidgzip | 0 | silent_short | 35 | 43 |  |
| gz_multimember | 66/83 | rapidgzip | 1 | silent_short | 35 | 43 |  |
| gz_multimember | 67/83 | rapidgzip | 0 | silent_short | 36 | 43 |  |
| gz_multimember | 67/83 | rapidgzip | 1 | silent_short | 36 | 43 |  |
| gz_multimember | 68/83 | rapidgzip | 0 | silent_short | 37 | 43 |  |
| gz_multimember | 68/83 | rapidgzip | 1 | silent_short | 37 | 43 |  |
| gz_multimember | 69/83 | rapidgzip | 0 | silent_short | 38 | 43 |  |
| gz_multimember | 69/83 | rapidgzip | 1 | silent_short | 38 | 43 |  |
| gz_multimember | 70/83 | rapidgzip | 0 | silent_short | 39 | 43 |  |
| gz_multimember | 70/83 | rapidgzip | 1 | silent_short | 39 | 43 |  |
| gz_multimember | 71/83 | rapidgzip | 0 | silent_short | 40 | 43 |  |
| gz_multimember | 71/83 | rapidgzip | 1 | silent_short | 40 | 43 |  |
| gz_multimember | 72/83 | rapidgzip | 0 | silent_short | 41 | 43 |  |
| gz_multimember | 72/83 | rapidgzip | 1 | silent_short | 41 | 43 |  |
| gz_multimember | 73/83 | rapidgzip | 0 | silent_short | 42 | 43 |  |
| gz_multimember | 73/83 | rapidgzip | 1 | silent_short | 42 | 43 |  |
| gz_header_only_10 | 10/10 | rapidgzip | 0 | silent_zero | 0 | None |  |
| gz_header_only_10 | 10/10 | rapidgzip | 1 | silent_zero | 0 | None |  |
| gz_header_plus_1 | 10/11 | rapidgzip | 0 | silent_zero | 0 | None |  |
| gz_header_plus_1 | 10/11 | rapidgzip | 1 | silent_zero | 0 | None |  |
| gz_header_plus_1 | 11/11 | rapidgzip | 0 | silent_zero | 0 | None |  |
| gz_header_plus_1 | 11/11 | rapidgzip | 1 | silent_zero | 0 | None |  |
| gz_header_plus_8 | 10/18 | rapidgzip | 0 | silent_zero | 0 | None |  |
| gz_header_plus_8 | 10/18 | rapidgzip | 1 | silent_zero | 0 | None |  |
| gz_header_plus_8 | 11/18 | rapidgzip | 0 | silent_zero | 0 | None |  |
| gz_header_plus_8 | 11/18 | rapidgzip | 1 | silent_zero | 0 | None |  |
| gz_header_plus_8 | 12/18 | rapidgzip | 0 | silent_zero | 0 | None |  |
| gz_header_plus_8 | 12/18 | rapidgzip | 1 | silent_zero | 0 | None |  |
| gz_header_plus_8 | 13/18 | rapidgzip | 0 | silent_zero | 0 | None |  |
| gz_header_plus_8 | 13/18 | rapidgzip | 1 | silent_zero | 0 | None |  |
| gz_header_plus_8 | 14/18 | rapidgzip | 0 | silent_zero | 0 | None |  |
| gz_header_plus_8 | 14/18 | rapidgzip | 1 | silent_zero | 0 | None |  |
| bz_empty | 0/14 | indexed_bzip2 | 0 | silent_zero | 0 | 0 |  |
| bz_empty | 0/14 | indexed_bzip2 | 1 | silent_zero | 0 | 0 |  |
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
| bz_tiny | 0/37 | indexed_bzip2 | 1 | silent_zero | 0 | 1 |  |
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
| bz_small | 0/48 | indexed_bzip2 | 1 | silent_zero | 0 | 11 |  |
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
| bz_medium | 0/92 | indexed_bzip2 | 1 | silent_zero | 0 | 1040 |  |
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
| bz_large | 0/156 | indexed_bzip2 | 1 | silent_zero | 0 | 36000 |  |
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
| 11 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
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
| 11 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 12 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 17 | raise:EOFError | raise:RuntimeError |  |
| 18 | raise:EOFError | raise:RuntimeError |  |
| 19 | raise:EOFError | raise:RuntimeError |  |
| 20 | raise:EOFError | raise:RuntimeError |  |
| 21 | full | full |  |
| … | _10 cuts omitted_ | | |

### gz_small

| cut | stdlib | rapidgzip/par=0 | notes |
| ---: | --- | --- | --- |
| 0 | **silent_zero**(len=0) | raise:ValueError | diff (silent_zero vs raise) |
| 1 | raise:BadGzipFile | raise:ValueError |  |
| 2 | raise:EOFError | raise:ValueError |  |
| 9 | raise:EOFError | raise:ValueError |  |
| 10 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 11 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 12 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 13 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 14 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 15 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 16 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 17 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 18 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 19 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 20 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 21 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 22 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 29 | raise:EOFError | raise:RuntimeError |  |
| 30 | raise:EOFError | raise:RuntimeError |  |
| 31 | full | full |  |
| … | _12 cuts omitted_ | | |

### gz_medium

| cut | stdlib | rapidgzip/par=0 | notes |
| ---: | --- | --- | --- |
| 0 | **silent_zero**(len=0) | raise:ValueError | diff (silent_zero vs raise) |
| 1 | raise:BadGzipFile | raise:ValueError |  |
| 2 | raise:EOFError | raise:ValueError |  |
| 9 | raise:EOFError | raise:ValueError |  |
| 10 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 11 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 12 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 13 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 14 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 15 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 16 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 17 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 18 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 19 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 20 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 21 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 22 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 23 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 24 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 25 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 26 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 27 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 28 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 29 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 30 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 31 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 32 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 33 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 34 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 35 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 36 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 37 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 38 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 39 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 40 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 41 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 42 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 43 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 44 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 45 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| … | _16 cuts omitted_ | | |

### gz_large

| cut | stdlib | rapidgzip/par=0 | notes |
| ---: | --- | --- | --- |
| 0 | **silent_zero**(len=0) | raise:ValueError | diff (silent_zero vs raise) |
| 1 | raise:BadGzipFile | raise:ValueError |  |
| 2 | raise:EOFError | raise:ValueError |  |
| 9 | raise:EOFError | raise:ValueError |  |
| 10 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 11 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 12 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 13 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 14 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 15 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 16 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 17 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 18 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 19 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 20 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 21 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 22 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 23 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 24 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 25 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 26 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 27 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 28 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 29 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 30 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 31 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 32 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 33 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 34 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 35 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 36 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 37 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 38 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 39 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 40 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 41 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 42 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 43 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 44 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 45 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| … | _148 cuts omitted_ | | |

### gz_multiblock

| cut | stdlib | rapidgzip/par=0 | notes |
| ---: | --- | --- | --- |
| 0 | **silent_zero**(len=0) | raise:ValueError | diff (silent_zero vs raise) |
| 1 | raise:BadGzipFile | raise:ValueError |  |
| 2 | raise:EOFError | raise:ValueError |  |
| 9 | raise:EOFError | raise:ValueError |  |
| 10 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 11 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 12 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 13 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 14 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 15 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 16 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 17 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 18 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 19 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 20 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 21 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 22 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 23 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 24 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 25 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 26 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 27 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 28 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 29 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 30 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 31 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 32 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 33 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 34 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 35 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 36 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 37 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 38 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 39 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 40 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 41 | raise:EOFError | **silent_short**(len=260) | SILENT vs stdlib raise |
| 42 | raise:EOFError | **silent_short**(len=260) | SILENT vs stdlib raise |
| 43 | raise:EOFError | **silent_short**(len=260) | SILENT vs stdlib raise |
| 44 | raise:EOFError | **silent_short**(len=260) | SILENT vs stdlib raise |
| 45 | raise:EOFError | **silent_short**(len=260) | SILENT vs stdlib raise |
| … | _125 cuts omitted_ | | |

### gz_multimember

| cut | stdlib | rapidgzip/par=0 | notes |
| ---: | --- | --- | --- |
| 0 | **silent_zero**(len=0) | raise:ValueError | diff (silent_zero vs raise) |
| 1 | raise:BadGzipFile | raise:ValueError |  |
| 2 | raise:EOFError | raise:ValueError |  |
| 9 | raise:EOFError | raise:ValueError |  |
| 10 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 11 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 12 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 13 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 14 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 15 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 16 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 17 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 18 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 19 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 20 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 21 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 22 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 23 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 24 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 25 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 26 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 27 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 28 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 29 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 38 | **silent_short**(len=18) | **silent_short**(len=18) |  |
| 48 | raise:EOFError | **silent_short**(len=18) | SILENT vs stdlib raise |
| 49 | raise:EOFError | **silent_short**(len=18) | SILENT vs stdlib raise |
| 50 | raise:EOFError | **silent_short**(len=19) | SILENT vs stdlib raise |
| 51 | raise:EOFError | **silent_short**(len=20) | SILENT vs stdlib raise |
| 52 | raise:EOFError | **silent_short**(len=21) | SILENT vs stdlib raise |
| 53 | raise:EOFError | **silent_short**(len=22) | SILENT vs stdlib raise |
| 54 | raise:EOFError | **silent_short**(len=23) | SILENT vs stdlib raise |
| 55 | raise:EOFError | **silent_short**(len=24) | SILENT vs stdlib raise |
| 56 | raise:EOFError | **silent_short**(len=25) | SILENT vs stdlib raise |
| 57 | raise:EOFError | **silent_short**(len=26) | SILENT vs stdlib raise |
| 58 | raise:EOFError | **silent_short**(len=27) | SILENT vs stdlib raise |
| 59 | raise:EOFError | **silent_short**(len=28) | SILENT vs stdlib raise |
| 60 | raise:EOFError | **silent_short**(len=29) | SILENT vs stdlib raise |
| 61 | raise:EOFError | **silent_short**(len=30) | SILENT vs stdlib raise |
| 62 | raise:EOFError | **silent_short**(len=31) | SILENT vs stdlib raise |
| … | _44 cuts omitted_ | | |

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
| 11 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| … | _6 cuts omitted_ | | |

### gz_header_plus_8

| cut | stdlib | rapidgzip/par=0 | notes |
| ---: | --- | --- | --- |
| 0 | **silent_zero**(len=0) | raise:ValueError | diff (silent_zero vs raise) |
| 1 | raise:BadGzipFile | raise:ValueError |  |
| 2 | raise:EOFError | raise:ValueError |  |
| 9 | raise:EOFError | raise:ValueError |  |
| 10 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 11 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 12 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 13 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 14 | raise:EOFError | **silent_zero**(len=0) | SILENT vs stdlib raise |
| 16 | raise:error | raise:RuntimeError |  |
| 17 | raise:error | raise:RuntimeError |  |
| 18 | raise:error | raise:RuntimeError |  |
| … | _7 cuts omitted_ | | |

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

