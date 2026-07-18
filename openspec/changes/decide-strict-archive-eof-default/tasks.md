## 1. Decision (locked — Option F)

- [x] 1.1 Maintainer picked Option F in `design.md` (signal-aware default: `nonzero` raises
      `CorruptionError` regardless of flag; `absent`/`short` warn by default, `TruncatedError`
      under strict; extract raises at end)
- [x] 1.2 Deltas rewritten for Option F (`specs/format-tar`, `specs/documentation`); no
      `archive-reading` delta (config default/signature unchanged)
- [ ] 1.3 Record CLI strict-EOF intent for `cli-v1` (Open Question 2) as a one-line cross-link
      — do not implement CLI here

## 2. Implement Option F in `tar_reader._verify_tar_eof`

- [ ] 2.1 No `ArchiveyConfig` / `config.py` default change (stays `False`)
- [ ] 2.2 On `observed_kind == "nonzero"`: escalate as `CorruptionError` unconditionally
      (independent of `strict_archive_eof`), preserving the count/retain/log/callback ordering
      and precedence over `DiagnosticRaisedError`
- [ ] 2.3 On `observed_kind in ("absent", "short")`: keep current disposition — warn by
      default, escalate as `TruncatedError` only when `strict_archive_eof=True`
- [ ] 2.4 Confirm extract paths (`extract_all` / `stream_members`) raise at end after
      salvageable members, not mid-pass (verify `_verify_tar_eof` still runs after the full
      iteration in both random-access and streaming modes)
- [ ] 2.5 Update the `_verify_tar_eof` docstring / diagnostic message to describe the
      `nonzero`-vs-`absent`/`short` split

## 3. Docs and open-issues

- [ ] 3.1 Update `docs/formats.md` TAR EOF section: `nonzero` raises by default;
      `strict_archive_eof=True` escalates the ambiguous `absent`/`short` residual
- [ ] 3.2 When user Gotchas exists (or in the same docs PR): TAR silent-shorten + the
      default `nonzero` raise + strict knob for the residual + “may improve with native TAR
      later”; post-v1 ZIP items as current limitations
- [ ] 3.3 Reword `docs/internal/open-issues.md` P1 to "decided — Option F"; point at this
      change; leave P3 (native TAR) owning the `absent`/`short` structural fix

## 4. Verify

- [ ] 4.1 Targeted tests: `tests/test_tar.py` EOF section (add `nonzero`→`CorruptionError`
      default case; `absent`/`short` stay warn by default and raise `TruncatedError` under
      strict; `tar -b1` + trailing-padding no-false-positive regression; extract raise-at-end
      on `nonzero`), `tests/test_archivey_config.py`, `tests/test_diagnostics.py` IGNORE vs
      escalate for both exception types
- [ ] 4.2 Run the suite in all three dependency configs (`[all]`, `[all-lowest]`,
      `[core-only]`) per `CONTRIBUTING.md`
- [ ] 4.3 `openspec validate --strict decide-strict-archive-eof-default`
