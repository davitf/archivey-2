# Migrate the zstd decode backend from `zstandard` to the stdlib `compression.zstd` line

## Why

The compression-library evaluation (`docs/library-analysis.md`) decided to move zstd decoding
off `zstandard` to the standard-library line — **stdlib `compression.zstd` on Python 3.14+,
`backports.zstd` on 3.11–3.13** (the same `compression.zstd` API; `pyzstd >= 0.19` pulls
`backports.zstd` transitively, so an env with `pyzstd` also satisfies it). That evaluation
recorded the decision and its rationale but **deferred the swap** to this change.

Measured on a 200 KB incompressible zstd frame (full table in `docs/library-analysis.md`),
`zstandard` has two warts the stdlib line fixes:

- **Truncation is silent** — `zstandard` returns a short read with no error; `compression.zstd`
  / `backports.zstd` raise `EOFError` (which maps cleanly to `TruncatedError`, like gzip/bz2/lzma).
- **No in-place backward seek** — `zstandard`'s reader raises on a backward seek, which is why
  the code wraps it in `_ZstdReopenStream` (close → rewind source → reopen → re-decode forward).
  The stdlib `ZstdFile` (built on `_compression.DecompressReader`) rewinds in place, so the
  special-case wrapper can be deleted.

## What Changes

- **Backend swap (`compressed-streams`)**: zstd decodes via `compression.zstd` when present
  (3.14+), else `backports.zstd`. Resolve the module once (like the other optional codecs) and
  raise `PackageNotInstalledError` naming the backend when neither is importable.
- **Delete `_ZstdReopenStream`** and the backward-seek reopen special-case: zstd now rewinds via
  the stdlib reader like brotli/lz4/zlib (still emits the rewind warning — it has no index).
- **Exception translation**: map `compression.zstd` `ZstdError` → `CorruptionError` and
  `EOFError` → `TruncatedError`, replacing the `zstandard.ZstdError` translation.
- **Packaging (`packaging-and-extras`)**: `[zstd]` pins `backports.zstd; python_version < "3.14"`
  (no runtime dep on 3.14+); the `[7z]` bundle's zstd dependency follows the same backend. Drop
  `zstandard` from the runtime extras (it may return to `[all]` later as an alternative backend
  behind its own extra if there is ever a reason).
- **Tests**: update the zstd fixtures/tests that import `zstandard` to the new backend; add a
  truncation test that now asserts `TruncatedError` (previously impossible with `zstandard`).
- **Seekable behaviour (`seekable-decompressor-streams`)**: zstd's rewinding seek is no longer a
  "reopen from start" special-case; it is the ordinary index-less rewind. (Efficient seekable
  zstd via `indexed_zstd` remains out of scope — tracked in `IDEAS.md`.)

## Impact

- **Files**: `src/archivey/internal/streams/codecs.py` (the `ZstdCodec` + `_ZstdReopenStream`
  removal), `pyproject.toml` (extras), zstd tests.
- **Behaviour**: truncated `.zst` now raises `TruncatedError` instead of a silent short read — a
  strictly-better, but observable, change.
- **Risk**: low–moderate. The backend is API-compatible with the stdlib codec family; the main
  care points are the marker-gated `[zstd]` pin and the exception-translation taxonomy.
- **Depends on**: `compression-library-evaluation` (records this decision). Coordinates with
  `codec-descriptor-refactor` (where a codec's `open`/`translate` are wired).
