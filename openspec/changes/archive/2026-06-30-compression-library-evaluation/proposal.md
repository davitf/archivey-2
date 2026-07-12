# Per-format compression-library evaluation + a recorded decision doc

## Why

We picked a concrete library (or rolled our own) for each codec, but **the rationale is
not written down anywhere**, and a couple of the choices are now in doubt:

- **zstd uses `zstandard`.** It is the one codec with a known wart — it cannot seek
  backwards, so we wrap it in `_ZstdReopenStream` (reopen-from-start), and it is loose
  about surfacing some error conditions. The maintainer intends to **migrate to
  `pyzstd`**, which wraps libzstd more directly and is the spirit of Python 3.14's stdlib
  `compression.zstd`. There are several candidates to weigh:
  - `zstandard` (current), `pyzstd`, the **stdlib `compression.zstd`** (3.14+),
    `backports.zstd` ([Rogdham](https://github.com/Rogdham/backports.zstd)) for older
    Pythons, and **`indexed_zstd`** ([martinellimarco](https://github.com/martinellimarco/indexed_zstd),
    used by ratarmount, same author lineage as rapidgzip/indexed_bzip2) for *efficient
    seeking*.
  - `pyzstd` ships a `SeekableZstdFile` class, but it only handles the **Seekable Zstd
    format** (not arbitrary `.zst`), and pyzstd's own docs nudge toward the stdlib /
    `backports.zstd`; it is unclear whether those support efficient seeking at all.
- **xz uses our own parser** (`internal/streams/xz.py`) rather than
  [`python-xz`](https://github.com/rogdham/python-xz). This decision **was** made and is
  recorded — in the DEV repo, [`davitf/archivey-dev#214`](https://github.com/davitf/archivey-dev/pull/214)
  ("Implement XzDecompressorStream with block-level seeking"): the native parser (on stdlib
  `lzma`) gives block-level random access via the XZ stream index, an efficient `SEEK_END`
  via a backwards index scan, correct **multi-stream** handling (a case the prior path got
  wrong), per-stream fallback scanning for truncated/index-less files, and architectural
  uniformity with `LzipDecompressorStream`. DEV kept `python-xz` only as a *disabled-by-default
  comparison/benchmark* backend. v2 carried over the native parser **but not** that
  comparison backend, so the `python-xz` pin in `[all]` is now **entirely unused** (no `src/`
  import, not even referenced by the dev test oracle). The decision just needs to be carried
  into *this* repo's spec/docs (and the dead pin resolved). `pyzstd` is likewise pinned in
  `[all]` but unused in `src/` (only the dev test oracle uses it, to *generate* zstd fixtures).

So we have undocumented decisions, at least one intended migration, an unexplored
seekable-zstd option, and dead dependencies. This change does the systematic comparison,
**records it in a doc**, decides per codec, and cleans up the dependency list.

## What Changes

This is **investigation + a decision doc + dependency cleanup**. Actual library swaps
(e.g. `zstandard` → `pyzstd`, or adding `indexed_zstd` for seekable zstd) land in their
own follow-up changes once decided — but the decisions and their rationale are fixed here.

### 1. A per-format library analysis doc

Add **`docs/internal/library-analysis.md`** (rendered in the MkDocs site) — the single source of
truth for "which library backs each codec and why". For every codec the library reads, a
row per candidate library scored on:

| Criterion | What it captures |
|-----------|------------------|
| Non-seekable source | Works on a pipe/socket (forward-only) without a fileno? |
| Efficient seeking | Indexed/random access without re-decompressing from the start? |
| Corruption detection | Raises on bad data (vs. silently returning garbage)? |
| Truncation detection | Raises on a short/cut stream (vs. silent short read)? |
| Error reporting fidelity | Are errors distinguishable + translatable to our `CorruptionError`/`TruncatedError`? |
| Install / availability | Pure-Python vs. native wheels; platform/arch coverage; build deps |
| Maintenance | Activity, releases, Python-version support |
| Notes | Quirks (e.g. zstandard's no-backward-seek; rapidgzip/indexed_bzip2 macOS dual-load) |

Codecs to cover: gzip (stdlib, `rapidgzip`), bzip2 (stdlib, `rapidgzip.IndexedBzip2File`),
xz/lzma (stdlib `lzma`, our `xz.py`, `python-xz`), lzip (our `lzip.py`), zstd (`zstandard`,
`pyzstd`, stdlib `compression.zstd`, `backports.zstd`, `indexed_zstd`), lz4 (`lz4`), brotli
(`brotli`), unix-compress (`uncompresspy`), deflate64 (`inflate64`), ppmd (`pyppmd`).

### 2. Decisions to reach (and record)

- **zstd:** choose the primary decode backend (validate or revise the `zstandard` →
  `pyzstd` intent) and decide the seekable-zstd story (`indexed_zstd` vs. pyzstd's
  `SeekableZstdFile` vs. none), noting the trade-off that `SeekableZstdFile` only reads the
  Seekable Zstd container, not arbitrary `.zst`. Capture how the choice interacts with the
  3.14 stdlib so we can prefer the stdlib when available.
- **xz:** carry the already-made decision (DEV [`#214`](https://github.com/davitf/archivey-dev/pull/214))
  into this repo's docs/spec — own `xz.py` for block-index random access, efficient
  `SEEK_END`, correct multi-stream handling, and `DecompressorStream` uniformity — and
  resolve the dangling `python-xz` `[all]` pin (v2 doesn't keep it even as a comparison
  backend, so it should be removed).
- **everything else:** record the chosen library + the rejected alternatives + the reason,
  so future contributors don't re-litigate (e.g. why `uncompresspy` for `.Z`, why
  `rapidgzip` for both gzip and bzip2 random access — the macOS single-accelerator
  constraint is already in `docs/internal/known-issues.md` and should be cross-linked).

### 3. Dependency cleanup

User-facing extras must not pin a library only `src/` *doesn't* use, and **test-only**
libraries (decode oracles `rarfile`/`py7zr`; fixture generators `ncompress`, and `pyzstd`
while it only *writes* fixtures) belong in the `dev` dependency group, not in an extra.
Concretely:

- `python-xz` — imported nowhere (not `src/`, not the tests): **remove** from `[all]`.
- `pyzstd` — used only by the dev test oracle to generate zstd fixtures: **move** from
  `[all]` to the `dev` group (or, if the evaluation promotes it to the runtime zstd backend,
  to the `[zstd]` extra).
- Ensure the `[zstd]` extra points at whatever the evaluation selects (and add an extra for
  a seekable-zstd backend if one is adopted).
- Add a guard so a user-facing extra can never again pin a package no `src/` code imports.

## Specs

The full delta requirements (with scenarios) live in this change's `specs/` directory:

- `specs/packaging-and-extras/spec.md` — **MODIFIED** optional extras map to exactly the libraries the code uses (drop the dead `python-xz` / `pyzstd` pins).
- `specs/documentation/spec.md` — **ADDED** per-format compression-library choices are documented in `docs/internal/library-analysis.md`, citing already-recorded decisions (e.g. [`davitf/archivey-dev#214`](https://github.com/davitf/archivey-dev/pull/214) for XZ).

This change is primarily a doc + packaging decision; the spec touchpoints are light. The
concrete backend-selection requirements in `compressed-streams` /
`seekable-decompressor-streams` are updated by the **follow-up** changes that implement any
chosen swap (zstd migration, seekable-zstd support) — this change records the decisions and
criteria, not the swaps.

## Impact

- **New doc:** `docs/internal/library-analysis.md` (+ a nav entry in `mkdocs.yml`); cross-links from
  `docs/internal/known-issues.md` and `COMPARISON.md` (which already mentions the seekable-stream
  subsystem).
- **Packaging:** `pyproject.toml` `[all]` / `[zstd]` extras adjusted per the findings
  (drop dead `python-xz` / `pyzstd`, or justify keeping).
- **Follow-up changes (not built here):** a zstd backend migration (`zstandard` → chosen
  primary), optional seekable-zstd support (`indexed_zstd` / `SeekableZstdFile`), and any
  xz backend change — each its own proposal once this evaluation lands.
- **Coordinates with:** `codec-descriptor-refactor` — the descriptor's `open`/`translate`/
  `requirement` are where a chosen swap is ultimately wired.
- **Risk:** low — this records decisions and removes dead deps; behavior changes are
  deferred to the follow-up swap changes, each independently testable against the existing
  codec/seekable-stream suites.
