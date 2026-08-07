# Tasks — decouple member metadata from declared seekability

## 1. Separate the probe config from the member-stream config

- [x] 1.1 Derive `_metadata_config` in `SingleFileReader.__init__`: `seekable` from the
      **source's** shape, accelerators `OFF`, `streaming=False`.
- [x] 1.2 Point `_probe_decompressed_size` at it, so the xz index is parsed on a seekable
      source whether or not the caller declared `seekable_members`.
- [x] 1.3 Drop the `self._codec_config.seekable` gate from `_probe_lzip_index`;
      `_with_seekable_source` is the check that was always the real one.
- [x] 1.4 Leave `_codec_config` — and therefore every stream handed to a caller —
      untouched.

## 2. Specs and docs

- [x] 2.1 `format-single-file-compressors`: the LZIP size row, the lzip digest rule, and
      an explicit "source shape, not declared capability" clause on both requirements.
- [x] 2.2 Check the published docs for the same claim (`docs/formats.md`,
      `docs/reading-members.md`, `docs/access-and-cost.md`).
- [x] 2.3 `CHANGELOG.md` under the unreleased section — caller-visible.

## 3. Tests

- [x] 3.1 Delete the three red halves' markers:
      `test_member_size_does_not_depend_on_declared_seekability[lz]`, `[xz]`,
      `test_lzip_surfaces_crc32_without_declaring_seekable_members`.
- [x] 3.2 Rewrite the pin `test_declared_seekability_changes_member_size` — it asserted
      the divergence this change removes.
- [x] 3.3 A pipe still reports `size=None` and no digest, and does not decode.

## 4. Verify

- [x] 4.1 `openspec validate --strict decouple-member-metadata-from-declared-seekability`
- [x] 4.2 `uv run pytest` in all three dependency configs, `ruff check`,
      `ruff format --check`, `pyrefly check`, `ty check`.

## Archive (after this merges)

Left unchecked on purpose: `scripts/check_openspec_archived.py` reads an all-complete
tasks list as "finished but unarchived" and fails on `main`, and archiving is a separate
step from merging (`CONTRIBUTING.md`).

- [ ] A.1 `openspec archive decouple-member-metadata-from-declared-seekability --yes`, then commit the resulting
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
