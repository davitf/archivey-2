## 1. Public types and ABC

- [ ] 1.1 Add frozen `MemberListReport` (`members`, `error`, `diagnostics`) with
      sequence ergonomics mirroring `ExtractionReport`; export from public surface
- [ ] 1.2 Add `ArchiveReader.members_report() -> MemberListReport` to the ABC and
      `BaseArchiveReader` skeleton
- [ ] 1.3 Rename `get_members_if_available` → `members_report_if_available`
      everywhere: ABC + `base_reader.py`, `directory_reader.py`, CLI
      (`test_cmd`/`extract_cmd`), `extraction.py`, the base specs that name it
      (`archive-reading`, `access-mode-and-cost`, `reader-concurrency`,
      `format-tar`, `format-directory`, `safe-extraction`), and tests
      (`test_reader_contract`, `test_directory`, `test_single_file`, `test_tar`,
      `test_zip`, `test_listing_limits`). Leave `get(name)` untouched (distinct
      Mapping-style lookup)

## 2. Materialization: one stored report (N1)

- [ ] 2.1 Store materialization as a single `MemberListReport` (completeness is
      `error is None`) behind an internal holder that carries the name index, so
      publication is one immutable-reference store (drop the two-write
      `_members_cache` order discipline); terminal archive-level damage populates
      `error`, while `ResourceLimitError` / interrupt-class exceptions propagate
      and leave the reader unmaterialized
- [ ] 2.2 Identity-stamp recovered members (`member in reader`); allow
      `open(member)` by identity; derive `members()` / `scan_members()` /
      `get(name)` from the stored report (raise `error` if set) so they stay
      complete-or-raise
- [ ] 2.3 Implement `members_report()` (return the stored report; replay on
      repeat calls) and change `members_report_if_available()` to
      `-> MemberListReport | None` (stored report complete-or-incomplete / upfront
      index / `None`, never scanning); keep `ResourceLimitError` raise-only

## 3. Yield-then-raise alignment (option 7)

- [ ] 3.1 Change RA `__iter__` / `stream_members` so terminal archive-level
      listing errors yield the recovered prefix then raise (match streaming)
- [ ] 3.2 Confirm RA `extract_all` extract-prep remains fail-closed (no partial
      writes) on those errors
- [ ] 3.3 Confirm streaming `__iter__` / `stream_members` / `members_report` /
      `scan_members` contracts match the delta specs (report vs raise)

## 4. TAR / Option F first consumer

- [ ] 4.1 Wire TAR rejected-header / strict absent-short trailer paths through
      the shared incomplete + report + yield-then-raise machinery
- [ ] 4.2 Add/adjust unit tests for TAR RA and streaming: `members_report()`
      fields, `members()` raise, `__iter__` yield-then-raise, no complete cache
      publish, `open` on recovered member

## 5. CLI and docs

- [ ] 5.1 Switch CLI `list` to `members_report()`: print recovered members; stderr
      + exit `1` when `error` is set
- [ ] 5.2 Document dual listing contract in `docs/usage.md` / Gotchas / API notes
      per documentation delta
- [ ] 5.3 Record Q7 decision in `review/api-coherence/QUESTIONS.md` and
      cross-link `review/backlog.md` / `STATUS.md` to this change

## 6. Verify

- [ ] 6.1 Targeted pytest for report / yield-then-raise / CLI list honesty
      (TAR fixtures); three-config not required until apply lands on main path
- [ ] 6.2 `openspec validate --strict partial-members-and-errors`
- [ ] 6.3 `uv run --no-sync ruff format` / `ruff check` / pyrefly + ty clean on
      touched Python
