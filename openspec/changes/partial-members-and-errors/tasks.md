## 1. Public types and ABC

- [ ] 1.1 Add frozen `MemberListReport` (`members`, `error`, `diagnostics`) with
      sequence ergonomics mirroring `ExtractionReport`; export from public surface
- [ ] 1.2 Add `ArchiveReader.list_members() -> MemberListReport` to the ABC and
      `BaseArchiveReader` skeleton

## 2. Materialization: incomplete vs complete (N1)

- [ ] 2.1 Teach RA materialization to retain a recoverable prefix on terminal
      archive-level listing errors without publishing `_members_cache` as complete
- [ ] 2.2 Identity-stamp recovered members (`member in reader`); allow
      `open(member)` by identity; keep `members()` / `scan_members()` /
      `get(name)` complete-or-raise; `get_members_if_available()` stays `None`
      until a successful complete materialization
- [ ] 2.3 Implement `list_members()` to always return `MemberListReport` for
      terminal archive listing errors; keep `ResourceLimitError` raise-only

## 3. Yield-then-raise alignment (option 7)

- [ ] 3.1 Change RA `__iter__` / `stream_members` so terminal archive-level
      listing errors yield the recovered prefix then raise (match streaming)
- [ ] 3.2 Confirm RA `extract_all` extract-prep remains fail-closed (no partial
      writes) on those errors
- [ ] 3.3 Confirm streaming `__iter__` / `stream_members` / `list_members` /
      `scan_members` contracts match the delta specs (report vs raise)

## 4. TAR / Option F first consumer

- [ ] 4.1 Wire TAR rejected-header / strict absent-short trailer paths through
      the shared incomplete + report + yield-then-raise machinery
- [ ] 4.2 Add/adjust unit tests for TAR RA and streaming: `list_members` report,
      `members()` raise, `__iter__` yield-then-raise, no complete cache publish,
      `open` on recovered member

## 5. CLI and docs

- [ ] 5.1 Switch CLI `list` to `list_members()`: print recovered members; stderr
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
