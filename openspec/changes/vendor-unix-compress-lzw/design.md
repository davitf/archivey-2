## Context

Unix-compress (`.Z`, LZW) is wired today as `UnixCompressCodec` → `uncompresspy.LZWFile` (`src/archivey/internal/streams/codecs.py`). That library is a ~600-line pure-Python `RawIOBase` with its own open/read/seek/checkpoint plumbing. Archivey already owns the equivalent via `DecompressorStream` + `SeekPoint` (`decompressor_stream.py`); xz/lzip already register format-native seek points the same way.

`uncompresspy` requires a seekable source because CLEAR-code bit realignment does `file.seek(...)` during forward decode, not only for random access. Specs currently encode that as “`.Z` always needs seek.” `IDEAS.md` already flags non-seekable `.Z` as a follow-up. `seekable-decompressor-streams` already anticipates “unix-compress indexed seeks” by excluding them from `STREAM_REWIND_REDECOMPRESSES`.

License: archivey is MIT; uncompresspy 0.4.1 is BSD-3-Clause (Copyright 2025 Tiago Gomes). Compatible with attribution.

## Goals / Non-Goals

**Goals:**

- Vendor/adapt the LZW decode kernel into a `DecompressorStream` subclass.
- Forward decode works on non-seekable sources (CLEAR overshoot handled in a bounded buffer).
- Seekable source + declared seekability → CLEAR positions become `SeekPoint`s; random access resumes from the nearest CLEAR (or start) with an empty dictionary.
- Seekable source without declared seekability → forward-only (no seek-point table), matching other codecs.
- Non-seekable source → `seekable()` false; `seek` unsupported.
- Move `.Z` into zero-dep core; remove `[unix-compress]` / `uncompresspy`.
- Preserve behavioral parity with today’s decoder on the existing corpus / `ncompress` fixtures (corruption → `CorruptionError`; truncation still undetectable).

**Non-Goals:**

- Compression / writing `.Z` (still out of scope; fixtures stay on `ncompress`).
- Upstreaming the streaming fix to uncompresspy as the primary path.
- Rewriting the algorithm from `ncompress` or another reference (kept as a rejected alternative).
- Changing detection magic (`1F 9D`) or TAR+`.Z` composition rules.
- Emitting `TruncatedError` for cut `.Z` streams (format has no length/checksum).

## Investigations

### What uncompresspy actually does

| Piece | Lines (approx.) | Archivey equivalent |
| --- | --- | --- |
| Path/file open, `RawIOBase`, `open()`, `extract()` | ~250 | `DecompressorStream` / `ReadOnlyIOStream` / codec layer |
| Checkpoint list + `seek`/`tell` | ~80 | `SeekPoint` + `DecompressorStream.seek` |
| Header + LZW loop + CLEAR realignment | ~200–250 | **Port this** |

### Why CLEAR needs “seek” today

Unix-compress packs codes into blocks of `code_width` bytes. After CLEAR, the bitstream realigns to the next block boundary. uncompresspy reads a whole block-sized chunk, then on CLEAR repositions with a relative `seek`. Overshoot is at most one code-width block — bounded — so an in-memory unread/discard replaces the seek.

### SeekPoint fit

At CLEAR the dictionary resets to the 256 literals (+ placeholder in block mode). That is exactly a resume state with no dictionary payload to serialize:

```
SeekPoint(
  decompressed_offset=<bytes emitted so far>,
  compressed_offset=<absolute source pos after CLEAR realignment>,
  state=None,  # or header params only; dict always empty at CLEAR
)
```

Stream start is already `SeekPoint(0, 3)` after the 3-byte header (uncompresspy’s first checkpoint). `_create_decompressor(point)` rebuilds a fresh LZW state with the header’s `max_width` / `block_mode` already parsed.

`DecompressorStream.seekable()` already delegates to `_inner.seekable()`, and `add_seek_points` no-ops when `seekable=False` on construction — the dual contract falls out of the existing base.

### Truncation / finished semantics

`DecompressorStream._read_decompressed_chunk` raises `TruncatedError` unless `_is_decompressor_finished()` is true at source EOF. `.Z` has no end marker; today’s codec intentionally never raises truncation. The unix-compress stream MUST treat input EOF as finished (optional soft warning for a partial trailing code, matching uncompresspy’s `warn_truncation`, but not a typed truncation error).

### Packaging / zero-dep

`.Z` becomes another pure-Python core codec alongside native xz/lzip. Core-only CI (`check_zero_dep_core.py`) must still pass: no `uncompresspy` import. `ncompress` remains dev-only for fixture generation.

## Decisions

### 1. Adapt uncompresspy’s LZW kernel; do not keep the package

Port header + decode loop + KwKwK special case into an internal module (e.g. `streams/unix_compress.py` or `streams/lzw.py`) under a BSD-3-Clause file header / `NOTICE` entry naming Tiago Gomes / uncompresspy. Project license stays MIT.

**Rejected:** keep `uncompresspy` and only upstream a streaming patch (does not fix ownership/stability). **Rejected:** rewrite from `ncompress` (Unlicense) — more behavioral risk vs the decoder we already test against.

### 2. `UnixCompressDecompressorStream(DecompressorStream[LzwState])`

Same pattern as Brotli/PPMd wrappers: codec `open()` constructs the stream with `seekable=config.seekable`. LZW state is push-oriented (`feed` compressed bytes → decompressed bytes), owning a small input bit/byte buffer so CLEAR realignment never calls `seek` on the source.

Track absolute compressed cursor as bytes are consumed so CLEAR can `add_seek_points([SeekPoint(decomp_pos, comp_pos)])` when `_index_enabled`.

**Rejected:** wrap `LZWFile` with a `BufferedReader` that fakes seek via full buffering (violates “no silent unbounded buffer of non-seekable” / ADR 0010).

### 3. Seek contract mirrors xz/lzip, not stdlib gzip

| Source | Declared `seekable` | Behavior |
| --- | --- | --- |
| Non-seekable | any | Forward-only; `seekable()` false |
| Seekable | false | Forward-only; no CLEAR table retained |
| Seekable | true | CLEAR → `SeekPoint`; random access from nearest CLEAR/start |

`UnixCompressCodec.rewind_warning` stays `None` (indexed path; already excluded from rewind diagnostics). No accelerator extra.

**Rejected:** always O(n) rewind from offset 0 with a `RewindWarning` (throws away free CLEAR index).

### 4. Core packaging; remove `[unix-compress]`

Drop the extra from `pyproject.toml`, `[recommended-lite]` composition, and missing-backend tests. Pre-1.0 **BREAKING** for anyone who depended on the extra name alone; capability moves to bare install. Do not leave an empty deprecated extra unless a later release needs a migration shim.

Keep `ncompress` in the `dev` group only.

### 5. Error translation

Invalid magic / invalid codes / bad header → `CorruptionError`. No `StreamNotSeekableError` from the codec for `.Z` pipes. No `PackageNotInstalledError` for unix-compress. Partial final code at EOF → optional warning only; not `TruncatedError`.

### 6. Docs / IDEAS cleanup in the same change

Update `docs/internal/library-analysis.md`, remove the IDEAS “non-seekable unix-compress” bullet (done by this change), adjust purpose prose in affected specs.

## Risks / Trade-offs

| Risk | Mitigation |
| --- | --- |
| Behavioral drift vs uncompresspy on edge CLEAR/alignment cases | Keep existing corpus + `ncompress` roundtrips; add explicit CLEAR-heavy / multi-block seek tests |
| Incorrect compressed_offset on SeekPoints when feeding large chunks | Unit-test CLEAR seek resumes byte-identical to forward decode from that offset; compare against full re-decode |
| `DecompressorStream` EOF → `TruncatedError` | Override finished/flush semantics for `.Z` as above; regression test truncated fixture yields short data without error |
| License attribution missed | File-level BSD-3 header + third-party notice in package metadata/docs |
| Slightly larger core wheel | ~200–300 lines pure Python; acceptable vs an extra dependency |

## Open Questions

None blocking — seek dual-contract and CLEAR→SeekPoint approach confirmed for this proposal.
