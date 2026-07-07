# Phase 5 tasks — public API finalization

> Order matters: the config object (1) unblocks strict_eof relocation (its tests) and
> extraction limits; the password model (2) and multi-source (3) are independent of each
> other; the link-resolution sweep (5) is independent of 1–4; the finalization sweep (6)
> and sync (7) run last.

## 1. ArchiveyConfig / ExtractionLimits

- [x] 1.1 Define public `ExtractionLimits` and `ArchiveyConfig` frozen dataclasses
      (fields per design.md), exported from `archivey`; module default constant.
- [x] 1.2 `open_archive(..., config: ArchiveyConfig | None = None)`; the reader stores
      its config; internal `StreamConfig` becomes a view derived from
      `ArchiveyConfig` + the access mode (`streaming` stays derived, not public).
- [x] 1.3 Relocate `strict_eof` → `config.strict_archive_eof`; **remove** the
      `open_archive(strict_eof=)` keyword and the `ReadBackend.open_read` parameter
      (readers read it from their config). Update TAR backend + tests.
- [x] 1.4 Tests: default config equivalence; accelerator modes honored via config
      (monkeypatched sentinels); `strict_archive_eof=True` raises / default warns;
      frozen-ness (`dataclasses.FrozenInstanceError` on mutation).
- [x] 1.5 Per-call `limits: ExtractionLimits | None = None` on `extract()` /
      `extract_all()`; **remove** the four loose bomb-limit kwargs; precedence
      per-call > `config.extraction_limits` > default; `ExtractionLimits.UNLIMITED`
      preset. Tests: override tightens/loosens one call; reader-config limits apply
      when no per-call override; UNLIMITED disables all four guards.
- [x] 1.6 `format-tar` spec references updated via the new delta (`strict_eof` →
      `config.strict_archive_eof`); `SPEC.md` §"truncation detection" wording follows.

## 2. Password candidates and provider

- [x] 2.1 Widen `password` typing on `open_archive` (`str | bytes | Sequence[str |
      bytes] | PasswordProvider | None`); define the frozen `PasswordRequest`
      dataclass (`member: ArchiveMember | None`, `attempt: int`); normalize once into
      an internal `_PasswordCandidates` helper on `BaseArchiveReader` (known-good list
      + remaining candidates + optional provider; `provider(PasswordRequest(...))`).
- [x] 2.2 Wire the ZIP backend (the only current consumer) through the helper: try
      known-good → candidates → provider per encrypted member; successful password
      appended to known-good; exhaustion → `EncryptionError`. Encrypted-symlink
      listing path uses the same resolution.
- [x] 2.3 Tests: sequence across two differently-encrypted members in one pass;
      provider called once per *new* password (call-count assertion) and receives a
      `PasswordRequest` carrying the member; `attempt` increments on a wrong-password
      retry for the same unit; provider `None` → `EncryptionError`; header-level
      request carries `member=None` (unit-test the helper; the real header-encrypted
      case lands with Phase 7).
- [x] 2.4 Docs: candidate-order note ("most likely password first" — 7z key derivation
      is deliberately expensive) in the open_archive docstring.

## 3. Multi-source input

- [x] 3.1 Accept `Sequence[str | Path | BinaryIO]` in `open_archive` (length-1 ≡
      single source); detection runs on the first volume; origin-normalize each
      seekable stream.
- [x] 3.2 Single-path volume-set discovery: `.7z.NNN` / `.partN.rar` / `.rNN` name
      patterns → sibling scan in the path's directory, natural order (path sources
      only; a helper the Phase 7 readers reuse).
- [x] 3.3 Phase-5 behavior: a resolved multi-source open raises
      `UnsupportedFeatureError` with a format-appropriate message (7z/RAR: "lands in
      Phase 7"; others: "not a multi-volume format"). Tests for both messages and
      for discovery ordering (fixture files, no real volume parsing).
- [x] 3.4 `extract()` parity: widen its `source` union to the same `Sequence[...]`
      form (it delegates to `open_archive`) and add the `encoding` keyword; test a
      one-shot extract of a non-UTF-8-named TAR with an explicit `encoding`.

## 4. MemberSelector collection form

- [ ] 4.1 Normalizer: `Collection[str | ArchiveMember]` → predicate (name set matches
      all duplicates; `ArchiveMember` entries matched by `_archive_id` + `member_id`
      id-set; mixed collections fine); wire into `stream_members` (and the
      `extract_all(members=)` path when 4b lands).
- [ ] 4.2 Update the `MemberSelector` alias + docstring in `reader.py` (drop the
      "Phase 5 pending" note); tests: duplicate-name tar selects both; member-entry
      selects only its identity; mixed collection.

## 5. Link-resolution sweep & safety test gaps

- [x] 5a.1 Positional hardlink resolution in `_get_members_registered` /
      `_resolve_link`: walk members in order with an incremental map (latest earlier
      occurrence wins); fall back to a later member only when no earlier one exists;
      symlinks keep resolving against the full last-wins map. Streaming and
      random-access modes must agree on the duplicate-name cases.
- [x] 5a.2 Cycle detection by member id (not name) in `_resolve_link` and
      `_open_with_link_follow`; error message reports a cycle (not "target not
      found"). Tests: cyclic symlink pair (`a → b`, `b → a`) raises on `open()`;
      a chain through two distinct same-named members does NOT false-positive.
- [x] 5a.3 Duplicate-name positional test: `[A(content1), hardlink L→A, A(content2)]`
      — `read(L)` returns content1 and extraction links `L` to the content1 inode, in
      both access modes (the regression the last-wins map caused).
- [ ] 5a.4 Port the chained-symlink attack test from `_dev_oracle` into the v2 suite
      (member 1 plants `sub → /outside`, member 2 writes through `sub/...`; both the
      SYMLINK-payload and FILE-payload variants must be rejected).
- [ ] 5a.5 Defensive raise in `_copy_to_fileobj`/`_write_file` when a FILE member
      arrives with `stream=None` (today it silently creates an empty file, masking a
      backend bug; a zero-byte FILE gets a real empty stream, so raising is safe).
- [ ] 5a.6 Export `MemberSelector`, `MemberFilter`, `ArchiveyConfig`,
      `ExtractionLimits`, `PasswordRequest`, `PasswordProvider` from `archivey`
      (`__all__` + test_public_api assertions).

## 6. Finalization sweep

- [ ] 6.1 Per-format `CostReceipt` value assertions (zip/tar/all-compressed-tar/
      single-file/iso/directory) in one parametrized contract test.
- [ ] 6.2 Error-context stamping verified end-to-end for every backend (archive_name/
      member_name/format present on translated errors).
- [ ] 6.3 Remaining `archive-reading` / `archive-data-model` scenarios audited against
      the test suite; add the missing ones.
- [ ] 6.4 `SPEC.md` §2 signature blocks + `ARCHITECTURE.md` sketches updated to the
      final signatures; `openspec validate --strict` clean for the touched specs.
- [ ] 6.5 Implement the two maintainer decisions recorded in proposal.md
      (`max_entries`: count only written members — move `start_member` after selector +
      filter; ZIP `format_availability`: always PARTIAL until Phase 7 codec bypass).
      Spec deltas landed; code + tests still pending.

## 7. Sync

- [ ] 7.1 Sync delta specs to `openspec/specs/` (archive-reading, safe-extraction,
      format-7z, format-tar, backend-registry, format-zip) and archive the change.
