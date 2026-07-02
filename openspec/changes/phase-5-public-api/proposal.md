# Phase 5: Public API finalization & cost surface

## Why

Phases 1–4 grew the public surface incrementally, deferring several signature decisions
("Phase 5 stopgaps"): `strict_eof` sits bare on `open_archive()`, the bomb limits and
extraction knobs have no agreed home, `MemberSelector`'s collection form is specced but
unimplemented, and `password` is a single value. Meanwhile Phase 7's native 7z/RAR
readers need API shapes that don't exist yet — multi-volume sources and multi-password
archives — and finalizing the surface *before* those consumers land (the PLAN ordering)
only works if their requirements are designed in now. This change locks the public API
per the maintainer decisions from the 2026-07 whole-codebase review.

## What Changes

- **Passwords become multi-candidate** (`archive-reading`): `password` accepts a single
  value, a **sequence** of candidates, or a **provider callable**
  (`Callable[[ArchiveMember | None], str | bytes | None]`) that is invoked per encrypted
  unit — receiving the member so a UI can show what is being asked about, or `None` for
  archive-level (header) decryption. Candidates that succeed join a per-archive
  known-good list tried first for later units, keeping single-pass streaming viable for
  archives whose folders/members use different passwords (7z has no cheap password
  check, so candidate order matters; RAR5/ZIP validate cheaply). Provider returning
  `None` → `EncryptionError`.
- **Multi-source input is implemented** (`archive-reading`, already specced):
  `open_archive()` accepts `Sequence[str | Path | BinaryIO]` as the explicit ordered
  volume list, and single-path volume-set **auto-discovery** (a path matching a volume
  pattern discovers its siblings on the same filesystem). Discovery is path-only —
  streams cannot enumerate siblings; the future fsspec URL layer supplies filesystem
  context for remote sets. Phase 5 lands the signature, detection plumbing, and the
  rejection behavior (multi-source for a format without volume support raises
  `UnsupportedFeatureError`); the actual joining readers land with their formats in
  Phase 7.
- **`ArchiveyConfig` public config object** (`archive-reading`; graduates the internal
  `StreamConfig`): a frozen dataclass passed explicitly as `open_archive(...,
  config=...)` / `extract(..., config=...)` — accelerator selection
  (gzip/bzip2 tri-state modes), `strict_eof`, and default `ExtractionLimits`
  (`max_extracted_bytes`, `max_ratio`, `ratio_activation_threshold`, `max_entries`).
  Per-call operational arguments (`format`, `streaming`, `password`, `members`,
  `filter`, `policy`, `overwrite`, `on_error`) stay keyword arguments — config is for
  tuning/policy knobs that rarely vary per call. No contextvars and no mutable global in
  v1 (decided: DEV's ambient-config approach traded traceability for convenience);
  a process-wide default stays possible to add later without breaking.
- **`strict_eof` moves into the config** (default **False** — see design.md for the
  rationale) and is renamed `strict_archive_eof` (format-agnostic: TAR trailer today,
  applicable to ZIP trailing-junk / gzip trailing-garbage checks later). **BREAKING**
  for the Phase 4 stopgap keyword (pre-1.0; the bare `strict_eof=` kwarg is removed).
- **`MemberSelector` collection form is implemented** (`archive-reading`): a
  `Collection[str | ArchiveMember]` selects by **name match — every duplicate with that
  name — or by member identity** for `ArchiveMember` entries (matched via
  `member_id`/`archive_id`, since members are unhashable). Equivalent to a predicate;
  extraction of duplicate selected names keeps sequential last-wins-on-disk semantics.
- **Finalization sweep** (mostly verification, not new behavior): `archive-data-model`,
  `access-mode-and-cost` (CostReceipt values asserted per format), `error-handling`
  (context stamping verified end-to-end), remaining `archive-reading` scenarios.

## Capabilities

### New Capabilities

_None — this change finalizes existing capabilities._

### Modified Capabilities

- `archive-reading`: password sequence/provider model; multi-source
  discovery/rejection details; `ArchiveyConfig` / `ExtractionLimits` definition and the
  `config=` parameter; `MemberSelector` collection semantics; `strict_eof` →
  `config.strict_archive_eof`.
- `safe-extraction`: `extract()`/`extract_all()` gain `config=`; the bomb-limit
  requirements reference `ExtractionLimits` (defaults unchanged); results-list
  accumulation documented as unconditional for v1 (a no-tracking mode interacts with
  the readers' internal member caching and is deferred — see design.md).
- `format-7z`: the per-call-password phrasing is replaced by the
  candidate-list/provider model (no separate `open(member, password=...)` parameter).
  (`format-rar`'s password wording already fits the model; no delta needed.)
- (`compressed_source_size` generalization — any path source, `size`-advertising
  streams, seekable streams — is implemented and recorded in the in-flight
  `phase-4-safe-extraction` delta, not here.)

## Impact

- `archivey.open_archive()` / `archivey.extract()` signatures (password type widens;
  `config=` added; `strict_eof=` removed; `source` union widens).
- `archivey/internal/config.py`: `StreamConfig` folds into the public `ArchiveyConfig`
  (internal plumbing keeps a derived view).
- `BaseArchiveReader`: selector normalization for the collection form; password
  candidate/known-good bookkeeping helper for Phase 7 backends.
- Phase 7 consumes: multi-volume entry paths, the password model, and (already landed)
  the generalized `compressed_source_size`.
- Docs: `SPEC.md` §2 signature blocks updated to match.
