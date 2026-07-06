## 1. Public surface

- [ ] 1.1 Add `scan_members(self) -> list[ArchiveMember]` to the `ArchiveReader` protocol in `src/archivey/reader.py`, documenting: returns the fully-resolved list; in streaming mode it finishes (runs, or completes an interrupted) single forward pass and may follow a completed one.

## 2. Failing tests (red)

- [ ] 2.1 `tests/test_tar.py`: streaming tar with a **forward-pointing symlink** — `scan_members()` returns the list with that symlink's `link_target_member` resolved (parity with random-access `members()` on the same archive).
- [ ] 2.2 `tests/test_tar.py`: after a complete streaming `__iter__` (and separately `stream_members`) pass, `get_members_if_available()` returns the fully-resolved list (not `None`), and a forward symlink collected during the pass now shows its resolved target (in-place fill).
- [ ] 2.3 `tests/test_tar.py`: `scan_members()` **finishes an interrupted pass** — iterate with an early `break`, then `scan_members()` returns the complete resolved list; a subsequent `__iter__`/`stream_members`/`extract_all` raises `UnsupportedOperationError`.
- [ ] 2.4 `tests/test_tar.py`: an **abandoned** partial pass (early `break`, no `scan_members`) leaves `get_members_if_available()` returning `None`.
- [ ] 2.5 One-pass-only rejection (streaming): a second `__iter__`/`stream_members`/`extract_all` raises `UnsupportedOperationError` — **including a second `__iter__` after the first ran to completion** (no cache-replay). Cover no-index (tar) and an indexed format (zip).
- [ ] 2.6 `scan_members()` in random-access mode returns the same list as `members()` and leaves the reader usable (nothing consumed); cover zip (trailing-index), a directory (leading-index), and tar (no-index).
- [ ] 2.7 `scan_members()` before any pass on a streaming reader consumes it (subsequent `stream_members()` raises) for every topology, incl. an indexed zip.
- [ ] 2.8 `tests/test_zip.py`: index-only vs resolved links — `get_members_if_available()` returns a ZIP symlink with `link_target`/`link_target_member` **unset** and reads no member data; `members()`/`scan_members()` return it **resolved**; `open()`/`read()`/extraction still follow the symlink.

## 3. base_reader implementation (green)

- [ ] 3.1 Add `_forward_pass_started: bool` (init `False`) and a guard that, in streaming mode, raises `UnsupportedOperationError` from `__iter__`/`stream_members`/`extract_all` when a pass has already started (even a completed one). Set the flag when a pass begins. `scan_members()` is exempt (it may finish/return the pass); `get_members_if_available()` never sets or checks it.
- [ ] 3.2 Make the streaming forward pass a **single instance-held** iterator so an interrupted pass can be continued: `_register_progressively` accumulates into instance state (`_scanned`, `_by_name_lists`) and, on **normal exhaustion** (consumer-drained or `scan_members`-drained to EOF), runs full `_resolve_link` over all link members (forward/last-wins/chains, in place) and sets `self._members_cache` / `self._members_by_name_lists`. Confirm an early `break` (without finishing) does not reach the finalization tail.
- [ ] 3.3 Implement `scan_members()`: random-access → `list(self._get_members_registered())`; streaming → return `_members_cache` if set, else begin (if not started) and drain the instance-held pass to EOF (metadata only), triggering 3.2's finalization, then return `_members_cache`.
- [ ] 3.4 Ensure `get_members_if_available()` is index-only: returns `_members_cache` when set, the index list for `_MEMBER_LIST_UPFRONT` backends (no member-data read), else `None`. No pass consumption, no scan.

## 4. Backend routing / ZIP symlink deferral

- [ ] 4.1 Refactor TAR's `_iter_with_data()` streaming path to pull from the shared instance-held forward pass (so `__iter__`/`stream_members`/`scan_members` share one cursor and finalization), opening member data alongside; preserve the `_verify_tar_eof()` interaction.
- [ ] 4.2 ZIP: stop `ZipReader._to_member` from eagerly opening+reading symlink data during enumeration. Leave `link_target` unset at enumeration; read the target lazily where a resolved target is needed — link resolution in `_get_members_registered`/`scan_members`, and link-following in `open()`/`read()`/extraction. Verify listing performs no symlink-data reads.
- [ ] 4.3 Update the `BaseArchiveReader` docstring: streaming backends overriding `_iter_with_data()` MUST route their forward pass through the shared `_register_progressively` pass (so the resolved cache is finalized on completion and `scan_members` can finish an interrupted pass). Note this as a requirement for the future native 7z/RAR readers.

## 5. Docs

- [ ] 5.1 Update `ARCHITECTURE.md` §2.4: `scan_members()`, post-pass materialization, one-pass-only (no streaming `__iter__` replay), the index-only `get_members_if_available()` contract, and the resolved-vs-unresolved-links asymmetry (link targets stored in member data). Correct the illustrative `members()` snippet if it implies streaming materialization or streaming replay.

## 6. Validation gate

- [ ] 6.1 `openspec validate scan-members --strict` passes.
- [ ] 6.2 Type-check clean: `uv run --no-sync pyrefly check` and `uv run --no-sync ty check`.
- [ ] 6.3 Lint clean: `uv run --no-sync ruff check`.
- [ ] 6.4 Tests pass across the three dependency configs (`[all]`, `[all-lowest]`, `[core-only]`) per CONTRIBUTING "Before pushing…".
