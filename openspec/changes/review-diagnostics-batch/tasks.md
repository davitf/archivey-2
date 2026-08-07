# Tasks — the review diagnostics batch

## 1. Taxonomy

- [x] 1.1 Six codes on `DiagnosticCode`: `ENCODING_ARGUMENT_UNUSED`,
      `PASSWORD_ARGUMENT_UNUSED`, `MEMBER_NAME_BIDI_CONTROL`, `EMPTY_ARCHIVE`,
      `EXPLICIT_FORMAT_LISTED_EMPTY`, `EXTENSION_FORMAT_UNCONFIRMED`.
- [x] 1.2 Four context dataclasses: `UnusedArgumentContext` (shared by the two argument
      codes), `MemberNameControlsContext`, `EmptyArchiveContext`,
      `UnconfirmedFormatContext` (shared by the two format codes).
- [x] 1.3 Register them in `_CODE_CONTEXT_KINDS` and the `DiagnosticContext` union;
      export the new names.

## 2. Unused arguments (4a, 4b)

- [x] 2.1 `ReadBackend.USES_ENCODING: bool = False`; `True` on ZIP and TAR only.
- [x] 2.2 `open_archive`: emit `ENCODING_ARGUMENT_UNUSED` for the **caller's** explicit
      `encoding=` on a backend that does not use it. A detector `encoding_hint` does not
      emit.
- [x] 2.3 Replace the `UnsupportedOperationError` password gate with
      `PASSWORD_ARGUMENT_UNUSED`, and widen it to every password form — a provider
      callable already opened fine, which was the asymmetry.
- [x] 2.4 Fix the three places that document the old raise:
      `review/docs/independent/must-explain.md`, `review/docs/outline.md`, and any
      published `docs/` page. Grep `UnsupportedOperationError` near "password".

## 3. Bidi (4d)

- [x] 3.1 `emit_member_name_bidi_control` in `naming.py`, replacing the bare
      `logger.warning`; context records the `U+XXXX` spellings found.
- [x] 3.2 Call it from `_register_member`, where the warning already lived, so directory
      and single-file names get it too and no backend can duplicate it.

## 4. Empty listing (4e, 4c, 4f)

- [x] 4.1 `FormatProvenance` on the reader, set by `open_archive` before it returns.
- [x] 4.2 `_publish_materialized`: on a clean, zero-member listing emit `EMPTY_ARCHIVE`,
      then at most one of the two format codes.
- [x] 4.3 `EXTENSION_FORMAT_UNCONFIRMED` from `FormatInfo.detected_by == "extension"` —
      no rescan; that value already means "no content signal matched".
- [x] 4.4 `EXPLICIT_FORMAT_LISTED_EMPTY`: re-run `detect_format` on an empty listing,
      Path sources only.

## 5. Docs

- [x] 5.1 `docs/errors-and-diagnostics.md` — the six codes.
- [x] 5.2 `docs/opening-and-listing.md` / `docs/gotchas.md` — `password=` no longer
      raises; the empty-listing signals.
- [x] 5.3 `CHANGELOG.md` — behaviour heading for the `password=` change.

## 6. Tests

- [x] 6.1 Delete the two red halves that assert a **refusal**: the decision was a
      diagnostic (`test_unusable_encoding_argument_is_refused[...]`,
      `test_wrong_explicit_format_does_not_silently_succeed`). Replace with the
      diagnostic assertions.
- [x] 6.2 Delete `test_bidi_name_warning_has_no_diagnostic_code` — it asserts the absence
      this change removes.
- [x] 6.3 Cover: all three password forms; the encoding hint not emitting; empty tar /
      empty zip; `z.tar` zeros; `format=TAR` on an ISO; an incomplete listing not emitting.

## 7. Verify

- [x] 7.1 `openspec validate --strict review-diagnostics-batch`
- [x] 7.2 Three dependency configs, `ruff`, `pyrefly`, `ty`.

## Archive (after this merges)

Left unchecked on purpose: `scripts/check_openspec_archived.py` reads an all-complete
tasks list as "finished but unarchived" and fails on `main`, and archiving is a separate
step from merging (`CONTRIBUTING.md`).

- [ ] A.1 `openspec archive review-diagnostics-batch --yes`, then commit the resulting
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
