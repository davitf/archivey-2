# Phase 3: Base reader interface cleanup

## Why

The ABC contract between `BaseArchiveReader` and the format backends accumulated
ad-hoc hooks and flags in the DEV codebase: a `for_iteration` flag threaded through
`_open_member()`, a `_prepare_member_for_open()` hook that exists only to work
around a `py7zr` link-target limitation, capability switches passed as constructor
arguments, and method names that drifted from the `ARCHITECTURE.md` vocabulary.
Phase 4 (the extraction-coordinator rewrite) and Phase 8 (the native 7z/RAR
readers) both build directly on this ABC, so tightening it first keeps those phases
clean and removes a `py7zr`-shaped hook before `py7zr` leaves the read path. This is
an **internal refactor with no behavior change**.

## What Changes

- **Rename `iter_members_for_registration()` → `_iter_members()`** in
  `BaseArchiveReader` and every backend.
- **Remove the `for_iteration` parameter from `_open_member()`.** Solid backends
  (7z, RAR) instead override the iteration entry point wholesale, so `_open_member`
  is never called during iteration and no longer needs to know the calling context.
- **Convert `streaming_only` + `members_list_supported` from constructor args to
  class attributes** `_SUPPORTS_RANDOM_ACCESS` / `_MEMBER_LIST_UPFRONT`. TAR's
  random-access support still depends on whether the source is seekable, so its
  `__init__` may override the class default at instance level.
- **Remove the `_prepare_member_for_open()` hook.** The 7z backend stored a lazy
  link-target resolver in `member.raw_info` and `_open_member` invokes it as needed,
  so no separate hook is required.
- **Rename `_iter_members_and_streams_internal()` → `_iter_with_data()`** to match
  the `ARCHITECTURE.md` naming.
- **Normalize `_translate_exception()`** to the `Exception → ArchiveError | None`
  signature across all backends (already the shape; verify and make consistent).

## Specs

**No behavioral spec deltas.** The renamed methods, removed hooks, and capability
flags are private members of the reader ABC; the public behavior they implement is
already specified by `archive-reading` (sequential iteration, random access,
streaming, link following) and `backend-registry` (the `Backend` factory contract),
and none of it changes. The internal ABC vocabulary is documented in
`ARCHITECTURE.md`, not in `openspec/specs/`.

## Impact

- **Depends on:** Phase 2 (stream layer reorganization) complete and green.
- **Affected code:** `BaseArchiveReader` and all `format-*` reader backends
  (method renames, signature changes, flag-to-attribute conversion); the 7z and RAR
  backends specifically (drop `for_iteration` / `_prepare_member_for_open`, override
  the iteration entry point, lazy link resolver in `raw_info`).
- **No public API or behavior change** — the existing test suite is the regression
  guard.
- **Risk:** the 7z/RAR iteration-override change is the most invasive; ensure solid
  sequential iteration and random access still produce identical members and bytes
  before and after.
