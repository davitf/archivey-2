# Tasks — `strict_archive_eof` trailing bytes

## 1. The code

- [x] 1.1 `ARCHIVE_TRAILING_DATA` on `DiagnosticCode`, mapped to the existing
      `archive_eof` context kind (the fields already fit; `expected_marker` distinguishes
      the two checks).
- [x] 1.2 In `_verify_tar_eof`, on the **success** path only, and only under
      `strict_archive_eof`: read to EOF and require every byte to be zero.
- [x] 1.3 First non-zero byte → emit with `observed_bytes` = its offset past the trailer,
      escalating to `CorruptionError`.
- [x] 1.4 Read in bounded chunks, not one `read()` — the tail can be arbitrarily long and
      this must not materialize it.

## 2. Cost, written where it will be read

- [x] 2.1 The `strict_archive_eof` comment in `config.py` says the check is now
      O(tail length), and that a compressed tar decompresses its tail.
- [x] 2.2 `docs/gotchas.md` (the TAR residuals bullet) and `docs/formats.md`.
- [x] 2.3 `CHANGELOG.md` under a behaviour heading.

## 3. Tests

- [x] 3.1 `test_strict_archive_eof_ignores_trailing_junk[True]` flips: rewrite it to
      assert the raise. `[False]` keeps passing unchanged.
- [x] 3.2 Zero padding still passes under strict — the case the rule exists to preserve.
- [x] 3.3 `test_legitimately_empty_tar_stays_valid` and
      `test_zero_filled_dot_tar_opens_empty_via_extension[…]` keep passing: a zero-member
      TAR must not raise (O8a).
- [x] 3.4 Concatenated tars raise under strict; the ISO-as-TAR case does not.
- [x] 3.5 A non-zero byte buried between zero runs is caught (not just a junk suffix).

## 4. Verify

- [x] 4.1 `openspec validate --strict strict-archive-eof-trailing-bytes`
- [x] 4.2 Three dependency configs, `ruff`, `pyrefly`, `ty`.

## Archive (after this merges)

Left unchecked on purpose: `scripts/check_openspec_archived.py` reads an all-complete
tasks list as "finished but unarchived" and fails on `main`, and archiving is a separate
step from merging (`CONTRIBUTING.md`).

- [ ] A.1 `openspec archive strict-archive-eof-trailing-bytes --yes`, then commit the resulting
      `openspec/specs/` diff.

**Archive order matters.** Several of these changes modify the same requirement, so a
later one pastes an earlier one's version and only matches once that has been applied:

```
format-availability-required-source -> decouple-member-metadata-from-declared-seekability -> review-diagnostics-batch -> reject-bidi-overrides-in-safe-extraction -> strict-archive-eof-trailing-bytes -> rewind-diagnostic-redecode-cost
```

Verified by dry-run archive on a scratch tree in that order: all six apply, and the only
lines removed from `openspec/specs/` are ones a delta deliberately rewrites. (`openspec
validate --strict` does **not** catch a mis-targeted `MODIFIED` header — it passed while
two deltas would have silently dropped a scenario on archive.)
