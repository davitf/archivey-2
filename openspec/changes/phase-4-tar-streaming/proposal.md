# Phase 4a: TAR forward-only streaming + `strict_eof`

## Why

Phase 3 landed the TAR **random-access** reader: `tarfile` scans headers on a seekable
source, builds an index, and serves members on demand. Phase 4a's job is the other half of
the TAR read path — **forward-only, bounded-memory** access on a non-seekable source
(pipes, sockets, `NonSeekableBytesIO`).

That path is required by:

- `archive-reading` — `stream_members()` / `__iter__` on solid containers without
  buffering whole blocks.
- `access-mode-and-cost` — `streaming=True` on a pipe must work for TAR (plain and
  compressed); `CostReceipt.stream_capability` must reflect `FORWARD_ONLY` on non-seekable
  sources.
- `format-tar` — end-of-archive truncation detection (`strict_eof`).
- `testing-contract` — *non-seekable TAR.GZ source*.

Safe extraction (`ExtractionCoordinator`, `extract()`) is a **separate change**
(`phase-4-safe-extraction`). This change delivers the read/stream primitive that
extraction consumes via `_iter_with_data()`.

## What Changes

### `TarReader._iter_with_data()` override (the core deliverable)

The base `BaseArchiveReader._iter_with_data()` default eagerly calls
`_get_members_registered()` — correct for ZIP/directory, **incorrect** for TAR on a
forward-only pass. `TarReader` MUST override it to:

1. Walk the archive in **one forward pass** — `tarfile` incremental iteration
   (`TarFile` as an iterator / `next()`), **not** `getmembers()` (which scans the whole
   archive up front).
2. Yield `(member, stream)` pairs progressively; assign `member_id` as members are
   encountered (the base's link-resolution pass cannot run before the full list exists in
   streaming mode — link targets for `open()`/`read()` in streaming mode remain limited per
   `archive-reading`).
3. Verify end-of-archive markers at the end of the pass (`format-tar` truncation
   requirement).
4. Close each member stream when the iterator advances (per `stream_members` contract).

For **compressed tars** (`.tar.gz`, `.tar.bz2`, `.tar.xz`, …): open the codec layer with
`StreamConfig(streaming=True)` (no seekable accelerators) and feed the decompressor stream
to `tarfile.open(mode="r:", fileobj=...)`. `TarReader` **already** threads
`StreamConfig(streaming=streaming)` into the codec today (`tar_reader.py`); the remaining work
is removing the seekable-outer-source requirement (below), not the codec wiring.

For **plain `.tar`** on a non-seekable source: header scan is inherently forward-only;
`streaming=True` is the only mode that works (random-access open already fails fast).

### Access gating: relax `REQUIRES_SEEK` under `streaming=True` — **per backend, not in the opener**

Today the opener (`core.py`) enforces `StreamNotSeekableError` whenever
`backend_cls.REQUIRES_SEEK` is true and the source is non-seekable — **unconditionally on
`streaming`**, for every backend. The naive fix of adding `and not streaming` to that single
opener check is **wrong**: it would also relax ZIP and ISO, which set `REQUIRES_SEEK = True`
and genuinely cannot do a forward-only pass on a non-seekable source (ISO needs the path
table / directory records; the ZIP backend has no forward-only `_iter_with_data()` override).
ISO and ZIP MUST keep failing fast on a non-seekable source even under `streaming=True`.

The relaxation is therefore **per-backend opt-in**, not an opener-wide change. Concretely:
introduce a backend capability (e.g. a `SUPPORTS_STREAMING_NON_SEEKABLE` class flag, default
`False`) that **only `TarReadBackend` sets `True`**, and have the opener skip the seek
requirement only when `streaming=True` **and** the backend declares that capability. Net
behaviour:

- `streaming=False` (random access) + non-seekable source → **fail fast** for all backends
  (unchanged).
- `streaming=True` + non-seekable source → **allowed for TAR only**; ZIP/ISO still fail fast.

Wire `CostReceipt.stream_capability` to the actual source: `SEEKABLE` when
`is_seekable(source)`, `FORWARD_ONLY` otherwise (per `access-mode-and-cost`). Today
`TarReader` hardcodes `stream_capability=StreamCapability.SEEKABLE` in `_get_archive_info()`;
this change derives it from the source instead.

### Minimal `strict_eof` config (Phase 4, not Phase 5)

Add `strict_eof: bool = False` to `open_archive()` (and thread it into `TarReader`).

- After the last member in a forward pass (or after `getmembers()` scan in random-access
  mode), verify null-filled 512-byte end-of-archive block(s).
- `strict_eof=False` (default): emit `logging.WARNING` on `archivey.backends.*`.
- `strict_eof=True`: raise `TruncatedError`.

This is intentionally minimal — no full public `ReaderConfig` yet (Phase 5).

### `compressed_source_size` hook for safe extraction

Expose the outer compressed byte length on the reader when known (typically `Path.stat().st_size`
for a file-backed compressed tar; `None` for pipes and plain `.tar`). The
`phase-4-safe-extraction` change uses this for archive-wide bomb-ratio checks; this change
only captures and exposes the value.

## Decisions locked in this change

1. **`strict_eof` lands here** with a single keyword on `open_archive()`, not deferred to
   Phase 5.
2. **Streaming TAR does not use `getmembers()`** — progressive iteration only.
3. **No `ExtractionCoordinator` in this change** — read/stream path only.
4. **DEV reference** (pin `730275b…`): `archivey-dev` `formats/tar_reader.py` streaming /
   `_iter_with_data` path; adapt to the v2 ABC override, not copy the old helper class.

## Specs

- **`format-tar`** (ADDED) — forward-only streaming on non-seekable sources; truncation
  check runs at end of the streaming pass.
- **`access-mode-and-cost`** (ADDED) — non-seekable compressed tar opens under
  `streaming=True`; `stream_capability` reflects the source.

Implements (no delta) existing requirements in `archive-reading` (`stream_members`,
forward iteration, stream invalid after advance) and `testing-contract` (non-seekable
TAR.GZ).

## Impact

- **Depends on:** Phase 3 green (`TarReader` random-access path, detection, codec layer).
- **Blocks:** full non-seekable `tar.gz` *extraction* until `phase-4-safe-extraction` also
  lands; streaming *read* is testable after this change alone.
- **Affected code:** `src/archivey/internal/backends/tar_reader.py` (override + `strict_eof`
  + cost capability + `SUPPORTS_STREAMING_NON_SEEKABLE` flag),
  `src/archivey/core.py` (`strict_eof` param on `open_archive()`; per-backend seek gating),
  `src/archivey/internal/base_reader.py` (optional `compressed_source_size` property),
  tests (`test_tar.py`, `testing-contract` non-seekable scenario).
- **Coordinates with:** `phase-4-safe-extraction` (which lands **after** this change and
  consumes `_iter_with_data()` and `compressed_source_size`; pipe `tar.gz` extract needs both).

## Implementation stages

1. **Plain `.tar` streaming** — override `_iter_with_data`, non-seekable plain tar
   `stream_members()` + `__iter__`, `FORWARD_ONLY` cost.
2. **Compressed tar streaming** — non-seekable `.tar.gz` (and one other codec smoke test),
   relax `REQUIRES_SEEK` gating, `testing-contract` non-seekable scenario.
3. **`strict_eof`** — wire keyword, truncation scenarios from `format-tar`.

Each stage ends green (pyrefly + ty + ruff + its new tests).
