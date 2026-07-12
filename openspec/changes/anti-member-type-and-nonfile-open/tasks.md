## 1. Data model

- [ ] 1.1 Add `MemberType.ANTI = "anti"` to `types.py` and docs/SPEC mirrors that list the enum
- [ ] 1.2 Add `ArchiveMember.is_anti` property (`type == MemberType.ANTI`); do **not** add an `is_anti` dataclass field
- [ ] 1.3 Update `test_data_model` (and any equality/helpers) for `ANTI` / `is_anti`

## 2. Non-file open/read gate

- [ ] 2.1 In `BaseArchiveReader`, after link following resolves to a member, raise `ArchiveyUsageError` from `open()`/`read()` when the resolved type is not `FILE`
- [ ] 2.2 Stop synthesizing empty `BytesIO` for directories in ZIP/TAR/7z `_open_member` paths (gate should make them unreachable; clean up dead branches)
- [ ] 2.3 Ensure directory and ISO backends no longer surface raw `IsADirectoryError` / directory-path `CorruptionError` for dir `open`/`read` (usage error instead)
- [ ] 2.4 Extend `error-handling` usage-error coverage comments/tests for the new case

## 3. Anti classification + extraction

- [ ] 3.1 Amend in-flight `native-7z-reader` (or post-merge 7z reader): classify ANTI-bit entries as `MemberType.ANTI`; remove “empty payload on open” behavior/tests
- [ ] 3.2 Confirm `check_universal` rejects only `OTHER`, not `ANTI`; anti extract branch still runs for `is_anti`
- [ ] 3.3 Update `native-7z-reader` delta specs that say empty anti open / `is_anti` field so they match this change

## 4. Tests

- [ ] 4.1 Cross-format: ZIP/TAR/ISO/directory — `open`/`read` on a directory raises `ArchiveyUsageError`; `stream_members` yields `None`
- [ ] 4.2 7z anti fixture (synthetic and/or CLI): `type is ANTI`, `stream_members` stream is `None`, `open`/`read` raise; extraction behavior unchanged
- [ ] 4.3 Run lint + type-check + the three-config pytest gate touched by these paths

## 5. Validation

- [ ] 5.1 `openspec validate --strict anti-member-type-and-nonfile-open`
- [ ] 5.2 Note in PR that this amends `native-7z-reader` anti open semantics and should merge with awareness of #66 ordering
