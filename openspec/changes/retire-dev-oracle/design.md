# Design — retire-dev-oracle

## Context

PLAN.md's test strategy (step 2) called for cloning DEV's suite into
`tests/_dev_oracle/` as a running regression gate, to be deleted in Phase 10 once every
spec scenario is covered by the new suite. In practice the gate never ran: the drivers
bind to v1 APIs, pytest excludes the tree entirely (`norecursedirs`), and the tree holds
two divergent copies (an adaptation attempt at the top level, the raw DEV clone under
`archivey/`). The durable assets identified by the plan — the *declarative corpus*
(`sample_archives.py` archive shapes + `ArchiveContents`/`FileInfo` expectations) and the
oracle *libraries* (py7zr/rarfile/CLIs for Phase 7 cross-validation) — are separable from
the dead drivers. v2's own corpus is a small fraction of DEV's, so deleting the tree
without porting first would lose real coverage (multi-member layouts, unicode/encoding
names, links, duplicates, per-format quirks).

## Goals / Non-Goals

**Goals:**

- v2's declarative corpus covers every DEV corpus shape relevant to the implemented
  formats; 7z/RAR shapes are carried as inactive entries ready for Phase 7.
- A single corpus-driven conformance sweep asserts open/list/extract-or-documented-error
  for the whole corpus, replacing the oracle's intended role with something that runs.
- `tests/_dev_oracle/` and its exclusion configuration are gone; PLAN.md and
  CONTRIBUTING.md tell the truth about the strategy.

**Non-Goals:**

- Not the Phase 7 oracle *cross-validation* (py7zr/rarfile comparisons) — those specs and
  dev-group dependencies stay untouched.
- Not a committed-binary fixture corpus: generation-on-demand + cache remains the model
  (`testing-contract`'s no-committed-binaries rule; the few hand-crafted committed
  fixtures with JSON sidecars stay as they are).
- Not fixing backend bugs the sweep may uncover — those become their own changes
  (the sweep may land with targeted xfails referencing them).

## Decisions

- **Port shapes, not code.** DEV's `sample_archives.py` is read as a catalog; each shape
  is re-expressed in v2's corpus idiom (v2's dataclasses/builders), not copied verbatim —
  DEV's file also carries v1-specific creation plumbing we don't want.
- **One parametrized sweep module** (`tests/test_corpus_sweep.py`): for each corpus
  entry whose format is implemented → generate (cached), `open_archive`, compare the
  member listing against the declared expectations, `extract_all` to a tmp dir under
  default safety policy, and verify file contents; entries declaring an expected error
  assert that error type instead. Skips (not failures) when an optional dependency for
  that entry's format/codec is absent — same rule as the rest of the suite.
- **7z/RAR corpus entries land disabled** (a `requires_format_implemented` guard keyed
  off the registry), so Phase 7 flips them on without re-porting.
- **Delete both `_dev_oracle` copies in one commit** with the config cleanup, so the tree
  and its special-casing disappear together; DEV remains reachable as an external
  reference repo per CLAUDE.md.

## Risks / Trade-offs

- **Coverage gap window**: none in practice — the oracle contributes zero signal today,
  so every ported shape is a strict gain.
- **Sweep runtime**: the full corpus generates once per environment (cached under
  `.pytest_cache/archivey-archives/`); the sweep itself is I/O-light. If generation cost
  grows noticeable in CI, entries can be tiered (small default set, full set on a flag).
- **Losing DEV context**: deleting the in-repo clone removes easy diffing. Mitigated by
  the port task explicitly recording, in the corpus module docstring, the DEV commit hash
  the shapes were taken from.
