# Tasks — Phase 3: Base reader interface cleanup

> Run tools through uv: `uv run pytest`, `uv run mypy`, `uv run ruff`.
> Prerequisite: Phase 2 complete (stream layer reorganized; suite green).
> Goal: a cleaner `BaseArchiveReader` ↔ backend ABC; **no behavior change**.

## 1. Rename iteration entry points

- [ ] 1.1 Rename `iter_members_for_registration()` → `_iter_members()` in
      `BaseArchiveReader`.
- [ ] 1.2 Rename `_iter_members_and_streams_internal()` → `_iter_with_data()`
      (matching `ARCHITECTURE.md`).
- [ ] 1.3 Update every backend override and all call sites to the new names.

## 2. Drop the `for_iteration` flag

- [ ] 2.1 Remove the `for_iteration` parameter from `BaseArchiveReader._open_member()`
      and from every backend's override.
- [ ] 2.2 Make the solid backends (7z, RAR) override `_iter_with_data()` entirely so
      `_open_member` is never invoked during iteration.
- [ ] 2.3 Confirm no remaining reader branches on calling context.

## 3. Capability flags → class attributes

- [ ] 3.1 Replace the `streaming_only` / `members_list_supported` constructor args
      with class attributes `_SUPPORTS_RANDOM_ACCESS` / `_MEMBER_LIST_UPFRONT`.
- [ ] 3.2 Set the defaults per backend (e.g. ZIP: both `True`; TAR: both `False`).
- [ ] 3.3 In `TarReader.__init__`, override `_SUPPORTS_RANDOM_ACCESS` to `True` when
      the underlying source is seekable (instance-level resolution).

## 4. Remove the `_prepare_member_for_open()` hook

- [ ] 4.1 Delete `_prepare_member_for_open()` from `BaseArchiveReader` and backends.
- [ ] 4.2 In the 7z backend, store a lazy link-target resolver in `member.raw_info`
      and invoke it from `_open_member` when a link target is needed.
- [ ] 4.3 Verify symlink/hardlink reads still resolve correctly.

## 5. Normalize exception translation

- [ ] 5.1 Ensure every backend's `_translate_exception()` has the signature
      `Exception → ArchiveError | None` and is consistent.

## 6. Verify — acceptance criteria

- [ ] 6.1 `uv run pytest tests/` passes — identical results to Phase 2 (no behavior
      change).
- [ ] 6.2 `uv run mypy src/` passes under `--strict`.
- [ ] 6.3 `uv run ruff check` passes.
- [ ] 6.4 No `for_iteration` parameter anywhere in the tree.
- [ ] 6.5 No `_prepare_member_for_open` method anywhere in the tree.
- [ ] 6.6 All backends use the new method names and class-attribute capability flags
      consistently.
