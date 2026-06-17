# Phase 1: Project scaffold, spine, and test harness

## Why

The v2 rewrite is **clean-slate** (see `PLAN.md`): new code is written fresh
against the `openspec/specs/` capability specs, with `archivey-dev` as
reference-only. Phase 1 stands up the target shape *before any format is wired* —
the package layout, the **spine contracts written fresh** (public API skeleton,
the `BaseArchiveReader` ABC, the backend registry, the error hierarchy, and the
data-model / cost types), the `archivey` logger hierarchy, and the **new
declarative test framework**.

Authoring the spine correctly the first time — to `ARCHITECTURE.md`, not evolved
from DEV — is what lets every later phase attach a backend to a stable ABC. There
is **no later "interface cleanup" pass**: the renames, the removal of the
`for_iteration` flag and the `_prepare_member_for_open` hook, and the
flag-to-class-attribute conversion are simply how the ABC is written here. DEV is
cloned as a **frozen test oracle**, not copied as a baseline.

## What Changes

- **`pyproject.toml`** — `hatchling`; PEP 621 metadata (`archivey`, `0.2.0.dev0`,
  `requires-python >=3.11`); runtime extras exactly per `packaging-and-extras`
  (`[7z]`, `[rar]`, `[crypto]`, `[7z-write]`, `[iso]`, `[zstd]`, `[lz4]`, `[cli]`,
  `[seekable]`, `[recommended-lite]`, `[recommended]`, `[all]`); a `dev`
  `[dependency-groups]` entry for tooling **and the `py7zr`/`rarfile` oracles**.
  uv workflow; package stays pip-installable.
- **Package layout** — `src/archivey/{internal,formats}/` + public `__init__.py` +
  `py.typed`; establish the `archivey` **logger hierarchy** (no handlers).
- **Spine, written fresh** (types/ABCs in place, no backends yet):
  - `BaseArchiveReader` ABC in `ARCHITECTURE.md` vocabulary — `_iter_members`,
    `_iter_with_data`, `_open_member` with **no** `for_iteration` and **no**
    `_prepare_member_for_open`; `_SUPPORTS_RANDOM_ACCESS` / `_MEMBER_LIST_UPFRONT`
    as **class attributes**; `member_id` assignment at registration; the public
    surface (`stream_members(members)` selector-only, `read`/`open`, `extract_all`
    signature) and **link resolution by cycle-detection** (visited-set, no depth
    limit; resolves `link_target_member`; missing target → `LinkTargetNotFoundError`).
  - `ReadBackend` / `WriteBackend` ABCs (split — not one `Backend`) + the registry
    with separate `register_reader`/`reader_for_format` and
    `register_writer`/`writer_for_format`; backends declare `MAGIC`/`EXTENSIONS` as
    **data** and `REQUIRES_SEEK`; selection is **by format** (the central detector
    that consumes the magic data is wired in Phase 3). Import-time self-registration.
  - Public-API skeleton (`open_archive` with `encoding: str | None = None`, the
    `ArchiveReader` surface, context-manager lifecycle).
  - Data model — `ArchiveMember` (**mutable**, caller-read-only with `.replace()`
    copy-on-edit, **unhashable**; `raw_name: bytes`; `link_target_member`; `hashes`
    digests under algorithm keys; `extra`), `ArchiveInfo` (incl. `is_solid`),
    `ArchiveFormat` as a `(container, stream)` frozen pair (`ContainerFormat` ×
    `StreamFormat`, named class-vars), `MemberType`, the `CompressionAlgo`
    (extensible, incl. `BROTLI`) / `CompressionMethod` model, and member-name
    normalization (`name` decoded+normalized; `raw_name` verbatim bytes).
  - `ArchiveyError` hierarchy with required attributes (`message`, `source_format`,
    `archive_name`, `member_name`, `__cause__`), the per-library-translator +
    central-context-stamping pattern, and the new members `StreamNotSeekableError`,
    `LinkTargetNotFoundError`, `UnsupportedFeatureError`, `PackageNotInstalledError`,
    `UnsupportedOperationError`.
  - `Intent` enum + `CostReceipt` types — `ListingCost`
    (`INDEXED`/`REQUIRES_SCANNING`/`REQUIRES_DECOMPRESSION`), `AccessCost`
    (`DIRECT`/`SOLID`), `StreamCapability` (`SEEKABLE`/`FORWARD_ONLY`),
    `solid_block_count` (`is_solid` lives on `ArchiveInfo`, not the receipt).
- **New declarative test framework** — cleaned corpus port (`sample_archives`,
  `ArchiveContents`, `FileInfo`, `ArchiveCreationInfo`); `conftest.py`
  parametrization; **generate-on-demand + cache**; `tests/fixtures/` with a JSON
  sidecar per committed archive; **no committed generated binaries**; flat
  `tests/`. DEV's suite is cloned into `tests/_dev_oracle/` as the **frozen,
  read-only regression gate**.

## Specs

This change **implements** already-written specs; it does not modify them, so it
carries no spec deltas. Capabilities realized or seeded:

- **`packaging-and-extras`** — realized directly (pyproject, extras→capability
  mapping, env matrix, `__version__`).
- **`backend-registry`, `archive-data-model`, `error-handling`,
  `access-intent-and-cost`** — the **types and contracts** land here (written
  fresh); per-format *behavior* arrives as backends are added (Phases 3–7).
- **`logging`** — the named-logger hierarchy is established.
- **`testing-contract`** — framework foundations (declarative corpus, on-demand
  generation + cache, no committed binaries). Finalized in Phase 10.

## Impact

- **Affected code:** full project scaffold; spine modules; the new test framework;
  the frozen `tests/_dev_oracle/` tree.
- **Folds in the former "base reader interface cleanup" phase:** the ABC is
  authored correctly the first time, so there is no later rename/hook-removal pass.
- **7z/RAR (resolved):** DEV's `py7zr`/`rarfile` *read* backends are **not**
  ported; 7z/RAR reads are `xfail`/`skip` until the native readers land in
  Phase 7. `py7zr`/`rarfile` enter now only as `dev`-group oracles.
- **Risk:** low–medium. Later phases build on this ABC, so it must match
  `ARCHITECTURE.md`; mitigate by keeping the surface minimal and letting the
  Phase-3 vertical slices surface gaps early. Watch-item: the implementing agent
  must be able to `git clone davitf/archivey-dev` from its environment (a plain
  HTTPS clone works; the GitHub API is rate-limited — do not infer the repo is
  private from a `403`).
