# Archivey — Implementation Plan (v2 Selective Rewrite)

> **Starting point:** the existing `archivey-dev` codebase.
> **Approach:** selective rewrite — keep the parts that are already good, replace the parts that need it.
> **No backwards-compatibility requirement:** the new public API is defined in SPEC.md and ARCHITECTURE.md, not inherited from DEV.
>
> Each phase ends with a mergeable, mypy-clean, passing state.
> Phases are sized for roughly 1–3 days of focused work each.

---

## What we're keeping from DEV (largely as-is)

- Format backends: ZIP, TAR, single-file compressors, ISO, directory — logic and edge cases are correct and well-tested. Port with interface adjustments only.
- 7z backend: the thread-queue streaming approach (`StreamingFile`) and per-folder extraction cache — both are good. Port and clean up.
- RAR backend: the `unrar p` pipe demultiplexer (`RarStreamReader`) and solid-cache approach — port and clean up.
- `ArchiveStream`: lazy-opening + exception translation wrapper — clean design, keep it.
- `RewindableStreamWrapper` + `RecordableStream` — correct, keep them.
- `DecompressorStream`, `XzStream`, `LzipStream` — keep, move to better location.
- Format detection logic.
- Test declarative approach: `ArchiveContents`, `FileInfo`, `ArchiveCreationInfo`, `conftest.py` parametrization.

## What we're rewriting

- **`ExtractionHelper`**: replace with `ExtractionCoordinator` (unified streaming pass, no deferred/pending state machine).
- **`BaseArchiveReader` interface**: rename methods, remove `for_iteration` parameter, convert `streaming_only`/`members_list_supported` constructor args to class attributes, remove `_prepare_member_for_open` hook.
- **`io_helpers.py`**: split into logical modules; rewrite `BinaryIOWrapper` to remove the method-replacement trick.
- **Public API**: align to SPEC.md — `iter_members_with_streams` → `stream_members`, add `CostReceipt`, `ArchiveInfo` fields, etc.
- **Test binary archives**: stop committing generated archives; generated-on-demand only.

---

## Phase 1: Project scaffold and initial port

**Goal:** new project compiles, all DEV tests pass.

### Tasks

1. **`pyproject.toml`** — clean slate:
   - Build backend: `hatchling`.
   - `[project]` metadata: `archivey`, version `0.2.0.dev0`, Python `>=3.11`.
   - Optional extras: `7z` (py7zr), `rar` (rarfile), `iso` (pycdlib), `zstd` (zstandard), `all`, `dev`.
   - `[tool.mypy]`: `strict = true`, `python_version = "3.11"`.
   - `[tool.ruff]`, `[tool.coverage]`.

2. **Copy source from DEV**: bring over all `src/archivey/` Python files verbatim to start. This gives a baseline that compiles and passes tests before any changes.

3. **Copy test infrastructure**: `sample_archives.py`, `create_archives.py`, `conftest.py`, `testing_utils.py`, and all `test_*.py` files. Move committed test archives to `tests/fixtures/`.

4. **Verify**: `mypy src/` passes, `pytest tests/` passes (minus any tests depending on DEV-only API).

### Acceptance criteria
- All tests pass.
- No new public API exposed yet (this is a private fork state).

---

## Phase 2: Stream layer reorganization

**Goal:** `io_helpers.py` is split into logical modules; `BinaryIOWrapper` is simplified.

### Tasks

1. **Create `src/archivey/internal/streams/` package**:
   - `detect.py`: `RecordableStream`, `RewindableStreamWrapper` — only used for format detection.
   - `slice.py`: `SlicingStream`.
   - `compat.py`: `is_seekable`, `is_stream`, `is_filename`, `ensure_binaryio`, `ensure_bufferedio`, `fix_stream_start_position`, `read_exact`. Also `BinaryIOWrapper` (simplified, see below).
   - Keep `archive_stream.py` in place (it's clean and focused).
   - Move `decompressor_stream.py`, `xz_stream.py`, `lzip_stream.py` into `streams/` as `decompress.py`, `xz.py`, `lzip.py`.

2. **Simplify `BinaryIOWrapper`**: remove the method-replacement hot-path trick (`self.read = self._raw.read` after first call). Replace with straightforward delegation. The micro-optimization isn't worth the fragility.
   ```python
   def read(self, size=-1):
       return self._raw.read(size)
   def readinto(self, b):
       if hasattr(self._raw, 'readinto'):
           return self._raw.readinto(b)
       data = self.read(len(b)); b[:len(data)] = data; return len(data)
   ```

3. **Update all imports**: `from archivey.internal.io_helpers import X` → appropriate new path. Keep `io_helpers.py` as a re-export shim temporarily to avoid changing format backends in this phase.

4. **Verify**: same test suite passes, mypy clean.

### Acceptance criteria
- `io_helpers.py` is ≤ 50 lines (all re-exports).
- No behaviour change.

---

## Phase 3: Base reader interface cleanup

**Goal:** the ABC contract between `BaseArchiveReader` and format backends is cleaner.

### Changes

1. **Rename `iter_members_for_registration()` → `_iter_members()`** in `BaseArchiveReader` and all backends.

2. **Remove `for_iteration` parameter from `_open_member()`**:
   - Currently used by 7z to hint whether it's an iteration call vs random access. Instead, solid backends override `_iter_members_and_streams_internal()` entirely — they never rely on `_open_member` being called during iteration.
   - Update 7z and RAR backends to override `_iter_members_and_streams_internal()` rather than checking `for_iteration`.

3. **Convert `streaming_only` + `members_list_supported` from constructor args to class attributes**:
   ```python
   class TarReader(BaseArchiveReader):
       _SUPPORTS_RANDOM_ACCESS = False   # set to True if source is seekable (resolved at __init__)
       _MEMBER_LIST_UPFRONT = False

   class ZipReader(BaseArchiveReader):
       _SUPPORTS_RANDOM_ACCESS = True
       _MEMBER_LIST_UPFRONT = True
   ```
   Note: `_SUPPORTS_RANDOM_ACCESS` is still determined at instance level for TAR (depends on whether the source is seekable), so `__init__` may override the class default.

4. **Remove `_prepare_member_for_open()` hook**:
   - Currently used by 7z to fetch link targets not populated at listing time (a py7zr limitation). Instead: store a lazy resolver in `member.raw_info`; `_open_member` calls it as needed. No extra hook required.

5. **Rename `_iter_members_and_streams_internal()` → `_iter_with_data()`** to match ARCHITECTURE.md naming.

6. **Update `_translate_exception()` signature**: take `Exception` → return `ArchiveError | None`. (Already the case; just verify all backends are consistent.)

7. **Verify**: all tests pass, mypy clean.

### Acceptance criteria
- No `for_iteration` parameter anywhere.
- No `_prepare_member_for_open` method anywhere.
- All backends updated consistently.

---

## Phase 4: ExtractionHelper rewrite

**Goal:** replace the deferred-pending state machine with a unified streaming coordinator.

### Design

The new `ExtractionCoordinator` in `internal/extraction_helper.py` (or rename to `extraction_coordinator.py`):

```python
@dataclass
class ExtractionCoordinator:
    archive_reader: ArchiveReader
    root_path: str
    overwrite_mode: OverwriteMode

    # Built before the pass, in random-access mode.
    # Maps source member_id → list of hardlink members that point to it.
    _hardlink_targets: dict[int, list[ArchiveMember]] = field(default_factory=...)

    # Populated during the pass.
    _extracted_path_by_id: dict[int, str] = field(default_factory=dict)
    _extracted_members_by_path: dict[str, ArchiveMember] = field(default_factory=dict)

    def run(self, members_and_streams: Iterable[tuple[ArchiveMember, BinaryIO | None]]) -> dict[str, ArchiveMember]:
        for member, stream in members_and_streams:
            self._process(member, stream)
        self._apply_metadata()
        return self._extracted_members_by_path

    def _process(self, member, stream): ...
```

**Pre-pass hardlink closure** (random-access mode only):

```python
def build_hardlink_map(members: list[ArchiveMember]) -> dict[int, list[ArchiveMember]]:
    # For each hardlink, find its source and record it.
    # Sources not already in the selected set are added as "data needed".
    ...
```

**During-pass logic** (no deferred state):
- `FILE`: write `stream → dest_path` immediately. Record `member_id → dest_path`.
- `DIR`: `os.makedirs(path, exist_ok=True)`.
- `HARDLINK`: look up `_extracted_path_by_id[source_id]`. If found: `os.link(source_path, dest_path)` (fallback: `shutil.copy2`). If not found and streaming mode: `ArchiveError("hardlink target not yet extracted")`. If not found and random-access: cannot happen if pre-pass was correct.
- `SYMLINK`: `os.symlink(target, dest_path)`, then verify the resolved path stays within root (post-creation check). If escaped: unlink and raise.

**Removal of complexity**:
- No `pending_files_to_extract_by_id` / `pending_target_members_by_source_id` dicts.
- No `can_move_file` flag.
- No `process_file_extracted()` method.
- No two-pass file extraction for the random-access path — everything is a single ordered pass over `_iter_with_data()`.

**Remaining second pass for random-access**: after the main pass, an optional second pass re-opens any hardlink sources that the filter excluded but that were needed. This is the only deferred work, it's O(skipped_sources), and it's explicit.

### Tasks

1. Implement `ExtractionCoordinator` as described above.
2. Update `BaseArchiveReader.extractall()` and `BaseArchiveReader.extract()` to use it.
3. Remove `ExtractionHelper`.
4. Port all tests from `test_extractall.py` to the new logic.

### Acceptance criteria
- All existing extraction tests pass.
- No `pending_*` attributes anywhere in the new code.
- Streaming-mode extraction works correctly with a non-seekable TAR stream.
- Hardlinks and symlinks extracted correctly in both modes.

---

## Phase 5: Public API alignment

**Goal:** the public API matches SPEC.md.

### Tasks

1. **`iter_members_with_streams()` → `stream_members()`** (public rename, keep alias for one release if desired).

2. **`ArchiveInfo`**: add `solid_block_count`, `archive_comment`, `format` fields as per SPEC §4. Update all backends to populate them.

3. **`Member` dataclass**: add `extra: dict[str, Any] = field(default_factory=dict, hash=False, compare=False)`. Add `atime` field. Verify `frozen=True` still works with `hash=False` on `extra`.

4. **`CostReceipt`** (`ListingCost`, `AccessCost`, `StreamCapability`): implement as per SPEC §7. Wire into `open_archive()` return or expose via `reader.get_archive_info()`.

5. **`open_archive()` `streaming` parameter**: already exists in DEV as `streaming_only`, renamed. Verify the deprecation warning for `streaming_only`.

6. **Exception hierarchy**: align to SPEC §6. Verify `ArchiveMemberNotFoundError`, `ArchiveMemberCannotBeOpenedError`, `ArchiveEncryptedError`, `ArchiveCorruptedError`, `ArchiveIOError`, `SameFileError` all exist and are raised in the right places.

7. **`has_random_access()`**: verify it returns `False` for non-seekable TAR, `True` for everything else.

8. **`resolve_link()`**: verify it handles symlink chains up to depth 8, returns `None` for external targets.

9. **Update `__init__.py`** re-exports.

10. **Tests**: `test_types.py` for new `Member` fields; `test_api.py` for `CostReceipt` values per format.

### Acceptance criteria
- `mypy --strict` passes.
- Public API matches SPEC.md §2–§7.
- `CostReceipt` returns correct values for all 7 backends.

---

## Phase 6: Test infrastructure overhaul

**Goal:** no generated binary archives committed to the repo; committed fixtures are documented with JSON sidecars.

### Tasks

1. **Delete committed generated archives** (any `test_archives/*.zip`, `*.tar`, `*.7z`, etc. that are generated by `create_archives.py`). Add them to `.gitignore`.

2. **Add caching to archive generation**: `sample_archive_path` fixture in `conftest.py` caches to `~/.cache/archivey-tests/` (or `$XDG_CACHE_HOME/archivey-tests/`), keyed by `hash(spec + creation_params)`. Regenerates only if missing or `--regen` flag passed.

3. **Committed fixtures** (`tests/fixtures/`):
   - Move existing adversarial archives here; strip any that can be regenerated from `create_adversarial.py`.
   - Add `create_adversarial.py` script with instructions for generating each one (idempotent).
   - For each committed archive `foo.zip`, add `foo.json` sidecar (format documented in ARCHITECTURE §2.8).

4. **Parametrized fixture test**: `test_fixtures.py::test_committed_fixture` — opens each archive in `tests/fixtures/`, reads all members, asserts fields match the JSON sidecar. Runs on all platforms including Windows.

5. **Remove `tests/archivey/` nesting**: flatten to `tests/` (remove the intermediate directory that DEV uses). Update all import paths.

6. **Cross-tool verification** (optional CI job): `tests/verify_with_7z.py` — for each generated archive, runs `7z l -slt` and compares member list to parsed output. Gated behind `--verify-with-7z` flag, skipped if `7z` not in PATH.

### Acceptance criteria
- `git status` after test run shows no new binary files.
- CI generates all archives from scratch in a fresh environment and all tests pass.
- Every file in `tests/fixtures/` has a corresponding `.json` sidecar.

---

## Phase 7: Writing support

**Goal:** `ArchiveWriter` ABC, ZIP and TAR writers, conversion pipeline.

### Tasks

1. **`src/archivey/_writer.py`** — `ArchiveWriter` ABC:
   - `add(path, *, name=None, recursive=True)` — add from filesystem.
   - `add_bytes(data, name, *, mtime=None, mode=None)` — add from bytes.
   - `add_stream(stream, name, *, size=None, mtime=None, mode=None)` — add from stream.
   - `add_member(member, stream)` — preserve a Member's metadata exactly.
   - `add_members(reader)` — calls `reader.stream_members()` and `add_member` in a loop.
   - `close()`, `__enter__`/`__exit__`.

2. **`ZipWriter`** in `formats/zip_reader.py` (or `zip_writer.py`):
   - `add_stream()`: uses `ZipFile.open(name, 'w')` (Python 3.6+) to avoid pre-buffering for CRC.
   - `add_bytes()`: `ZipFile.writestr()`.
   - `add()`: `ZipFile.write()` for files, `os.walk` for directories.

3. **`TarWriter`** in `formats/tar_reader.py`:
   - `add_stream()`: `TarFile.addfile(tarinfo, stream)`.
   - `add()`: `TarFile.add()` with `recursive=False` for files, walking for directories.

4. **`create_archive()` in `core.py`** — analogous to `open_archive()`.

5. **Tests `test_writing.py`** and **`test_conversion.py`**:
   - Round-trip: write then read back; verify member names, sizes, content, mtime.
   - `stream_members()` in conversion: verify memory profile (no full buffering).
   - `add_members()` across formats: `tar.gz` → `zip`, `zip` → `tar`.

### Acceptance criteria
- ZIP and TAR round-trip correctly.
- `add_members(reader)` produces an archive with correct content for all reader formats.
- No full archive buffering during stream-to-stream conversion (verify via `tracemalloc`).

---

## Phase 8: 7z and RAR streaming improvements

**Goal:** integrate DEV's best streaming approaches with the new interface.

DEV already has two excellent implementations:
- **7z**: `StreamingFile` with thread+queue — truly streaming, O(queue_bound) memory.
- **RAR**: `RarStreamReader` with `unrar p` pipe demultiplexer — single subprocess, CRC-validated.

### Tasks

1. **7z `_iter_with_data()` override**: use the thread+queue `StreamingFile` approach from DEV rather than the "extract folder to tmpdir" approach from ARCHITECTURE.md. This gives true streaming with bounded memory without any disk I/O. Verify the per-folder memory release still happens (the queue bound acts as the buffer).

2. **RAR `_iter_with_data()` override**: use the `unrar p` pipe demultiplexer approach from DEV. This avoids tmpdir entirely — data flows directly from `unrar` stdout to the caller. Verify CRC checking is retained.

3. **Both backends**: ensure `_open_member()` (for random access) still uses the tmpdir/cache approach — the pipe demultiplexer is sequential-only.

4. **Tests**:
   - 7z solid: assert `stream_members()` over a solid archive decompresses each folder exactly once (count `py7zr.SevenZipFile.extract` calls).
   - RAR solid: assert `stream_members()` spawns exactly one `unrar p` subprocess.
   - Both: assert `stream_members()` peak memory is bounded (instrument with `tracemalloc`).
   - Both: `open()` (random access) still works after `stream_members()` returns.

### Acceptance criteria
- 7z solid: one `py7zr.extract()` call per folder during `stream_members()`.
- RAR solid: one `unrar p` process for the full `stream_members()` pass.
- `open()` unaffected by whether `stream_members()` has been called.

---

## Phase 9: Zstandard and extended compression

**Goal:** `.zst` and `.tar.zst` support.

### Tasks

1. **`SingleFileReader`**: add `ZST` using `zstandard` package (gated behind `[zstd]` extra).
2. **`TarReader`**: add `.tar.zst` via `tarfile` + `zstandard` codec hook.
3. **`TarWriter`**: `w:zst` mode.
4. **Format detection**: add `.zst` magic bytes.
5. Tests: skip if `zstandard` not installed.

---

## Phase 10: Polish, documentation, and packaging

**Goal:** `0.2.0` release-ready.

### Tasks

1. **README.md** — quick-start: open, iterate, stream_members, extract, create, convert. Install with extras.
2. **API docstrings** — Google-style on all public methods. Build with `mkdocstrings`.
3. **`archivey.__version__`** via `importlib.metadata`.
4. **`archivey.list_formats()`** — returns available formats based on installed extras.
5. **`CHANGELOG.md`** — fill in `## [0.2.0]` section.
6. **CI matrix** — Python 3.11, 3.12, 3.13; ubuntu-latest + windows-latest. Cache generated test archives per Python version.
7. **Coverage** — `fail_under = 90`, report on PRs.

---

## Cross-cutting concerns

### What stays from DEV in each phase

| Area | Keep | Change |
|------|------|--------|
| ZIP backend | Logic, edge cases | Interface (rename methods) |
| TAR backend | Logic, PAX, all variants | Interface |
| 7z backend | Thread+queue StreamingFile, folder cache | Interface, remove `for_iteration` |
| RAR backend | Pipe demultiplexer, CRC validation | Interface |
| ISO, dir backends | Logic | Interface |
| Single-file readers | All | None |
| Format detection | All | None |
| ArchiveStream | All | None |
| DecompressorStream/XZ/lzip | All | Move to streams/ |
| RewindableStreamWrapper | All | Move to streams/ |
| BinaryIOWrapper | Outer shape | Remove method-replacement |
| ExtractionHelper | `apply_member_metadata`, `check_overwrites` | Rewrite the rest |
| BaseArchiveReader | Registration, link resolution, iter logic | Rename interface, remove hooks |
| Test declarative specs | All of sample_archives.py | Remove committed generated binaries |

### Risk areas

- **Hardlink edge cases in streaming mode**: TAR guarantees target precedes link, but 7z does not. The new `ExtractionCoordinator` must be explicit about what it supports per mode.
- **py7zr link target availability**: the `_prepare_member_for_open` hook removal requires storing link-target resolution in `raw_info`. Verify this works for all 7z archives with symlinks.
- **`BinaryIOWrapper` simplification**: some format backends may rely on the hot-path method replacement for performance. Benchmark before removing.
- **Cache invalidation for generated test archives**: the cache key must include the archivey version and library versions, not just the spec hash, to avoid stale archives after upgrades.
