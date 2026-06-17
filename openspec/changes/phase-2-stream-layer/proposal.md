# Phase 2: Stream layer (compressed + seekable)

## Why

In the new design, format parsers never call codec libraries directly — they
compose a shared, pull-based decompressor layer (`compressed-streams`) and a
random-access layer on top of it (`seekable-decompressor-streams`). Phase 2 builds
that layer **fresh**: the `internal/streams/` package (with the good DEV primitives
ported in as units) plus the uniform codec/crypto stream stack that every later
format backend depends on.

Because this is clean-slate, there is **no `io_helpers.py` to reorganize** and no
re-export shim — the package is laid out correctly from the start, and the
simplified `BinaryIOWrapper` is written with straightforward delegation (no
`self.read = self._raw.read` method-replacement trick) rather than retrofitted.

## What Changes

- **`src/archivey/internal/streams/`**, with focused modules:
  - `slice.py` — `SlicingStream` (ported).
  - `compat.py` — `is_seekable`, `is_stream`, `is_filename`, `ensure_binaryio`,
    `ensure_bufferedio`, `fix_stream_start_position`, `read_exact` (ported), plus a
    **freshly written simplified `BinaryIOWrapper`** (plain delegation; `readinto`
    fallback).
  - `decompress.py` / `xz.py` / `lzip.py` — the ported `DecompressorStream`,
    `XzStream`, `LzipStream`. `archive_stream.py` stays as-is (clean and focused).

  > **Not built here:** the detection peek/rewind primitive (DEV's
  > `RecordableStream` + `RewindableStreamWrapper`) is **subsumed by `PeekableStream`**,
  > which the opener constructs for non-seekable sources and which lands with
  > `format-detection` in **Phase 3** — not in this stream layer. (DEV used those two
  > only inside the opener; in v2 the seekable path simply `seek(0)`s and the
  > non-seekable path is wrapped in `PeekableStream`.)
- **`compressed-streams` codec layer** — one default backend per codec; a single
  wrapped AES crypto stage reachable **only** through the wrapper; a missing
  optional backend raises `PackageNotInstalledError`; decompression errors are
  translated; optional digest verification runs on full reads (partial reads
  unverified, unverifiable algorithms skipped with a warning); backend dispatch is
  separable from opening.
- **`seekable-decompressor-streams`** — XZ block-index and lzip trailer-scan random
  access; `rapidgzip` / `indexed_bzip2` accelerators behind `[seekable]` with clean
  absence behavior.

## Specs

This change **implements** already-written specs; it does not modify them, so it
carries no spec deltas. Capabilities realized:

- **`compressed-streams`** — fully realized (default backends, crypto wrapper,
  missing-backend errors, error translation, digest verification, separable
  dispatch).
- **`seekable-decompressor-streams`** — fully realized.

The internal module layout is documented in `ARCHITECTURE.md`, not in
`openspec/specs/`. The 7z/ZIP container codecs needing `pyppmd`/`inflate64` and the
AES stage are *exercised end-to-end* once 7z lands in Phase 7; the layer's contracts
and default codecs are complete here.

## Impact

- **Depends on:** Phase 1 (spine + test harness green).
- **Affected code:** new `internal/streams/` package; the `compressed-streams`
  codec layer; the `seekable-decompressor-streams` layer.
- **Tests:** new `compressed-streams` + `seekable` scenarios; the frozen
  `tests/_dev_oracle/` gate must be no worse.
- **Risk:** some hot read paths may have relied on DEV's `BinaryIOWrapper`
  method-replacement; benchmark plain delegation on a large-member read before
  settling (PLAN risk area).
