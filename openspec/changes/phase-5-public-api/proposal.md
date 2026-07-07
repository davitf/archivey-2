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
  (`Callable[[PasswordRequest], str | bytes | None]`) invoked per encrypted unit.
  `PasswordRequest` is a small frozen context object carrying the `ArchiveMember` being
  decrypted (`None` for archive-level/header decryption) and the `attempt` count for the
  unit, so an interactive UI can distinguish a first ask from a wrong-password retry —
  and the shape can grow (e.g. a `prior_error` field) without breaking the callable
  signature, which a bare-member parameter could not. Candidates that succeed join a
  per-archive known-good list tried first for later units, keeping single-pass streaming
  viable for archives whose folders/members use different passwords (7z has no cheap
  password check, so candidate order matters; RAR5/ZIP validate cheaply). Provider
  returning `None` → `EncryptionError`.
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
- **Extraction limits get a per-call override** (`safe-extraction`): the four loose
  bomb-limit kwargs on `extract()` / `extract_all()` are **removed**, replaced by a
  single structured `limits: ExtractionLimits | None = None` keyword. Precedence:
  per-call `limits` > `config.extraction_limits` > library default — the config is the
  app-wide home, the kwarg the per-call escape hatch ("tighter cap for this untrusted
  upload"), mirroring the existing "extract_all inherits the reader's config unless
  overridden" rule. An `ExtractionLimits.UNLIMITED` preset disables all four guards
  for explicitly trusted archives. Presets are deliberately **not** named by trust and
  **not** coupled to `ExtractionPolicy` (see design.md).
- **`strict_eof` moves into the config** (default **False** — see design.md for the
  rationale) and is renamed `strict_archive_eof` (format-agnostic: TAR trailer today,
  applicable to ZIP trailing-junk / gzip trailing-garbage checks later). **BREAKING**
  for the Phase 4 stopgap keyword (pre-1.0; the bare `strict_eof=` kwarg is removed).
  A `format-tar` delta renames the spec's `strict_eof` references accordingly.
- **`extract()` reaches parity with `open_archive()`** (`safe-extraction`): its
  `source` union widens to the same `Sequence[...]` multi-volume form (it merely
  delegates to `open_archive`, so a divergent signature would be an arbitrary freeze),
  and it gains the `encoding` keyword (needed for one-shot extraction of TAR/ZIP with
  non-UTF-8 member names).
- **Link resolution is made positional** (`archive-reading`): hardlinks resolve to the
  **latest occurrence at or before the link** (falling back to a later member only when
  no earlier one exists — the crafted/reordered-archive case extraction already
  recovers); symlinks keep resolving to the **last occurrence overall** (the final
  on-disk state). This makes streaming and random-access modes agree on duplicate
  names. Link-chain cycle detection tracks **member ids**, not names (name-based
  tracking false-positives on chains through distinct same-named members).
- **Public exports finalized**: `ArchiveyConfig`, `ExtractionLimits`, `PasswordRequest`
  / `PasswordProvider`, and the callback aliases used in public signatures
  (`MemberSelector`, `MemberFilter`) are exported from the top-level `archivey`
  namespace.
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

- `archive-reading`: password sequence/provider model (`PasswordRequest`); multi-source
  discovery/rejection details; `ArchiveyConfig` / `ExtractionLimits` definition and the
  `config=` parameter; `MemberSelector` collection semantics; `strict_eof` →
  `config.strict_archive_eof`; positional link resolution + member-id cycle detection
  (the "Transparent link following" requirement is modified).
- `safe-extraction`: `extract()`/`extract_all()` gain `config=` and the per-call
  `limits=` override (the four loose bomb-limit kwargs are removed); `extract()`'s
  `source` union widens and it gains `encoding`; the bomb-limit requirements reference
  `ExtractionLimits` (defaults unchanged); results-list accumulation documented as
  unconditional for v1 (a no-tracking mode interacts with the readers' internal member
  caching and is deferred — see design.md).
- `format-tar`: the truncation-detection requirement's `strict_eof` references are
  renamed to `config.strict_archive_eof` (no behavior change beyond the relocation).
- `format-7z`: the per-call-password phrasing is replaced by the
  candidate-list/provider model (no separate `open(member, password=...)` parameter).
  (`format-rar`'s password wording already fits the model; no delta needed.)
- `backend-registry` / `format-zip`: ZIP `format_availability` reports **PARTIAL**
  until Phase 7 wires optional member codecs into ZIP reads (even when packages are
  installed); see the resolved decisions below.
- (`compressed_source_size` generalization — any path source, `size`-advertising
  streams, seekable streams — is implemented and recorded in the in-flight
  `phase-4-safe-extraction` delta, not here.)

## Resolved decisions (2026-07 maintainer review)

Two items surfaced by the 2026-07 pre-Phase-5 reviews were decided as follows:

1. **`max_entries` counting semantics — count only members written.** The entry-count
   guard exists to bound filesystem/inode pressure, so only members that will actually
   be written increment the counter. The extraction coordinator calls
   `BombTracker.start_member()` after the `members` selector and user `filter` have
   accepted a member. Members excluded by the selector or skipped by the filter do not
   count. Spec delta: `safe-extraction`.
2. **ZIP `format_availability` — report current read truth.** Until Phase 7 bypasses
   stdlib `zipfile` for member data, `format_availability(ZIP)` reports **PARTIAL**
   regardless of optional codec installation; `missing` lists absent packages when
   applicable, and is empty when every package is present but the bypass is not yet
   wired. Spec deltas: `backend-registry`, `format-zip`.

## Impact

- `archivey.open_archive()` / `archivey.extract()` signatures (password type widens;
  `config=` added; `strict_eof=` removed; `source` union widens on **both**;
  `extract()` gains `encoding`; `extract()`/`extract_all()` gain `limits=` and lose
  the four loose bomb-limit kwargs).
- `archivey/internal/config.py`: `StreamConfig` folds into the public `ArchiveyConfig`
  (internal plumbing keeps a derived view).
- `BaseArchiveReader`: selector normalization for the collection form; password
  candidate/known-good bookkeeping helper for Phase 7 backends; `_resolve_link` /
  `_open_with_link_follow` reworked for positional hardlink resolution and member-id
  cycle detection (`__init__.py` exports grow accordingly).
- Phase 7 consumes: multi-volume entry paths, the password model, and (already landed)
  the generalized `compressed_source_size`.
- Docs: `SPEC.md` §2 signature blocks updated to match.
