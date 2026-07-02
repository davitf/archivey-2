# Phase 5 tasks — public API finalization

> Order matters: the config object (1) unblocks strict_eof relocation (its tests) and
> extraction limits; the password model (2) and multi-source (3) are independent of each
> other; the sweep (5) runs last.

## 1. ArchiveyConfig / ExtractionLimits

- [ ] 1.1 Define public `ExtractionLimits` and `ArchiveyConfig` frozen dataclasses
      (fields per design.md), exported from `archivey`; module default constant.
- [ ] 1.2 `open_archive(..., config: ArchiveyConfig | None = None)`; the reader stores
      its config; internal `StreamConfig` becomes a view derived from
      `ArchiveyConfig` + the access mode (`streaming` stays derived, not public).
- [ ] 1.3 Relocate `strict_eof` → `config.strict_archive_eof`; **remove** the
      `open_archive(strict_eof=)` keyword and the `ReadBackend.open_read` parameter
      (readers read it from their config). Update TAR backend + tests.
- [ ] 1.4 Tests: default config equivalence; accelerator modes honored via config
      (monkeypatched sentinels); `strict_archive_eof=True` raises / default warns;
      frozen-ness (`dataclasses.FrozenInstanceError` on mutation).

## 2. Password candidates and provider

- [ ] 2.1 Widen `password` typing on `open_archive` (`str | bytes | Sequence[str |
      bytes] | PasswordProvider | None`); normalize once into an internal
      `_PasswordCandidates` helper on `BaseArchiveReader` (known-good list +
      remaining candidates + optional provider; `provider(member_or_none)`).
- [ ] 2.2 Wire the ZIP backend (the only current consumer) through the helper: try
      known-good → candidates → provider per encrypted member; successful password
      appended to known-good; exhaustion → `EncryptionError`. Encrypted-symlink
      listing path uses the same resolution.
- [ ] 2.3 Tests: sequence across two differently-encrypted members in one pass;
      provider called once per *new* password (call-count assertion) and receives the
      member; provider `None` → `EncryptionError`; header-level call passes `None`
      (unit-test the helper; the real header-encrypted case lands with Phase 7).
- [ ] 2.4 Docs: candidate-order note ("most likely password first" — 7z key derivation
      is deliberately expensive) in the open_archive docstring.

## 3. Multi-source input

- [ ] 3.1 Accept `Sequence[str | Path | BinaryIO]` in `open_archive` (length-1 ≡
      single source); detection runs on the first volume; origin-normalize each
      seekable stream.
- [ ] 3.2 Single-path volume-set discovery: `.7z.NNN` / `.partN.rar` / `.rNN` name
      patterns → sibling scan in the path's directory, natural order (path sources
      only; a helper the Phase 7 readers reuse).
- [ ] 3.3 Phase-5 behavior: a resolved multi-source open raises
      `UnsupportedFeatureError` with a format-appropriate message (7z/RAR: "lands in
      Phase 7"; others: "not a multi-volume format"). Tests for both messages and
      for discovery ordering (fixture files, no real volume parsing).

## 4. MemberSelector collection form

- [ ] 4.1 Normalizer: `Collection[str | ArchiveMember]` → predicate (name set matches
      all duplicates; `ArchiveMember` entries matched by `_archive_id` + `member_id`
      id-set; mixed collections fine); wire into `stream_members` (and the
      `extract_all(members=)` path when 4b lands).
- [ ] 4.2 Update the `MemberSelector` alias + docstring in `reader.py` (drop the
      "Phase 5 pending" note); tests: duplicate-name tar selects both; member-entry
      selects only its identity; mixed collection.

## 5. Finalization sweep

- [ ] 5.1 Per-format `CostReceipt` value assertions (zip/tar/all-compressed-tar/
      single-file/iso/directory) in one parametrized contract test.
- [ ] 5.2 Error-context stamping verified end-to-end for every backend (archive_name/
      member_name/format present on translated errors).
- [ ] 5.3 Remaining `archive-reading` / `archive-data-model` scenarios audited against
      the test suite; add the missing ones.
- [ ] 5.4 `SPEC.md` §2 signature blocks + `ARCHITECTURE.md` sketches updated to the
      final signatures; `openspec validate --strict` clean for the touched specs.

## 6. Sync

- [ ] 6.1 Sync delta specs to `openspec/specs/` (archive-reading, safe-extraction,
      format-7z) and archive the change.
