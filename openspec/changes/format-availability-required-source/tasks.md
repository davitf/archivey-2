# Tasks — `required_source` on `FormatAvailability`

## 1. Order `StreamCapability`

- [x] 1.1 Make `StreamCapability` totally ordered, weakest first
      (`FORWARD_ONLY < SEEKABLE`), in `src/archivey/cost.py`. Keep the string values
      unchanged — they appear in specs and CLI output.
- [x] 1.2 Document on the enum *why* there is only one possible direction (a seekable
      source serves every read a forward-only one can), so the order is not read as a
      preference.

## 2. Add the field

- [x] 2.1 Add `required_source: StreamCapability = StreamCapability.SEEKABLE` to
      `FormatAvailability` (`src/archivey/internal/registry.py`).
- [x] 2.2 Derive it in `BackendRegistry.format_availability()` from the backend's
      `SUPPORTS_STREAMING_NON_SEEKABLE`, on **every** return path — including the
      missing-dependency and missing-codec paths, where the answer is still known.
- [x] 2.3 Leave the no-registered-backend path at the `SEEKABLE` default.

## 3. Docs

- [x] 3.1 `docs/opening-and-listing.md` — the non-seekable-stream paragraph gains the
      queryable answer, with the comparison against `reader.cost.stream_capability`.
- [x] 3.2 `docs/access-and-cost.md` — note the ordering next to the axis table.
- [x] 3.3 `CHANGELOG.md` under the unreleased section.

## 4. Tests

- [x] 4.1 `required_source` matches the accept/refuse behaviour for every known format:
      a format the library opens from a pipe reports `FORWARD_ONLY`, one that raises
      `StreamNotSeekableError` reports `SEEKABLE`. Drive it off `list_known_formats()`
      so a new backend cannot skip the question.
- [x] 4.2 The ordering: both directions, reflexivity, `sorted()`, and `TypeError`
      against a foreign type.
- [x] 4.3 The answer survives a missing optional dependency (`support=NONE` still
      carries a real `required_source`).

## 5. Verify

- [x] 5.1 `openspec validate --strict format-availability-required-source`
- [x] 5.2 `uv run pytest` in all three dependency configs (`[all]`, `[all-lowest]`,
      `core-only`), `ruff check`, `ruff format --check`, `pyrefly check`, `ty check`.

## Archive (after this merges)

Left unchecked on purpose: `scripts/check_openspec_archived.py` reads an all-complete
tasks list as "finished but unarchived" and fails on `main`, and archiving is a separate
step from merging (`CONTRIBUTING.md`).

- [ ] A.1 `openspec archive format-availability-required-source --yes`, then commit the resulting
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
