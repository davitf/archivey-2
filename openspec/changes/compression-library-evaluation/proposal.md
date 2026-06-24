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
  [`python-xz`](https://github.com/rogdham/python-xz), which appears to do the same
  thing (block-index random access). We *think* an own-vs-`python-xz` exploration happened,
  but it is **not recorded**, and `python-xz` is still pinned in the `[all]` extra while
  being **entirely unused** (no `src/` import, not even referenced by the dev test oracle).
  `pyzstd` is likewise pinned in `[all]` but unused in `src/` (only the dev test oracle uses
  it, to *generate* zstd fixtures).

So we have undocumented decisions, at least one intended migration, an unexplored
seekable-zstd option, and dead dependencies. This change does the systematic comparison,
**records it in a doc**, decides per codec, and cleans up the dependency list.

## What Changes

This is **investigation + a decision doc + dependency cleanup**. Actual library swaps
(e.g. `zstandard` → `pyzstd`, or adding `indexed_zstd` for seekable zstd) land in their
own follow-up changes once decided — but the decisions and their rationale are fixed here.

### 1. A per-format library analysis doc

Add **`docs/library-analysis.md`** (rendered in the MkDocs site) — the single source of
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
- **xz:** record *why* we maintain our own `xz.py` instead of `python-xz` (or decide to
  switch). Either way, resolve the dangling `python-xz` dependency.
- **everything else:** record the chosen library + the rejected alternatives + the reason,
  so future contributors don't re-litigate (e.g. why `uncompresspy` for `.Z`, why
  `rapidgzip` for both gzip and bzip2 random access — the macOS single-accelerator
  constraint is already in `docs/known-issues.md` and should be cross-linked).

### 3. Dependency cleanup

- Remove `python-xz` and `pyzstd` from the `[all]` extra **if** confirmed unused (they are,
  in `src/`), or wire them up if the evaluation says to keep them. Whatever the outcome,
  `[all]` should contain no dependency the library never imports.
- Ensure the `[zstd]` extra points at whatever the evaluation selects (and add an extra for
  a seekable-zstd backend if one is adopted).

## Specs

Proposed deltas (kept here until accepted). This change is primarily a doc + packaging
decision; the spec touchpoints are light.

### packaging-and-extras — MODIFIED Requirement: Optional extras map to exactly the libraries the code uses

The extras→capability mapping SHALL list only libraries the library actually imports; an
extra MUST NOT pin a dependency that no code path uses. The per-codec library choice and
its rationale SHALL be recorded in `docs/library-analysis.md`, which is the source of truth
for why each library is used or rejected.

#### Scenario: no dead optional dependency

- **WHEN** the `[all]` extra (or any extra) is audited against `src/` imports
- **THEN** every pinned package is reachable from some code path, or it is removed

### documentation — ADDED Requirement: Library choices are documented

The documentation SHALL include a per-format compression-library analysis that, for each
codec, names the chosen library, the alternatives considered, and the criteria
(non-seekable support, efficient seeking, corruption/truncation detection, error reporting,
install/availability, maintenance) behind the decision.

> The concrete backend-selection requirements in `compressed-streams` /
> `seekable-decompressor-streams` are updated by the **follow-up** changes that implement
> any chosen swap (e.g. zstd backend migration, seekable-zstd support); this change records
> the decisions and criteria, not the swaps.

## Impact

- **New doc:** `docs/library-analysis.md` (+ a nav entry in `mkdocs.yml`); cross-links from
  `docs/known-issues.md` and `COMPARISON.md` (which already mentions the seekable-stream
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
