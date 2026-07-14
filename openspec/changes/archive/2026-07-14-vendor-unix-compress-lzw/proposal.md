## Why

Unix-compress (`.Z`) currently depends on `uncompresspy`, a small Beta pure-Python package that reinvents stream plumbing Archivey already owns, requires a seekable source (even for forward decode, because CLEAR realignment seeks), and offers weak long-term support guarantees. Vendoring the LZW kernel into Archivey's `DecompressorStream` layer removes the dependency, unlocks non-seekable sources, and gives seekable sources CLEAR-based seek points like other indexed codecs.

## What Changes

- Replace the `uncompresspy` backend with an internal LZW decoder adapted from it (BSD-3-Clause attribution retained).
- Wire unix-compress through `DecompressorStream`: forward decode never seeks the source; CLEAR overshoot is resolved in a bounded in-memory buffer.
- On a seekable source with seekability declared, register a `SeekPoint` at each CLEAR (and at stream start) so backward/random seeks resume from the nearest reset — no `STREAM_REWIND_REDECOMPRESSES`.
- Match other codecs' seek contract: non-seekable source → non-seekable decompression; seekable source → seekable decompression when seekability is requested.
- Move `.Z` / `.tar.Z` into the zero-dependency core.
- **BREAKING** (pre-1.0): remove the `[unix-compress]` extra and the `PackageNotInstalledError` path for `uncompresspy`. Callers who only installed that extra get the capability from bare `archivey`.
- Drop `uncompresspy` from runtime extras and the `dev` group; keep `ncompress` as the test-only fixture compressor.
- Retire the IDEAS.md "non-seekable unix-compress" item; update packaging / library-analysis docs.

## Capabilities

### New Capabilities

### Modified Capabilities

- `compressed-streams`: unix-compress default backend becomes the native LZW stream; availability moves to core.
- `packaging-and-extras`: drop `[unix-compress]`; core install lists `.Z` / `.tar.Z`.
- `format-single-file-compressors`: `.Z` no longer requires a seekable source; streaming/non-seekable behavior matches other single-file codecs.
- `seekable-decompressor-streams`: specify CLEAR→`SeekPoint` indexing for unix-compress (already excluded from rewind diagnostics).

## Impact

- Code: new LZW state + `UnixCompressDecompressorStream` under `src/archivey/internal/streams/`; `UnixCompressCodec` in `codecs.py` stops importing `uncompresspy`.
- Packaging: `pyproject.toml` extras / dependency groups; `tests/check_zero_dep_core.py` and extras-import guards.
- Specs/docs: capabilities above; `docs/internal/library-analysis.md`; `IDEAS.md`; purpose prose in format/packaging specs.
- Tests: remove `@requires("uncompresspy")` / missing-backend cases; add non-seekable forward decode, seekable CLEAR seek-point seeks, corruption translation; keep `ncompress` for fixtures.
- License: retain Tiago Gomes / uncompresspy BSD-3-Clause notice on the vendored kernel (project stays MIT).
