# Phase 2: Stream layer reorganization

## Why

`io_helpers.py` in the DEV codebase is an overloaded grab-bag — detection
helpers, slicing, type/seek compatibility shims, and the `BinaryIOWrapper` all
live in one module. Later phases (reader-interface cleanup, extraction rewrite)
build directly on this stream layer, so splitting it into cohesive modules and
removing the fragile `BinaryIOWrapper` method-replacement trick first keeps those
phases clean. This is an **internal refactor with no behavior change**.

## What Changes

- **New `src/archivey/internal/streams/` package** with focused modules:
  - `detect.py` — `RecordableStream`, `RewindableStreamWrapper`
    (format-detection only).
  - `slice.py` — `SlicingStream`.
  - `compat.py` — `is_seekable`, `is_stream`, `is_filename`, `ensure_binaryio`,
    `ensure_bufferedio`, `fix_stream_start_position`, `read_exact`, and the
    simplified `BinaryIOWrapper`.
- **Relocate the decompressor streams** into the package:
  `decompressor_stream.py` → `streams/decompress.py`, `xz_stream.py` →
  `streams/xz.py`, `lzip_stream.py` → `streams/lzip.py`. `archive_stream.py`
  stays where it is (already clean and focused).
- **Simplify `BinaryIOWrapper`** — drop the `self.read = self._raw.read`
  hot-path method-replacement and use straightforward delegation.
- **Keep `io_helpers.py` as a thin re-export shim** (≤ 50 lines) so format
  backends don't need touching in this phase.

## Specs

**No spec deltas.** This phase only moves internal modules and simplifies an
internal wrapper; the behavioral capabilities (`archive-reading`,
`seekable-decompressor-streams`) are unchanged. Internal module layout is
documented in `ARCHITECTURE.md`, not in `openspec/specs/`.

## Impact

- **Depends on:** Phase 1 baseline (ported DEV source compiling and passing).
- **Affected code:** new `internal/streams/` package; relocated decompressor
  modules; updated internal imports; `io_helpers.py` reduced to a shim.
- **No public API or behavior change** — the existing test suite is the
  regression guard.
- **Risk:** some format backends may rely on `BinaryIOWrapper`'s hot-path method
  replacement for performance; benchmark before/after if a regression is
  suspected (noted in `PLAN.md` risk areas).
