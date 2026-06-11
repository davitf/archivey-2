# Archivey — Implementation Plan

> A phased delivery plan. The goal is a working, safe, well-tested library. Phases are sized for roughly 1–3 days of focused work each. Later phases can be reordered based on what formats are highest priority.
>
> **Guiding principle:** each phase ends with a mergeable, tested, mypy-clean state. No half-built phases.

---

## Phase 0: Project Scaffold

**Goal:** a clean project skeleton that all subsequent phases build on.

### Tasks

1. **`pyproject.toml`** (PEP 517 / PEP 621):
   - Build backend: `hatchling` (zero-config, src layout native).
   - `[project]` metadata: name, version `0.1.0.dev0`, Python `>=3.11`.
   - `[project.optional-dependencies]`: `7z`, `rar`, `iso`, `zstd`, `all`, `dev`.
   - `[tool.pytest.ini_options]`: testpaths, coverage, markers.
   - `[tool.mypy]`: `strict = true`, `python_version = "3.11"`.
   - `[tool.ruff]`: `select = ["E", "F", "I", "UP", "B", "SIM"]`, line-length 100.
   - `[tool.coverage.report]`: `fail_under = 90`.

2. **Src layout**:
   ```
   src/archivey/__init__.py   # empty for now
   src/archivey/py.typed
   tests/__init__.py
   tests/conftest.py
   ```

3. **CI workflow** (`.github/workflows/ci.yml`):
   - Matrix: Python 3.11, 3.12, 3.13; ubuntu-latest + windows-latest.
   - Steps: install dev deps → ruff check → mypy → pytest with coverage.

4. **`CHANGELOG.md`** stub with `## [Unreleased]` section.

5. **Verify**: `pytest tests/` passes (0 tests, 0 failures); `mypy src/` passes.

---

## Phase 1: Types, Errors, and Detection

**Goal:** all public types, the exception hierarchy, and format detection are implemented and tested. Nothing reads an actual archive yet.

### Tasks

1. **`src/archivey/_errors.py`** — full exception hierarchy as specified in SPEC §6.
   - Each exception class gets `format`, `member_name`, and `message` attributes.
   - `__str__` renders all fields.

2. **`src/archivey/_types.py`** — all enums and dataclasses:
   - `ArchiveFormat`, `MemberType`, `CompressionAlgo`, `CompressionMethod`
   - `ListingCost`, `AccessCost`, `StreamCapability`, `Intent`
   - `ExtractionPolicy`, `OverwritePolicy`
   - `Member` frozen dataclass (full field set)
   - `ArchiveInfo`, `CostReceipt`, `FormatInfo`
   - `CompressionSpec` with class-level constants

3. **`src/archivey/_detection.py`**:
   - `MAGIC_TABLE: list[tuple[bytes, int, ArchiveFormat]]` — magic bytes, offset, format.
   - `detect_format(source) -> FormatInfo` — reads peek, matches magic, falls back to extension.
   - `PeekableStream` — wraps non-seekable `BinaryIO`, buffers first N bytes, transparent replay.
   - `DETECTION_LIMIT = 4096`, `ISO_DETECTION_LIMIT = 32774`.

4. **Tests**:
   - `test_types.py`: construct every type, verify frozen, verify `__repr__`.
   - `test_errors.py`: raise and catch every exception type; verify `__cause__` chain is preserved.
   - `test_detection.py`: feed raw magic bytes for every format → correct `ArchiveFormat`; test `PeekableStream` replay; test ISO extended limit; test extension fallback; test conflict (magic wins).

### Acceptance criteria
- `mypy --strict` passes.
- All detection tests pass on Linux and Windows.
- 100% branch coverage on `_detection.py`.

---

## Phase 2: Backend Infrastructure + ZIP

**Goal:** the backend registry, the `ArchiveReader` ABC, and a fully functional ZIP backend (read only).

### Tasks

1. **`src/archivey/backends/_base.py`** — `Backend` ABC (as in ARCHITECTURE §2.2).

2. **`src/archivey/backends/__init__.py`** — `BackendRegistry` singleton:
   - `register(cls)`, `detect_backend(peek, path, intent) -> type[Backend]`.
   - Raises `UnsupportedFormatError` with install hint when optional backend is detected but unavailable.

3. **`src/archivey/_reader.py`** — `ArchiveReader` ABC:
   - Implements all public methods in terms of `_iter_members()`, `_open_member()`, and `_iter_with_data()`.
   - `open()` and `read()` add link-following on top of `_open_member()` (cycle guard, max depth 8).
   - `stream_members()` delegates to `_iter_with_data()`.
   - Default `_iter_with_data()`: naive `_open_member()` per member — correct for non-solid formats; overridden by solid-archive backends.
   - `add_members()` in `ArchiveWriter` calls `reader.stream_members()` so conversion always takes the bounded-memory path.
   - Lazy materialization cache with Sequential guard.
   - `__enter__`/`__exit__` delegates to `close()`.

4. **`src/archivey/backends/_zip.py`** — `ZipReader`:
   - Implement `_iter_members()`: iterate `ZipFile.infolist()`, map each `ZipInfo` → `Member`.
   - `mode` from `external_attr >> 16` (only if `create_system == 3`).
   - `modified` from `date_time` as naive `datetime`; upgrade to aware if ZIP64 NTFS extra present.
   - Symlink detection via Unix extra field `0x000A`.
   - `_open_member()`: `ZipFile.open(member.original_name)`.
   - Non-seekable ZIP: spool to `SpooledTemporaryFile(max_size=50*1024*1024)`.
   - `CostReceipt`: `O1`, `DIRECT`, `SEEKABLE` (or `REPLAY_ONLY` if spooled).
   - Register `ZipBackend` at module bottom.

5. **`src/archivey/__init__.py`** — wire up `open()`, `detect_format()`:
   ```python
   def open(source, *, format=None, intent=Intent.AUTO, ...) -> ArchiveReader:
       peek = _detection.peek_bytes(source)
       fmt = format or _detection.detect_format_from_peek(peek, source).format
       backend_cls = registry.detect_backend(peek, source if isinstance(source, Path) else None, intent)
       return backend_cls().open_read(source, intent, ...)
   ```

6. **Tests `test_zip.py`**:
   - Open real ZIP files (created in fixture with `zipfile`).
   - Iterate members, verify `Member` fields.
   - Random access by name.
   - `read()` returns correct bytes.
   - `open()` returns readable stream.
   - Unix permissions round-trip.
   - Symlink detection.
   - Non-seekable stream (spooling).
   - Encrypted ZIP raises `EncryptionError`.
   - Corrupt ZIP raises `CorruptionError`.

### Acceptance criteria
- Can open and read any standard ZIP file.
- All member fields correctly populated (including `None` where format doesn't provide).
- `mypy --strict` clean.

---

## Phase 3: TAR Backend

**Goal:** TAR backend for all variants (bare, gz, bz2, xz) and the single-file compressor backend.

### Tasks

1. **`src/archivey/backends/_tar.py`** — `TarReader`:
   - `_iter_members()`: `TarFile.next()` loop, map `TarInfo` → `Member`.
   - Handle PAX extended headers (`pax_headers`): override mtime (full precision), atime, charset.
   - `MemberType` from TAR type byte table.
   - Hardlink members: `type=HARDLINK`, `link_target=TarInfo.linkname`.
   - `CostReceipt`: `ON` listing, `SOLID` for compressed variants, `DIRECT` for plain `.tar`.
   - Truncation detection: check for end-of-archive marker after final member.
   - Handle GNU long name/link extension headers transparently (tarfile does this).
   - `_open_member()`: `TarFile.extractfile(tarinfo)`.
   - Detect TAR sub-format from `tarfile.OPEN_METH` candidates.

2. **`src/archivey/backends/_single.py`** — `SingleFileReader`:
   - Formats: `GZ`, `BZ2`, `XZ`; later `ZST` if `[zstd]` installed.
   - Construct a single `Member` with name inferred from path.
   - `_iter_members()` yields the single member.
   - `_open_member()`: returns `gzip.open()` / `bz2.open()` / `lzma.open()`.
   - `CostReceipt`: `O1` (one member), `SOLID`, appropriate seek capability.

3. **Tests `test_tar.py`**:
   - Plain `.tar`, `.tar.gz`, `.tar.bz2`, `.tar.xz`.
   - PAX headers: unicode names, high-precision mtime, subsecond access time.
   - Symlinks, hardlinks, directories.
   - Device nodes → `MemberType.OTHER`.
   - Truncated TAR → `TruncatedError` / warning (configurable).
   - Non-seekable `.tar.gz` stream.

4. **Tests `test_single.py`**:
   - `.gz`, `.bz2`, `.xz` files.
   - Name inference.
   - `read()` returns correct decompressed bytes.

---

## Phase 4: Safe Extraction

**Goal:** the extraction system — filters, policies, bomb detection — is implemented and forms the security core of the library.

### Tasks

1. **`src/archivey/_filters.py`**:
   - `check_universal(member: Member) -> None` — raises appropriate `FilterRejectionError` subclass.
   - `transform_strict(member: Member) -> Member` — returns new Member with adjusted permissions.
   - `transform_standard(member: Member) -> Member`.
   - `POLICY_TRANSFORMS` dict.
   - `resolve_within(path: Path, root: Path) -> Path` — raises `SymlinkEscapeError` if not within root.

2. **`src/archivey/_extraction.py`**:
   - `BombTracker` class.
   - `extract_member(...)` single-member extraction function.
   - `extract_all(reader, dest, policy, overwrite, bomb_tracker, on_progress) -> list[ExtractionResult]`.
   - Two-pass symlink/hardlink handling.
   - Metadata restoration (mtime via `os.utime`, permissions via `os.chmod`).
   - Atomic directory creation.

3. **`src/archivey/_progress.py`** — `ExtractionProgress`, `ExtractionResult`, `ExtractionStatus`.

4. **Wire `extract_all` / `extract` into `ArchiveReader` ABC and `archivey.extract()`**.

5. **Adversarial test corpus** — create binary test fixtures:
   - `tests/corpus/adversarial/path_traversal.zip` — member named `../../evil.txt`.
   - `tests/corpus/adversarial/absolute_path.tar` — member named `/etc/passwd`.
   - `tests/corpus/adversarial/symlink_escape.zip` — symlink → `../../outside`.
   - `tests/corpus/adversarial/bomb_flat.zip` — 1 MiB zeros compressed to ~1 KiB, ratio ~1000:1.
   - `tests/corpus/adversarial/corrupt.zip` — truncated central directory.
   - A Python script `tests/corpus/create_adversarial.py` that regenerates these.

6. **Tests `test_extraction.py`**:
   - All adversarial corpus cases.
   - `STRICT` policy strips executable bit, setuid, uid/gid.
   - `STANDARD` policy preserves executable but strips setuid.
   - `TRUSTED` policy passes through all permissions.
   - Overwrite policies: `ERROR`, `SKIP`, `REPLACE`.
   - Bomb detection: ratio trigger, total bytes trigger.
   - Progress callback receives correct values.
   - Two-pass hardlink extraction.
   - Symlink post-creation verification.

### Acceptance criteria
- All adversarial tests pass.
- Extraction from ZIP and TAR produces correct files on disk.
- No path escapes under any policy.

---

## Phase 5: Writing

**Goal:** `ArchiveWriter` ABC + ZIP and TAR writers + conversion pipeline.

### Tasks

1. **`src/archivey/_writer.py`** — `ArchiveWriter` ABC:
   - `add()`, `add_bytes()`, `add_stream()`, `add_member()`, `add_members()`.
   - `__enter__`/`__exit__` with clean `close()` on exception.
   - `add_members()` default implementation: iterate reader, stream each member.

2. **`src/archivey/backends/_zip.py`** — `ZipWriter`:
   - `add_stream()`: uses `SpooledTemporaryFile` per member to compute CRC before writing local header (required by `zipfile`). Alternatively: use `ZipFile.open(name, 'w')` write mode (Python 3.6+) which handles CRC via data descriptor. The latter is preferred as it avoids buffering.
   - `add_bytes()`: `ZipFile.writestr()`.
   - `add()` for paths: `ZipFile.write()` for files, recursive for directories.
   - Compression spec → `zipfile` compression constant mapping.

3. **`src/archivey/backends/_tar.py`** — `TarWriter`:
   - `add_stream()`: creates `TarInfo`, sets fields from metadata, calls `TarFile.addfile()`.
   - `add()` for paths: `TarFile.add()` with `recursive=True/False`.
   - Compression spec → tarfile mode string (`w:gz`, `w:bz2`, `w:xz`).

4. **Wire `archivey.create()`** in `__init__.py`.

5. **Tests `test_writing.py`**:
   - Create ZIP and TAR archives with files, bytes, and streams.
   - Verify member names, sizes, metadata round-trip.
   - Directory recursion.
   - Compression level respected.

6. **Tests `test_conversion.py`**:
   - `tar.gz` → `zip`: verify identical member names, sizes, and content.
   - `zip` → `tar.gz`.
   - Large file (stream-based, no full buffering) — verified via memory tracking.
   - Members with unsupported types for target format are skipped with warning.

---

## Phase 6: Equivalence Matrix and CI Corpus

**Goal:** prove the "one interface, same results" promise with an automated test matrix.

### Tasks

1. **`tests/corpus/equivalence/`**: a canonical directory tree:
   ```
   canonical/
   ├── README.txt            (ASCII text)
   ├── unicode_Ñoño.txt      (UTF-8 filename)
   ├── subdir/
   │   └── nested.bin        (binary data, known CRC)
   ├── empty_dir/
   ├── link -> README.txt    (symlink)
   └── executable.sh         (mode 0o755)
   ```
   Packaged as `.zip`, `.tar.gz`, `.tar.bz2`, `.tar.xz` (all committed as binary fixtures).
   Script `tests/corpus/create_equivalence.py` regenerates from the canonical directory.

2. **`tests/test_equivalence.py`** — equivalence matrix:
   - For each format, open and read all members.
   - Assert that `member.name`, `member.type`, `member.size`, `crc32` (for files) match across all formats.
   - Assert that `member.modified` is equivalent (within 2-second tolerance for DOS timestamps).
   - Assert that `member.mode & 0o777` is equivalent (within format limitations).
   - Document known limitations inline (e.g. ZIP without Unix extra has no permission bits).

3. **Hypothesis-based property tests** (`test_properties.py`):
   - Generate random `Member` objects and verify `check_universal()` correctly rejects/accepts.
   - Generate random path strings and verify normalization is idempotent (normalizing twice == normalizing once).

---

## Phase 7: Optional Backends

**Goal:** 7z, RAR, ISO, and Directory backends. Each is gated behind its optional dependency.

### Tasks

1. **`src/archivey/backends/_7z.py`** (`[7z]` extra → `py7zr`):
   - `SevenZReader._iter_members()`: `py7zr.SevenZipFile.list()` — cheap, metadata only from pre-parsed header.
   - Map `py7zr.FileInfo` → `Member`: extract `"folder"` reference for caching; compression from `archiveinfo().method_names`/`Folder.coders`.
   - `_open_member()`: lazy per-folder cache. On miss: `sz.extract(targets=folder_files, factory=SpooledFactory()); sz.reset()`. Populate `self._folder_cache[folder]`. On hit: `buf.seek(0); return buf`. Cache grows until `close()`.
   - `_iter_with_data()` override: iterate folders one at a time. Extract folder N into local `folder_bufs`, yield all its `(Member, stream)` pairs, then let `folder_bufs` go out of scope before extracting folder N+1. Peak memory = largest single folder.
   - Both paths use `_Py7zIOAdapter(SpooledTemporaryFile(max_size=64<<20))` — must be seekable (py7zr calls `seek(0)` after write; does not read back).
   - `CostReceipt`: `solid_block_count` from `archiveinfo().blocks`; `is_solid` from `archiveinfo().solid`.
   - `SevenZWriter`: use `py7zr.SevenZipFile(mode='w')` with `writef()` and `writestr()`.

2. **`src/archivey/backends/_rar.py`** (`[rar]` extra → `rarfile` + system `unrar`):
   - `RarReader._iter_members()`: `rarfile.RarFile.infolist()` — O(1), central dir parsed upfront.
   - Timestamp: RAR4 `ftime` → naive datetime; RAR5 `mtime` → timezone-aware UTC datetime.
   - `_open_member()`: non-solid → `rarfile.open()` (hack path); solid → `_ensure_solid_cache()` on first call (runs `unrar x` once), return `open(cache_dir / member.name, 'rb')`. Cache persists until `close()`.
   - `_iter_with_data()` override (solid): run `unrar x` to fresh tmpdir, yield `(Member, file_handle)` pairs, clean up tmpdir in `finally` when the generator closes. Disk freed as soon as caller finishes `stream_members()` loop.
   - Both paths use `rarfile.tool_setup()` to locate the binary; respect user-configured tool path.
   - Handle missing binary: catch `rarfile.RarCannotExec` → `UnsupportedFormatError` with install hint.
   - `SUPPORTS_WRITE = False` — raise `UnsupportedOperationError` on write attempt.

3. **`src/archivey/backends/_iso.py`** (`[iso]` extra → `pycdlib`):
   - `IsoReader._iter_members()`: walk `pycdlib.PyCdlib` with Rock Ridge → Joliet → Plain priority.
   - Namespace auto-selection: try `rr_path`, fall back to `joliet_path`, then `iso_path`.
   - `_open_member()`: `pycdlib_iso.get_file_from_iso_fp(iso_path=...)`.
   - Report selected namespace in `ArchiveInfo.extra["iso.namespace"]`.
   - ISO detection extended limit (32 774 bytes).

4. **`src/archivey/backends/_dir.py`**:
   - `DirReader._iter_members()`: `os.walk()` recursively, map `stat()` → `Member`.
   - `CostReceipt`: `O1`, `DIRECT`, `SEEKABLE`.
   - Useful as a "source" for the conversion pipeline.

5. **Tests `test_7z.py`**, **`test_rar.py`**, **`test_iso.py`**, **`test_dir.py`**:
   - Each uses `pytest.importorskip()` to skip if optional dep absent.
   - Standard read/iterate/extract cycle via normal `for member in ar: ar.open(member)`.
   - Format-specific quirks (solid blocks, RAR4/5 timestamps, ISO namespaces).
   - **7z solid**: mock `py7zr.SevenZipFile.extract` to count calls; assert one call per folder (solid block), not per file; verify `stream_members()` uses folder-by-folder loop.
   - **RAR solid**: mock `subprocess.run`; assert exactly one `unrar x` call for `stream_members()`, one for `open()` path (first access triggers cache). Verify `stream_members()` cleans up tmpdir after iteration; `open()` path cleans up on `close()`.

6. **Tests for sample usage patterns** (`test_patterns.py`):
   - Hash-all-files via `stream_members()`: verify correctness across all formats.
   - Hash-all-files via `for member in ar: ar.open(member)`: also correct, verify same hashes.
   - Memory profile test for solid 7z: verify folder-by-folder (instrument `SpooledTemporaryFile` creation counts).
   - `open(symlink_member)` follows link transparently: test with TAR, ZIP symlinks.
   - `open(hardlink_member)` follows hardlink transparently: test with TAR hardlinks.
   - Link cycle in archive: verify `ReadError` raised after depth limit.
   - Link to external (missing) target: verify `ReadError`.

---

## Phase 8: Cost Receipt, Intent, and Streaming Hardening

**Goal:** the `CostReceipt` system is fully functional and intent-based optimization works.

### Tasks

1. **Verify `CostReceipt` for all backends** — write a test that opens one of each format and asserts the correct `listing_cost`, `access_cost`, and `stream_capability` values.

2. **`Intent.RANDOM` enforcement**: if `intent=RANDOM` and the backend has `access_cost=SOLID`, raise `UnsupportedOperationError` with the cost receipt details in the message.

3. **`Intent.SEQUENTIAL` enforcement**: forbid `.members()`, `__len__`, `__getitem__`. Verify with tests.

4. **`Intent.AUTO` behavior**: if the source is non-seekable, auto-select sequential; if seekable, allow both. Document this in `archivey.open()` docstring.

5. **Streaming hardening** — test every backend with a `FakeNonSeekable` wrapper:
   - TAR backends: pass (streaming mode).
   - ZIP: triggers spool behavior.
   - 7z/RAR/ISO: raises `UnsupportedOperationError` with `REQUIRES_SEEK = True`.

---

## Phase 9: Zstandard and Extended Compression Support

**Goal:** `.zst` and `.tar.zst` support, plus any remaining compression methods.

### Tasks

1. **`src/archivey/backends/_single.py`** — add `ZST` format using `zstandard` package (if installed).
2. **`src/archivey/backends/_tar.py`** — add `.tar.zst` detection and `tarfile` mode `r|zst` / `w|zst` (requires `zstandard` hooked into `tarfile`).
3. Zstandard frame index: if available (seekable source), use it for random access. If not, mark `AccessCost.SOLID`.
4. `CompressionAlgo.ZSTD` round-trip in tar writer.
5. Tests with actual `.zst` files; skip if `zstandard` not installed.

---

## Phase 10: Polish, Documentation, and Packaging

**Goal:** library is ready for a `0.1.0` release.

### Tasks

1. **`README.md`** — quick-start examples: open, iterate, extract, create, convert. Install instructions with extras.

2. **API documentation** — Google-style docstrings on all public methods. Build with `pdoc` or `mkdocstrings`.

3. **`archivey.__version__`** — wired from `pyproject.toml` via `importlib.metadata`.

4. **`archivey.list_formats()`** — returns currently available formats (based on installed extras).

5. **Performance benchmarks** (`benchmarks/` directory, not part of CI):
   - Extract 1 000 small files from ZIP vs TAR.
   - Extract one large file from `.tar.gz` vs `.7z` vs `.zip`.
   - Profile with `cProfile` and check no hot paths in pure Python.

6. **Final coverage pass** — ensure all edge cases in the adversarial corpus are covered by tests. Target: 90%+ overall, 100% on `_filters.py` and `_errors.py`.

7. **`CHANGELOG.md`** — fill in `[0.1.0]` section.

8. **Tag `v0.1.0`** after all CI passes on all matrix combinations.

---

## Milestone Summary

| Phase | Deliverable | Key test signal |
|-------|-------------|-----------------|
| 0 | Scaffold | CI green, 0 tests |
| 1 | Types + Detection | Magic bytes unit tests |
| 2 | ZIP read | Read a real ZIP |
| 3 | TAR read + single-file | Read tar.gz, .gz, .bz2, .xz |
| 4 | Safe extraction | Adversarial corpus all pass |
| 5 | Writing + conversion | tar.gz → zip round-trip |
| 6 | Equivalence matrix | Same logical tree == same Member |
| 7 | Optional backends | 7z, RAR, ISO, Directory |
| 8 | Cost receipt + intent | All cost receipts correct |
| 9 | Zstandard | .tar.zst read/write |
| 10 | Polish + 0.1.0 | Docs, benchmarks, release |

---

## Open Questions (for revision)

These are design points that should be discussed before implementation:

1. **Encoding strategy for legacy ZIPs**: The `encoding` parameter defaults to `"utf-8"`, but many legacy Windows ZIPs use `cp437` or `cp1252`. Should `archivey.open()` accept `encoding="auto"` that attempts chardet detection on non-UTF-8 paths? (Adds `chardet` as optional dep.)

2. **Password handling**: Should passwords be `str` (and we encode with the archive's encoding) or `bytes` (fully explicit)? Both? ZIP and RAR use different password semantics. Recommendation: accept `str | bytes`, encode `str` with `"utf-8"` for 7z/RAR5, `"cp437"` for ZIP.

3. **Multivolume RAR/7z**: Currently flagged in `ArchiveInfo.is_multivolume` but no read support. Should v1 at least detect the volumes automatically if they follow the standard naming convention (`.part1.rar`, `.part2.rar`)?

4. **Windows junction points**: Are they common enough to warrant actual recreation on Windows via `os.symlink()` with `target_is_directory=True`? Or just extract as a normal symlink and let Windows handle it?

5. **`max_ratio` default**: 1000:1 is conservative. Some legitimate archives of highly repetitive data (database dumps, log files) can exceed this. Should it be higher (e.g. 10000:1) or should the default be `None` (no ratio limit, only total bytes limit)?

6. **`on_rejection` parameter**: Currently `FilterRejectionError` is always raised. Should there be an `OnRejection.WARN` mode that logs and skips rather than raising? This would be useful for "extract everything possible from untrusted archive, skip dangerous entries." High value for production use cases.

7. **Streaming ZIP write without spooling**: Python's `ZipFile.open(name, 'w')` (3.6+) writes data descriptors, so we don't need to pre-compute CRC. But `zipfile` doesn't expose a streaming interface for very large members. Should we detect member size > threshold and use a different approach?

8. **Directory backend write**: Should `archivey.create(dest, ArchiveFormat.DIRECTORY)` be a "writer" that just copies files to a directory? This makes the conversion pipeline symmetric and useful for "unarchive everything to a directory" workflows.

9. **Custom streaming 7z reader (v2 item)**: The py7zr wrapper caches per solid block in `SpooledTemporaryFile`. For archives with very large solid blocks (e.g. a single 2 GiB solid block), this means buffering 2 GiB. A native streaming reader using py7zr's `archiveinfo.py` header parser + stdlib `lzma.LZMADecompressor` + the `bcj` C extension would give true streaming with no buffering. Should this be planned as an optional `[7z-native]` extra backend in v2, or is the SpooledTemporaryFile approach acceptable indefinitely?

10. **Solid RAR disk requirement**: The one-shot `unrar x` approach requires disk space equal to the uncompressed archive size. For read-only inspection (e.g. listing hashes), this is wasteful. Is there a use case where the disk requirement is a problem? If so, a streaming stdin-pipe approach (piping `unrar p` output to a demultiplexer) is theoretically possible but complex to implement correctly.
