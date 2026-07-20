# Rapidgzip truncation characterization

Canonical measurement + recommendation record for this change. Raw tables live under
`results/`; the sweep tool is `scripts/rapidgzip_truncation_sweep.py`. §2 stack is
**locked** (empty→stdlib + single-member ISIZE). Upstream soft-EOF design:
`docs/internal/rapidgzip-upstream-report.md`.

**Platforms:** Linux x86_64 (local + CI), macOS arm64 (CI), Windows amd64 (CI) —
rapidgzip 0.16.0 / Python 3.11.  
**Script:** `scripts/rapidgzip_truncation_sweep.py`  
**CI:** `.github/workflows/rapidgzip-truncation-sweep.yml`  
**Raw data:** [`results/`](results/) (`linux-x86_64.*`, `macos-arm64.*`,
`windows-amd64.*`, `linux-x86_64-ci.summary.txt`)

## Method

- Path sources only (avoids upstream Bug 3 / Python file-object `terminate()`).
- Fresh subprocess per cut, 5 s wall-clock timeout (no hangs observed).
- Backends: stdlib `gzip`/`bz2` (oracle) vs `rapidgzip.open` / `IndexedBzip2File`.
- `parallelization=0` (archivey’s setting) and `=1` for dependence check.
- Fixtures: empty / tiny / small / medium / large / multi-block (`Z_FULL_FLUSH`) /
  multi-member / bare 10-byte header / header+1 / header+8; every byte offset
  (all fixtures ≤ 256 B compressed).

## Headline (gzip)

**On Linux and Windows the silent set is wide — not “header-only / ~10 bytes.”**
**On macOS arm64 it collapses almost to header-only (cut=10) — still not empty.**

### Cross-platform CI (task 1.3) — par=0 only, same fixtures

| Platform | rapidgzip silent_zero | silent_short | raise | full | timeouts/crashes |
| --- | ---: | ---: | ---: | ---: | ---: |
| Linux x86_64 (CI) | 283 | 134 | 177 | 16 | 0 / 0 |
| Windows amd64 (CI) | 283 | 134 | 177 | 16 | 0 / 0 |
| macOS arm64 (CI) | 10 | 1 | 592 | 7 | 0 / 0 |

Windows matches Linux exactly on this matrix. macOS raises on nearly all mid-body
cuts that Linux/Windows leave silent; its silent∩stdlib-raise set is **11 cuts**,
all also silent on Linux:

- Every fixture at **cut=10** (header-only / first byte after header) → silent_zero
- `gz_multimember` cut=38 → silent_short (len=18) — also silent on Linux; ISIZE
  multi-member bailout still applies (sum deferred)

Likely cause: different inflate backends (Archivey’s codec notes already distinguish
Linux ISA-L vs non-ISA-L macOS error shapes). Soft EOF remains upstream design;
macOS simply surfaces more paths as exceptions in 0.16.0.

**Implication for the locked stack:** empty→stdlib + single-member ISIZE is still
required for Linux/Windows (load-bearing) and still covers macOS’s residual
header-only silence. Do not special-case “macOS needs no backstop.”

## Headline detail (Linux / Windows gzip)

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

On many silent cuts rapidgzip may print to **stderr**
(`Unexpected end of file when getting block…`). Upstream research
(`UPSTREAM_TRUNCATION_REPORT.md`) shows that line is emitted on a **rethrow**
path (typically trailer-adjacent raise/abort), **not** as a reliable companion to
silent-empty success on 0.16.0 — **do not** build detection on capturing stderr.
Valid empty vs header-only trunc still differ on `tell_compressed` (160 vs 0).

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

Follow-up (2026-07-20), corrected after the maintainer noted that `readall()`
hides streaming behaviour.

### Correction: `readall()` vs sized / bytewise reads

The first probe used a single `f.read()` / `readall()`. That **mis-characterized
stdlib**: `GzipFile.read()` with no size keeps going until the gzip EOF/CRC
trailer; on a truncated member that call **raises `EOFError` and returns no
value to the caller**, even when zlib had already produced decompressed bytes.

A `read(1)` / `read(n)` loop **does** stream those bytes (seekable `BytesIO`
or a non-seekable file object), pulling compressed input as needed, then raises
on a later read when the member is incomplete.

So: stdlib **does** read the input as needed and **can** yield a correct
partial prefix — truncation is still **loud** (eventual `EOFError`).
rapidgzip’s defect is not “emits data stdlib wouldn’t”; it is “often **stays
silent** at EOF instead of raising,” and on single-block mid-cuts often emits
**nothing** where sized stdlib reads already returned a prefix.

### Single-block (`gzip.compress`) — large 280 KB example

| Cut | stdlib `readall` | stdlib `read(1)` loop | rapidgzip `read` / `read(1)` |
| --- | --- | --- | --- |
| after 10 | RAISE, got 0 | RAISE, got 0 | silent `len=0` |
| after 100 | RAISE, got 0 | **got 5733 correct prefix**, then RAISE | silent `len=0` |
| 50% | RAISE, got 0 | **got ~127509 correct prefix**, then RAISE | silent `len=0` |
| 100 before end | RAISE, got 0 | **got ~249285 correct prefix**, then RAISE | silent `len=0` |
| 1 before end | RAISE, got 0 | **got full 280000**, then RAISE (bad trailer) | RAISE |
| full | full exact | full exact | full exact |

Same on a small file: e.g. cut 29/59 → stdlib `read(1)` got **18** correct
bytes then RAISE; rapidgzip silent `len=0`.

### Multi-block (`Z_FULL_FLUSH`)

| Cut | stdlib `read(1)` | rapidgzip |
| --- | --- | --- |
| after 10 | RAISE, empty | silent empty |
| after 100 | correct prefix (~5733) then RAISE | silent empty |
| 50% | correct prefix **140000** then RAISE | correct prefix **140000**, **no raise** |
| ~100 before end | correct prefix then RAISE | prefix / or **abort** (`std::logic_error`) |
| trailer stripped | full prefix then RAISE | **full exact**, **no raise** |
| 1 before end | RAISE | RAISE |

When either library returned data in this probe, it was a **byte-correct
prefix** (no wrong-byte garbage observed).

**Caveat:** some mid/late multi-block rapidgzip cuts **abort the process**
(`terminate` / `std::logic_error`) rather than raising into Python — separate
from silent truncation; out of band for the ISIZE-backstop decision.

### Takeaway for the backstop

- stdlib: partial output is normal on sized/streaming reads; **truncation is
  still signaled** with `EOFError`.
- rapidgzip: may return empty or a correct short/full prefix and **not raise** —
  that missing signal is what the ISIZE / incompleteness backstop must cover.
- Measuring only `readall()` makes stdlib look like “never returns partial
  data”; that is an API artifact, not zlib/gzip behaviour.

## Refined recommendation (post depth-probe) — awaiting lock-in

Priorities (maintainer): (1) no silent success, (2) recover partial data,
(3) seekability on good inputs.

### Should we refine? **Yes.**

The earlier “extend ISIZE only” recommendation optimizes for (1) but **throws
away (2)** on the common silent-empty path: ISIZE raises `TruncatedError`
after rapidgzip already returned `b""`, with no recovered prefix. stdlib
sized-reads show that prefix was often available.

### Empty→stdlib fallback: possible? worthwhile? what does it miss?

**Possible.** On first rapidgzip EOF with **zero bytes delivered**, reopen the
same path with stdlib and drain via sized reads:

| Input | rapidgzip | stdlib after fallback | Result |
| --- | --- | --- | --- |
| Valid empty gzip | `len=0`, no err | `len=0`, no err | OK — no false positive |
| Header-only / mid single-block trunc | silent `len=0` | prefix (maybe 0) then `EOFError` | **(1)+(2)** |
| Multi-block silent **short** (e.g. 50%) | `len=140000`, no err | n/a — fallback never triggers | **MISS** |
| Trailer-stripped silent **full** | `len=full`, no err | n/a | **MISS** |
| rapidgzip raise / abort | err / crash | n/a | already loud / separate crash issue |

So empty-fallback is **worthwhile for the dominant silent-empty case** (ordinary
`gzip.compress` single-block files) and gives (2) “for free,” but it is **not
sufficient alone** — multi-block / trailer-strip silent non-empty still need a
length (ISIZE) or CRC check for (1).

### DIY limited seek via trailer / reverse block scan?

**Not with the gzip format as specified (RFC 1952).**

A gzip member is `header | deflate blocks | CRC32(4) + ISIZE(4)`. The trailer
is integrity/size metadata for that member, **not** a block offset table.
Deflate blocks are bit-aligned and the 32 KiB LZ77 window **crosses** block
boundaries, so you cannot resume at an arbitrary block from the trailer the
way xz/lzip indexes or `.Z` CLEAR points allow.

What Archivey already has without rapidgzip: stdlib `GzipFile.seek` =
**O(n) re-decompress from start** + rewind warning — same “limited seek”
pattern as zstd/lz4/brotli. Building a real gzip seek index means a
forward `zran`-style pass (sync points + window dictionaries) — that is
reinventing what rapidgzip already does, not a cheap trailer walk.

**Do not** pursue reverse deflate-boundary scanning for seek or truncation;
it is unreliable on arbitrary gzip and the wrong tool vs ISIZE/CRC + stdlib.

### Recommended stack (locked 2026-07-20)

Keep rapidgzip for (3) with **`parallelization=0` (all cores — intentional)**.
Compose two correctness layers for (1)+(2):

1. **Empty→stdlib fallback** when rapidgzip hits EOF having delivered 0 bytes
   (path sources; wall-clock / translate stdlib `EOFError`). Covers silent-empty;
   preserves valid empty; recovers partial data.
2. **Keep/extend single-member ISIZE backstop** when rapidgzip delivered **any**
   bytes then hit EOF without error: compare length (and fix the `< 18` hole).
   Covers silent short/full that empty-fallback misses.
3. **Multi-member ISIZE sum deferred.** Locating further members is a forward
   `1f 8b 08` scan with false-header risk (same ambiguity as today’s
   “further magic ⇒ accept” bailout and as rapidgzip’s speculative decode).
   Keep that conservative false-negative rule for now.
4. **Do not** parse stderr; **do not** trust `block_offsets_complete` / `size`;
   **do not** use `tell_compressed` bit offsets as a completeness heuristic
   (e.g. “160” is fixture-specific, not a general empty-gzip constant).
5. Soft EOF is upstream **design** — document in
   `docs/internal/rapidgzip-upstream-report.md`; do not file as a bug.
6. AUTO/`gzip_isize_backstop` coupling: keep as verifiability signal.

Upstream confirmation (`UPSTREAM_TRUNCATION_REPORT.md`): soft EOF is **by design**
(speculative parallel decode); exceptions/stderr/`block_offsets_complete` are
insufficient; `parallelization=0` means all cores.
