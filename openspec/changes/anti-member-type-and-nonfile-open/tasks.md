## 1. Data model

- [ ] 1.1 Add `MemberType.ANTI`; `is_anti` property; remove `is_anti` field
- [ ] 1.2 Keep `is_current` field; align `ArchiveMember` / data-model tests

## 2. Non-file open gate

- [ ] 2.1 After link follow, `open`/`read` raise `ArchiveyUsageError` unless resolved type is `FILE`
- [ ] 2.2 Remove empty-`BytesIO` dir/anti branches; fix directory/ISO error leakage

## 3. 7z + extraction

- [ ] 3.1 Classify 7z ANTI-bit entries as `MemberType.ANTI`
- [ ] 3.2 `check_universal` rejects only `OTHER`; anti extract still on `is_anti`
- [ ] 3.3 Update tests that expected empty anti/`FILE` opens

## 4. Verify

- [ ] 4.1 Cross-format dir + 7z ANTI tests per `testing-contract`
- [ ] 4.2 `openspec validate --strict anti-member-type-and-nonfile-open`
