## Context

Unix-compress (`.Z`, LZW) is wired today as `UnixCompressCodec` → `uncompresspy.LZWFile` (`src/archivey/internal/streams/codecs.py`). That library is a ~600-line pure-Python `RawIOBase` with its own open/read/seek/checkpoint plumbing. Archivey already owns the equivalent via `DecompressorStream` + `SeekPoint` (`decompressor_stream.py`); xz/lzip already register format-native seek points the same way.

`uncompresspy` requires a seekable source because CLEAR-code bit realignment does `file.seek(...)` during forward decode, not only for random access. Specs currently encode that as “`.Z` always needs seek.” `IDEAS.md` already flags non-seekable `.Z` as a follow-up. `seekable-decompressor-streams` already anticipates “unix-compress indexed seeks” by excluding them from `STREAM_REWIND_REDECOMPRESSES`.

License: archivey is MIT; uncompresspy 0.4.1 is BSD-3-Clause (Copyright 2025 Tiago Gomes). Compatible with attribution.

## Goals / Non-Goals

**Goals:**

- Vendor/adapt the LZW decode kernel as a push state machine behind `SegmentedDecompressorStream` (same split as xz/lzip).
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

### How existing stream bases split responsibility

| Pattern | Used by | Shape | Mid-decode events |
| --- | --- | --- | --- |
| Simple `DecompressorStream` | zlib, brotli, ppmd | `_decompress_chunk(chunk) → bytes` | None; rewind from origin |
| `SegmentedDecompressorStream` | xz, lzip | `state.feed(chunk) → (bytes, [(decomp, comp), …])` | Stream turns units into `SeekPoint`s via cursors |

CLEAR is a mid-decode event with relative segment sizes — same shape as xz/lzip member/stream completions, not zlib.

### SeekPoint fit

At CLEAR the dictionary resets to the 256 literals (+ placeholder in block mode). That is a resume state with no dictionary payload to serialize. Absolute offsets live on the stream’s `_comp_cursor` / `_decomp_cursor`; the state machine only reports relative `(decomp_size, comp_size)` for each CLEAR-ended segment.

After the 3-byte header, resume origin is `SeekPoint(0, 3)`. `_make_decompressor(point)` rebuilds a fresh `LzwState` with header params (`max_width`, `block_mode`) already known on the stream.

`DecompressorStream.seekable()` already delegates to `_inner.seekable()`, and `add_seek_points` no-ops when `seekable=False` on construction — the dual contract falls out of the existing base.

### Truncation / finished semantics

`DecompressorStream._read_decompressed_chunk` raises `TruncatedError` unless `_is_decompressor_finished()` is true at source EOF. `.Z` has no end marker; today’s codec intentionally never raises truncation. The unix-compress stream MUST treat input EOF as finished. A partial trailing code is accepted silently (not `TruncatedError`, and not a soft `warnings.warn` — see decision 5).

### Packaging / zero-dep

`.Z` becomes another pure-Python core codec alongside native xz/lzip. Core-only CI (`check_zero_dep_core.py`) must still pass: no `uncompresspy` import. `ncompress` remains dev-only for fixture generation.

## Decisions

### 1. Adapt uncompresspy’s LZW kernel; do not keep the package

Port header + decode loop + KwKwK special case into an internal module (e.g. `streams/unix_compress.py` or `streams/lzw.py`) under a BSD-3-Clause file header / `NOTICE` entry naming Tiago Gomes / uncompresspy. Project license stays MIT.

**Rejected:** keep `uncompresspy` and only upstream a streaming patch (does not fix ownership/stability). **Rejected:** rewrite from `ncompress` (Unlicense) — more behavioral risk vs the decoder we already test against.

### 2. Split `LzwState` + `UnixCompressDecompressorStream(SegmentedDecompressorStream)`

Do **not** dump the algorithm into the stream subclass, and do **not** use the zlib-only `_decompress_chunk → bytes` shape (CLEAR has nowhere to surface). Follow xz/lzip:

```
compressed chunk
       │
       ▼
┌──────────────────┐
│ LzwState         │  pure state machine (testable without I/O)
│ feed / flush     │  header, dict, bitbuf, KwKwK, CLEAR unread
│ is_finished      │
└────────┬─────────┘
         │ (out_bytes, [(decomp_n, comp_n), …])  one unit per CLEAR
         ▼
┌──────────────────────────────┐
│ UnixCompressDecompressorStream│  SegmentedDecompressorStream
│ _comp/_decomp_cursor         │
│ _on_completed_segments       │──▶ add_seek_points after advancing
│ header params for resume     │
└──────────────────────────────┘
```

**`LzwState`** (lives in e.g. `streams/unix_compress.py`):

- Implements the `_SegmentDecompressor` protocol: `feed` / `flush` → `(bytes, list[tuple[int, int]])`, `is_finished`.
- Owns dictionary, bit buffer, code-width growth, KwKwK, and CLEAR realignment via a bounded in-memory buffer (never `seek`s a file).
- Counts *logically consumed* compressed bytes (including CLEAR discard) so units’ `comp_size` are accurate even when the outer read chunk overshot.
- Knows nothing about `SeekPoint`, absolute offsets, or files.
- On first feed, parse the 3-byte header (or accept pre-parsed params); format errors raise `CorruptionError` directly (same pattern as native xz/lzip — no stdlib `ValueError` for the codec translator).

**`UnixCompressDecompressorStream`:**

- `codec.open()` constructs it with `seekable=config.seekable`.
- Stores `max_width` / `block_mode` after header parse so `_make_decompressor(point)` can rebuild an empty-dict `LzwState` at any CLEAR/`SeekPoint(0, 3)`.
- `_on_completed_segments`: for each unit, **advance cursors first**, then register the resume point (empty dict *after* CLEAR) — inverse of lzip’s “point then advance,” which marks segment *start* of the member that just finished:

  ```python
  for decomp_size, comp_size in units:
      self._comp_cursor += comp_size
      self._decomp_cursor += decomp_size
      self.add_seek_points(
          [SeekPoint(self._decomp_cursor, self._comp_cursor)]
      )
  ```

- No `_build_index` override: `.Z` has no trailer; progressive CLEAR points only; `SEEK_END` uses the base class scan-to-EOF when size is unknown.
- `_is_decompressor_finished()` is true at source EOF (see truncation decision).

**Rejected:** zlib-style `_decompress_chunk → bytes` plus ad-hoc `state.take_clears()` (reinvents segmented poorly). **Rejected:** monolithic stream subclass with inline LZW (harder to unit-test; blurs state vs I/O). **Rejected:** wrap `LZWFile` with a buffer that fakes seek (violates ADR 0010).

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

### 5. Error taxonomy (no soft warnings)

Invalid magic / invalid codes / bad header → raise `CorruptionError` from the LZW kernel (not stdlib `ValueError` + codec `translate`). Constructor misuse (`max_width`/`block_mode` only one set) is an `assert` — unreachable from public paths. No `StreamNotSeekableError` from the codec for `.Z` pipes. No `PackageNotInstalledError` for unix-compress.

Partial final code at EOF and reserved header bits (0x60) are **silent**: `.Z` has no trailer to confirm truncation, and Archivey’s diagnostic taxonomy has no fitting code for “unknown compress flags” / “partial trailing LZW code.” Emitting `warnings.warn` would bypass the collector; inventing a new `DiagnosticCode` is a cross-cutting diagnostics change deferred until a caller needs those advisories. Not `TruncatedError`.

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

None blocking — seek dual-contract, CLEAR→SeekPoint, and `LzwState` / `SegmentedDecompressorStream` module split confirmed.
