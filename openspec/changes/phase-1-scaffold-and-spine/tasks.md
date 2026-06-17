# Tasks — Phase 1: Project scaffold, spine, and test harness

> Run tools through uv: `uv sync`, `uv run mypy`, `uv run pytest`, `uv run ruff`.
> The package stays pip-installable; uv is the workflow, not a dependency.
>
> Clean-slate: the spine is written **fresh** to `ARCHITECTURE.md` / `SPEC.md`;
> `archivey-dev` is reference + frozen oracle only, not a copy baseline.

## 1. Obtain the DEV source (reference + oracle)

> A plain HTTPS `git clone` works from this environment. The GitHub API / WebFetch
> is unauthenticated-rate-limited and returns `403` — do not use it to conclude the
> repo is private. See the "Reference repository" section of the root `CLAUDE.md`.

- [ ] 1.1 Clone DEV into a scratch location outside the project tree:
      `git clone https://github.com/davitf/archivey-dev.git /tmp/archivey-dev`.
- [ ] 1.2 Check out a pinned commit SHA (known-good:
      `730275b7a755f8b5b8d08d3d4d9b267b5bdadb0d`) so references are reproducible.
- [ ] 1.3 Record the source SHA in the Phase 1 commit message / PR for traceability.
- [ ] 1.4 If the clone fails, retry over HTTPS with backoff; only then surface the
      blocker. DEV is used here as the frozen oracle and as the reference for
      porting leaf logic in later phases — it is **not** copied wholesale.

## 2. Project configuration

- [ ] 2.1 `pyproject.toml` with `[build-system]` = `hatchling`.
- [ ] 2.2 `[project]`: name `archivey`, version `0.2.0.dev0`,
      `requires-python = ">=3.11"`, description, license, readme.
- [ ] 2.3 `[project.optional-dependencies]` **exactly** per `packaging-and-extras`:
      `7z`, `rar`, `crypto`, `7z-write`, `iso`, `zstd`, `lz4`, `cli`, `seekable`,
      `recommended-lite`, `recommended`, `all` (the spec's table is the source of
      truth for each extra's dependency list and the union definitions).
- [ ] 2.4 `[dependency-groups]` `dev` (PEP 735): pytest, mypy, ruff, coverage, the
      archive-generation libs, **and the test oracles `py7zr` + `rarfile`**.
- [ ] 2.5 Tool config: `[tool.mypy]` `strict = true`, `python_version = "3.11"`;
      `[tool.ruff]`; `[tool.coverage]`.
- [ ] 2.6 `.python-version` (`3.11`) and any `[tool.uv]` settings.
- [ ] 2.7 Generate and commit `uv.lock`.

## 3. Package layout & logging

- [ ] 3.1 `src/archivey/{internal,formats}/` with `__init__.py` files; public
      `src/archivey/__init__.py`; add `py.typed` (PEP 561).
- [ ] 3.2 Establish the `archivey` logger hierarchy; the library installs **no**
      handlers and emits nothing by default (`logging` spec).

## 4. Spine — written fresh (no format backends yet)

- [ ] 4.1 `BaseArchiveReader` ABC in `ARCHITECTURE.md` vocabulary: `_iter_members`,
      `_iter_with_data`, `_open_member` (**no** `for_iteration`), class attributes
      `_SUPPORTS_RANDOM_ACCESS` / `_MEMBER_LIST_UPFRONT` (TAR resolves the former at
      `__init__` from source seekability), **no** `_prepare_member_for_open` hook;
      assign `member_id` at registration; public surface (`stream_members(members)`
      — selector only, **no** transform filter; `read`/`open`; `extract_all`
      signature); link resolution by **cycle-detection** (visited-set, no depth
      limit) that fills `link_target_member` and raises `LinkTargetNotFoundError`
      on a missing target. (`extract_all` *body* — coordinator/BombTracker — is
      Phase 4; the method exists here as a deferred stub.)
- [ ] 4.2 `ReadBackend` / `WriteBackend` ABCs (split) + registry with separate
      `register_reader`/`reader_for_format` and `register_writer`/`writer_for_format`;
      import-time self-registration; backends declare `MAGIC` (`(offset, bytes)`) /
      `EXTENSIONS` as data and `REQUIRES_SEEK`; **selection by format**, not a
      per-backend `detect(peek)`. (The central detector that consumes the magic
      table is Phase 3; here the registry + ABCs + format→backend lookup exist.)
- [ ] 4.3 Public-API skeleton: `open_archive(..., encoding: str | None = None)`, the
      `ArchiveReader` surface, the context-manager / `close()` lifecycle, and the
      `MemberSelector` / `MemberFilter` type aliases. (Actual detection wiring —
      PeekableStream + `detect_format` — lands in Phase 3.)
- [ ] 4.4 Data model: `ArchiveMember` (**mutable** `@dataclass`, caller-read-only,
      `.replace()` copy-on-edit, **unhashable**; `raw_name: bytes | None`;
      `link_target_member`; `hashes` + `extra` excluded from `__eq__`; `member_id` /
      `archive_id` properties; read-only helpers `is_file`/`is_dir`/`is_link`/
      `is_other`/`is_junction`; `comment` / `create_system` (`CreateSystem` enum) /
      `windows_attrs` fields; **no** `zipfile`-compat aliases `date_time`/`CRC`/`mtime`),
      `ArchiveInfo` (incl. `is_solid`), `ArchiveFormat` = `(container,
      stream)` frozen pair (`ContainerFormat` × `StreamFormat` + named class-vars),
      `MemberType`, `CompressionAlgo` (extensible, incl. `BROTLI`) /
      `CompressionMethod`, and member-name normalization (`name` decoded+normalized,
      `raw_name` verbatim bytes). Field names follow the **spec** (`name`, `size`,
      `compressed_size`, `modified`, …), not DEV's (`filename`/`file_size`/…).
- [ ] 4.5 `ArchiveyError` hierarchy (`error-handling` spec): single root; required
      attributes `message` / `source_format` / `archive_name` / `member_name` /
      `__cause__`; the per-**library** translator + central context-stamping wrapper
      in the ABC; members incl. `StreamNotSeekableError` (under `OpenError`),
      `LinkTargetNotFoundError` (under `ReadError`), `UnsupportedFeatureError`,
      `PackageNotInstalledError`, `UnsupportedOperationError`. Genuine
      `OSError`/`KeyboardInterrupt`/`MemoryError` propagate unchanged.
- [ ] 4.6 `Intent` enum + `CostReceipt` types: `ListingCost`
      (`INDEXED`/`REQUIRES_SCANNING`/`REQUIRES_DECOMPRESSION`), `AccessCost`
      (`DIRECT`/`SOLID`), `StreamCapability` (`SEEKABLE`/`FORWARD_ONLY`),
      `solid_block_count`, `notes`. `is_solid` lives on `ArchiveInfo`.

## 4b. First backend — directory pseudo-backend (validates the spine)

- [ ] 4b.1 `formats/directory_reader.py`: a `ReadBackend` + `BaseArchiveReader`
      subclass that walks a filesystem directory, yields one `ArchiveMember` per
      entry (file/dir/symlink), reads file data via `open()`, and resolves in-archive
      symlinks through the ABC's link-following. Needs **no** codec layer and **no**
      magic detection. Registered for `ArchiveFormat.DIRECTORY`; `open_archive(path)`
      selects it when the source is an existing directory.
- [ ] 4b.2 Its `CostReceipt`: `listing_cost=INDEXED`, `access_cost=DIRECT`,
      `stream_capability=SEEKABLE`, `is_solid=False`.

> With the directory backend wired, the spine is exercised end-to-end (iterate →
> `read`/`open` → link resolution → cost). Codec/detection-dependent formats
> (ZIP/single-file/ISO/TAR/7z/RAR) still raise a clear "no backend"/`xfail` until
> their phases (3–7).

## 5. New declarative test framework

- [ ] 5.1 Port the corpus cleaned: `sample_archives.py`, `ArchiveContents`,
      `FileInfo`, `ArchiveCreationInfo` (declarative specs + expected data, which are
      API-agnostic).
- [ ] 5.2 `conftest.py` parametrization; **generate-on-demand + cache** to a
      project-local dir `.pytest_cache/archivey-archives/` (overridable via
      `ARCHIVEY_TEST_CACHE`; **not** `$XDG_CACHE_HOME`, unset on Windows), written
      atomically (temp + `os.replace`), keyed by `hash(spec + creation_params + lib
      versions + generator-code version)`; `--regen` flag.
- [ ] 5.3 `tests/fixtures/` for committed adversarial archives, each with a JSON
      sidecar (format per `ARCHITECTURE.md §2.8`); add generated archives to
      `.gitignore`; **commit no generated binaries**.
- [ ] 5.4 Flat `tests/` layout (no `tests/archivey/` nesting).
- [ ] 5.5 Clone DEV's suite into `tests/_dev_oracle/` as a **frozen, read-only
      regression gate** — collected and run, never refactored, allowed to skip/xfail
      as the new API diverges. (Deleted in Phase 10.)

## 6. Verify — acceptance criteria

**Spec scenarios covered**
- [ ] 6.1 `packaging-and-extras`: *core install pulls no third-party packages*,
      *install rejected on unsupported Python*, *supported on all three operating
      systems*, *`__version__` reflects the installed distribution*.
- [ ] 6.2 `backend-registry`: *core backend available without extras*, *optional
      backend absent at import* (registry exists; no format backends yet).
- [ ] 6.3 `logging`: *library emits no output by default*.
- [ ] 6.4 `testing-contract`: framework stands up (matrix harness importable; oracle
      hooks wired but skipped when libs absent).
- [ ] 6.4b `format-directory`: opening a directory yields members with correct
      filesystem metadata; `read`/`open` return file content; in-directory symlinks
      follow; `cost` reports `INDEXED`/`DIRECT`/`SEEKABLE` — the spine validated
      end-to-end against a real backend.

**Gates**
- [ ] 6.5 `uv run mypy src/` clean under `--strict`.
- [ ] 6.6 `uv run ruff check` clean.
- [ ] 6.7 `uv run pytest tests/` green (mostly skips at this stage).
- [ ] 6.8 `git status` clean after a test run (no new binary files).
- [ ] 6.9 `archivey.__version__` resolves via `importlib.metadata`.

## 7. Deferred (not in this phase)

- Stream layer (`internal/streams/`, codec layer) — Phase 2.
- Codec/detection-dependent leaf backends (ZIP/single-file/ISO) — Phase 3 (the
  directory backend lands here in Phase 1).
- Native 7z/RAR readers — Phase 7.
- CI matrix + coverage gating — Phase 10.
- Deleting `tests/_dev_oracle/` — Phase 10.
