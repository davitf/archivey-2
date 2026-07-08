# Tasks — retire-dev-oracle

> Order matters: the corpus gap analysis (1) scopes the port (2); the sweep (3) proves
> the ported corpus before the deletion (4) removes the reference material; docs (5) last.

## 1. Corpus gap analysis

- [x] 1.1 Catalog every archive shape in `tests/_dev_oracle/sample_archives.py` (and the
      `archivey/` copy where it differs): format, member layout, names/encodings, links,
      duplicates, emptiness, metadata quirks, expected-failure cases. Record the DEV
      commit hash.
- [x] 1.2 Diff the catalog against v2's `tests/sample_archives.py`; produce the ported
      shape list (implemented formats now; 7z/RAR shapes flagged for Phase 7 activation).
      Drop shapes that are v1-API artifacts rather than archive properties.

## 2. Corpus port

- [x] 2.1 Extend v2's corpus dataclasses/builders as needed (expected-error entries,
      per-entry required-dependency markers, format-implemented guard) without breaking
      existing corpus consumers.
- [x] 2.2 Port the missing shapes for ZIP, TAR (+ compressed variants), single-file
      compressors, ISO, and directory, with expected contents; verify generation-on-demand
      + cache works for each (no committed binaries; `git status` clean after a run).
- [x] 2.3 Add the 7z/RAR shapes as inactive entries behind the registry-driven guard.

## 3. Conformance sweep

- [x] 3.1 `tests/test_corpus_sweep.py`: one parametrized driver — open, compare listing
      to expectations, extract to tmp under default policy, verify contents; expected-
      failure entries assert the documented error type; optional-dependency skips.
- [x] 3.2 Run the sweep across the three dependency legs; convert any backend bugs it
      surfaces into their own tracked changes (targeted xfail with a reference, not a fix
      here).

## 4. Oracle deletion

- [x] 4.1 Delete `tests/_dev_oracle/` (both copies) and remove the `norecursedirs`,
      ruff, and type-checker exclusion entries referencing it.
- [x] 4.2 Full gates: pytest (three legs), ruff, pyrefly, ty; `openspec validate --all`.

## 5. Documentation sync

- [x] 5.1 Rewrite PLAN.md's test-strategy step 2/4 (frozen-oracle narrative and the
      Phase 10 deletion task) to the corpus-sweep model; trim the Phase 10 task list
      accordingly.
- [x] 5.2 Update CONTRIBUTING.md (tool-exclusion note) and CLAUDE.md's `archivey-dev`
      section (the DEV repo stays the external reference; in-repo clone is gone).
- [x] 5.3 Sync the `testing-contract` delta into `openspec/specs/` and archive this
      change.
