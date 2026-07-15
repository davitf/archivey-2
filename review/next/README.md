# Next deep reviews — briefs

The `review/` deep review was done at **PR #73** (round-2 fixes in **#75**),
baseline `0f4254d`, ~16.5k LOC. The tree is now at **#111**, ~22k LOC — ~40 PRs
and +5.5k lines later. Much of the old report is either **closed** or, in two
places, **overturned by a later refactor**. These briefs commission three fresh,
*separate* deep dives on surfaces that either did not exist at the old baseline or
were substantially rewritten after it. Separate (not one omnibus) on purpose: each
is meant to let the model go deep on one hostile-input / correctness surface.

Read this file first, then the individual brief. Each brief is written to be handed
to a fresh frontier model with the repo checked out.

## The three reviews

| # | Brief | Surface | Why now |
|---|-------|---------|---------|
| 1 | `01-rar-reader.md` | Native RAR reader (`rar_parser`, `rar_reader`, `rar_unrar`) | Brand-new hostile-input parser (RAR3+RAR5), native encrypted-header decryption, `unrar`-pipe data path, `-ver` members. Largest unreviewed attack surface; direct analogue of the 7z parser that gave the last review its richest findings. |
| 2 | `02-crypto.md` | Every native decryption / verification path (`zip_aes`, `crypto`, `zipcrypto`, RAR header decryption, `hashing/blake2sp`) | ~5 native crypto primitives, **none** of which existed at the last baseline. Highest consequence-per-bug in the tree (silent acceptance of tampered data). Cross-cuts formats, so reviewed as one adversarial pass rather than per-backend. |
| 3 | `03-stream-decoder-layer.md` | Seekable decoder layer post-refactor + accelerators + vendored LZW (`decompressor_stream`, `decompress`/`xz`/`lzip`/`unix_compress`, `codecs` accelerator path) | The layer the old review told the team to *not* touch was collapsed (#96) and extended onto the hot path (#105 rapidgzip on deflate/zlib, #89 vendored LZW in core, #88 LZMA-Alone). Hunt correctness in the new shape — do **not** re-litigate the refactor. |

## What the old review already settled — do NOT re-report these

Closed since #73; a re-review that resurfaces them is wasting budget:

- **ZIP `member.hashes` / dedupe parity** (old finding #3) → landed in **#104**
  (stored digests + native BLAKE2sp).
- **Benchmark gate / the O(n²) solid-block re-decode trap** (#5) → landed in
  **#100 / #111** (structural CI gate + nightly wall drift + human report).
- **Case-insensitive / Unicode-normalizing FS collisions** (#8) → settled +
  implemented in **#109** (cross-platform name safety, O2/O3/O4/O7 + RENAME).
- **Unbounded 7z `num_files` OOM + listing bombs** (#1) → **#82 / #83**
  (`ListingLimits` / `ResourceLimitError`).
- **Atheris fuzz gate** (recommended by the review) → **#78–#81**, now covering RAR.

## Two old conclusions that were overturned — do NOT restore them

- `deep-simplification.md` argued "the half-size version does not exist" and
  protected the codec/stream layer. **#96** then collapsed the whole
  `DecompressorStream → SegmentedDecompressorStream → per-codec` hierarchy into one
  stream + a `Decoder` strategy. The `decompressor-stream-composition` design.md
  records this explicitly: *"review/complexity.md blessed `SegmentedDecompressorStream`
  as 'correct abstraction — don't touch.' The maintainer has reviewed that note …
  and judged it stale."* Brief 3 reviews the **result**, not whether to do it.
- The 7z parser the review called clean was restructured in **#93**
  (`sevenzip_methods` + `sevenzip_pipeline` split, two-phase parse, one method
  registry). Treat that as the current shape; see `2026-07-14-refactor-sevenzip-reader/design.md`.

## Still-open from the old review (fold into the relevant brief, don't re-derive)

- `deep-simplification.md` **S1/S2/S3** (one error boundary; one member-list
  pipeline; one pass driver) were deferred, not done. Not a review target here, but
  brief 1 should check whether the RAR reader re-implements the S1/S3 patterns
  (per-site translate/stamp, its own pass loop) rather than sharing them.
- Single-file stored-digest surfacing (gzip/lzip trailer CRC) — roadmap follow-up,
  relevant to brief 3's single-file/`.Z` path.

## Access notes (no prior-session transcripts exist)

The only artifacts from the earlier reviews are what got committed: the `review/`
briefs, and the OpenSpec `design.md` files under
`openspec/changes/archive/` for the two refactors. There are no chat transcripts to
consult — cite the committed docs.

## Conventions every brief inherits

**Baseline first.** Capture a green baseline before hunting, and note it in the
writeup (tests passed/skipped, coverage, `pyrefly`, `ty`, `ruff`). The `openspec`
CLI is not preinstalled: `npm install -g @fission-ai/openspec` (see `CLAUDE.md`).

**Three dependency configs.** Behaviour changes by both presence and version of
optional libs. The exact commands are in `CONTRIBUTING.md` → "Before pushing":
`[all]`, `[all-lowest]` (`--resolution lowest-direct`), and the zero-dep
`[core-only]` leg. A crypto/accelerator finding that only reproduces in one config
is still a finding — say which config.

**VISION is the tie-breaker.** Rank findings against the load-bearing claims:
(1) uniform interface + honest cost signals, (2) parse untrusted archives without
native-code parser attack surface / memory-safe hostile-input parsing, (3) damaged
input is a first-class citizen (truncation → recoverable members + honest error),
(4) the ≤1.3× stdlib perf budget. A bug that undercuts a marketing claim outranks a
same-severity bug that doesn't.

**Error contract.** `CONTRIBUTING.md`: every raw library/`OSError` crossing the
boundary is translated to the `ArchiveyError` tree; unrecognized exceptions
propagate raw (no catch-all); `ArchiveyUsageError` sits deliberately outside the
tree. Flag any new path that swallows, mistranslates, or over-catches.

**Deliverable shape** (mirror `review/`): a `SUMMARY.md` (headline + top findings
table with severity + where + status), theme files as needed, a `QUESTIONS.md` for
maintainer decisions, and — importantly — a "**where I disagree / what is actually
fine**" section. Findings must be traced from code (`file:line`), not inferred from
names. Behaviour-focused: a finding worth fixing should come with the concrete
input/state that triggers it. Pause and ask rather than silently resolving a
spec/design discrepancy (`CLAUDE.md`).
