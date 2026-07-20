# Linux characterization results — rapidgzip truncation

**Platform:** Linux x86_64 (Python 3.11 / rapidgzip 0.16.0)  
**Script:** `scripts/rapidgzip_truncation_sweep.py`  
**Raw data:** [`results/linux-x86_64.md`](results/linux-x86_64.md), [`results/linux-x86_64.json`](results/linux-x86_64.json)  
**macOS / Windows:** not run here (task 1.3) — same script is portable; CI or local follow-up.

## Method

- Path sources only (avoids upstream Bug 3 / Python file-object `terminate()`).
- Fresh subprocess per cut, 5 s wall-clock timeout (no hangs observed).
- Backends: stdlib `gzip`/`bz2` (oracle) vs `rapidgzip.open` / `IndexedBzip2File`.
- `parallelization=0` (archivey’s setting) and `=1` for dependence check.
- Fixtures: empty / tiny / small / medium / large / multi-block (`Z_FULL_FLUSH`) /
  multi-member / bare 10-byte header / header+1 / header+8; every byte offset
  (all fixtures ≤ 256 B compressed).

## Headline (gzip)

**The silent set is wide — not “header-only / ~10 bytes.”**

For a typical single-member gzip, rapidgzip’s behaviour by cut offset is:

| Region | Outcome |
| --- | --- |
| cut 0..9 (no full header) | **raises** |
| cut 10 .. ~(size − trailer) | **silent_zero** (`read()` → `b""`, no exception) — stdlib **raises** `EOFError` |
| last ~8 bytes (partial/bad trailer) | **raises** (`RuntimeError` / `std::exception`, etc.) |
| full file | **full** (matches stdlib) |

`parallelization` **0 vs 1: identical** gzip outcomes on this matrix.

### Curated silent set (rapidgzip silent ∩ stdlib raises)

**416 cuts** across all gzip fixtures (par=0). By fixture:

| Fixture | Size | Silent cuts (rgz ∩ stdlib raise) | Shape |
| --- | ---: | --- | --- |
| `gz_empty` | 20 | 10..11 | silent_zero |
| `gz_tiny` | 21 | 10..12 | silent_zero |
| `gz_small` | 31 | 10..22 | silent_zero |
| `gz_medium` | 55 | 10..46 | silent_zero |
| `gz_large` | 187 | 10..178 | silent_zero |
| `gz_multiblock` | 164 | 10..147 | silent_zero then silent_short (partial blocks) |
| `gz_multimember` | 83 | 10..29, 38, 48..73 | silent_zero / silent_short |
| `gz_header_only_10` | 10 | 10 | silent_zero (maintainer case) |
| `gz_header_plus_1` | 11 | 10..11 | silent_zero |
| `gz_header_plus_8` | 18 | 10..14 | silent_zero |

Additionally, rapidgzip can return the **full** payload from a file that is still
truncated (deflate body complete, trailer incomplete) while stdlib raises:

- `gz_multiblock` cuts 148..155 (missing 9..16 bytes) → `out_len == expected`
- `gz_multimember` cut 74 (missing 9 bytes) → full 43-byte payload

So truncation is not only “empty read” — trailer stripping can look like success.

### stderr vs exception

On many silent cuts rapidgzip prints to **stderr**
(`Unexpected end of file when getting block at 10 B …`) but still returns
`b""` to Python. That matches the maintainer’s `10 OK 0` note with a stderr
line — the Python API stayed silent even when C++ logged EOF.

## Current `_GzipTruncationCheckStream` coverage (same silent ∩ raise set)

Simulated against the 416 silent∩raise cuts:

| Backstop result | Count | Notes |
| --- | ---: | --- |
| Would raise (catch) | **337** | ISIZE mismatch, no extra `1f 8b 08` |
| Miss: file `< 18` bytes | **53** | Early return in `_verify_not_truncated` — **includes header-only 10** |
| Miss: multi-member bailout | **26** | Further gzip header ⇒ do not raise |
| Miss: ISIZE coincidentally matches | **0** | In this matrix only |

Archivey smoke check (`GzipCodec` + `AcceleratorMode.ON`, path source):

- cuts `< 18` with silent_zero → **SILENT_LEAK** (backstop skipped)
- mid cuts `≥ 18` with silent_zero → **TruncatedError** (backstop works)
- near-end cuts → rapidgzip raises (translation is the reader boundary’s job)
- valid full files → OK

## bzip2 (`IndexedBzip2File`)

| `parallelization` | Silent where stdlib raises |
| --- | --- |
| **0** (archivey) | cuts **0..9** on non-empty fixtures (14 on `bz_empty` 0..13) — empty `read()`, no exception |
| **1** | essentially only cut **0** (empty path) |

No mid-stream silent-short region like gzip. No hang/crash. **Do not invent an
ISIZE twin** for bzip2 from this matrix; document the short-prefix silent-empty
behaviour (container CRC still covers archive members). Optional follow-up:
treat “empty read from a non-empty `.bz2` path under accel” as truncation — out
of scope unless product wants parity with stdlib on bare `.bz2`.

## Recommendation for §2 (not locked — for maintainer review)

### Prefer **extend** the length backstop (tasks 2.2 + parts of 2.1), not remove

| Option | Verdict |
| --- | --- |
| **2.3 Remove** | **Reject.** Silent set is the common mid-stream case, not a tiny special-case. |
| **2.1 Narrow only** (header-only / ~10 B) | **Reject as sole fix.** Would drop protection for hundreds of silent mid-cuts the current ISIZE check already catches when `size ≥ 18`. |
| **2.2 Extend** | **Recommend.** Keep ISIZE (or equivalent length) compare; fix known holes. |

### Concrete shape to implement after lock-in

1. **Keep** sequential-EOF ISIZE compare for seekable **path** gzip (single-member).
2. **Close the `< 18` hole:** if rapidgzip returns EOF with a gzip magic present and
   the file is too short for a valid member (or decompressed length is 0 while the
   compressed path is non-empty / incomplete), raise `TruncatedError` — this is the
   only place a *tiny* special-case is still needed on top of ISIZE.
3. **Multi-member:** replace “any further `1f 8b 08` ⇒ accept” with an explicit
   **sum of per-member ISIZE** (walk members on an independent handle) with a rule
   that never false-positives on valid concatenated gzip (accept only when the sum
   matches `total % 2³²` **and** the member walk itself succeeds / is well-formed).
4. **AUTO:** keep `gzip_isize_backstop` (or a renamed “truncation_verifiable”
   flag) so bare `.gz` AUTO eligibility is not lost when the heuristic is refined.
5. **Do not** remove `_GzipTruncationCheckStream` until (1)–(3) are in place; then
   rename/simplify if the class becomes a thin “EOF length audit.”

### What this means for the delta spec

The delta’s “backstop **only** for characterized silent cases” is still right —
but the characterized set is **“valid header + incomplete stream”**, not
header-only. Spec text should say that explicitly when §3 lands.

## Reproducing

```bash
uv run --extra seekable python scripts/rapidgzip_truncation_sweep.py \
  --parallelization 0,1 \
  --json-out openspec/changes/rapidgzip-truncation-investigation/results/\$HOST.json \
  --md-out openspec/changes/rapidgzip-truncation-investigation/results/\$HOST.md
```

For macOS arm64 / Windows CI: same command; compare
`summary.silent_accelerator_cases` and the curated “rgz silent ∩ stdlib raise”
table above.

## Depth probe: how much data each returns (and is it correct?)

Follow-up question (2026-07-20): at specific cut points, how much uncompressed
output does stdlib vs rapidgzip produce, and is it a correct prefix of the true
payload?

**stdlib `gzip`:** on every truncated cut in this probe, **raises `EOFError`**
and returns no data. It does **not** hand back a partial prefix. (Empty input
`cut=0` is the odd case: `ok len=0`.)

**rapidgzip** depends on whether the member has one deflate block or many.

### Single-block files (`gzip.compress` — the common case)

Small (108 B → 59 B gz) and large (280 KB → 908 B gz) behave the same:

| Cut | stdlib | rapidgzip | Correctness |
| --- | --- | --- | --- |
| after header (10) | RAISE | `len=0` | empty (not a prefix of real data — just silence) |
| after 18 | RAISE | `len=0` | empty |
| after 100 (large) / N/A clamped (small) | RAISE | `len=0` | empty |
| 50% | RAISE | `len=0` | empty |
| 100 before end (large) | RAISE | `len=0` | empty |
| 1 before end | RAISE | RAISE (`RuntimeError`) | — |
| full | full exact | full exact | OK |

Denser %-sample on the large single-block file: rapidgzip stays at **`len=0`**
from ~5% through ~99%, then **raises** in the last ~0.5% (partial trailer),
then **full** at 100%. It never emits a partial correct prefix for a
single-block member — the incomplete block yields silence, not streaming output.

### Multi-block file (`Z_FULL_FLUSH` ≈ 16 blocks, 280 KB → 2140 B gz)

Here rapidgzip **does** return growing correct prefixes once whole blocks are
present. stdlib still always raises on truncation.

| Cut | stdlib | rapidgzip | Correctness |
| --- | --- | --- | --- |
| after 10 / 18 / 100 | RAISE | `len=0` | empty |
| 50% (cut 1070) | RAISE | `len=140000` | **correct prefix** (exactly 50% of payload) |
| ~100 before end | RAISE | often **crash** (`std::logic_error` / abort) or correct short prefix | mixed — see below |
| 1 before end | RAISE | RAISE | — |
| body complete, trailer stripped (miss 9..16 B) | RAISE | `len=280000` | **FULL exact** while stdlib still raises |
| full | full exact | full exact | OK |

When rapidgzip returns data on a truncated multi-block file, every sampled
non-crash point was a **byte-correct prefix** of the true payload (sha256 of
`expected[:out_len]` matched). No wrong-byte outputs observed in this probe.

**Caveat:** some mid/late multi-block cuts **abort the process**
(`terminate` / `std::logic_error`, rc −6) rather than raising into Python.
That is a separate reliability defect from silent truncation (path source,
so not Bug 3); worth tracking but out of band for the ISIZE-backstop decision.

### Takeaway for the backstop

- For ordinary single-block gzip, “silent” means **empty success**, not
  “partial correct data.” An ISIZE / incompleteness check is about detecting
  that false EOF, not about discarding bad bytes.
- For multi-block (or multi-member) input, rapidgzip can return a **correct
  but incomplete** prefix — or even a **full** payload with a missing trailer.
  Length comparison against declared/trailer size is what catches those;
  content corruption of the emitted prefix was not seen here.
