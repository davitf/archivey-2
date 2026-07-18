## Why

The native 7z parser walks an **already-in-memory** header (`header_data: bytes`)
through `io.BytesIO` stream primitives — every field is a `BytesIO.read()` call,
each property payload is re-wrapped in its own `BytesIO`, and truncation checks go
through a `tell`/`seek` dance. After #146's L1 name fix, this per-field stream
overhead is the dominant part of the "non-name parse" residual that keeps 7z
`open_list` ~2× above its native peer band (`listing-attribution.md`). The RAR
native parser already parses block headers from a `bytes` buffer with an integer
cursor (`_load_vint`/`_load_byte`/`_load_le32`/…); 7z is the last native parser
still on the stream idiom, over data that never touches the file.

## What Changes

- Add an internal byte-cursor reader (`memoryview` + integer position) for the
  in-memory 7z header parse, modeled on RAR's `_load_*(buf, pos)` primitives.
- Convert the in-memory header parsers — `parse_header_block` and everything it
  calls (`_read_streams_info`, `_read_unpack_info`, `_read_folder`,
  `_read_substreams_info`, `_read_files_info`, the `_handle_*` property handlers,
  `_read_boolean`, `_read_digests`, `_read_comment`, `_read_property`,
  `_read_uint64`/`_read_uint32`/`_read_real_uint64`/`_read_byte`, `_read_exact`) —
  from `BinaryIO` to the cursor.
- Keep `read_signature_and_next_header(fp)` on the real file stream (it seeks in
  the file); the cursor begins at the materialized `header_data`, including the
  re-parsed **decoded encoded-header** blob.
- Delete the now-dead stream scaffolding: the `_buffer_len`/`_buffer_remaining`
  seek dance and the per-property `io.BytesIO(...)` wraps.
- Preserve every hostile-input bound: out-of-range / truncated reads still raise
  `CorruptionError` at parse, and count fields are still bounded against the
  header size before allocation.
- No public API, format support, extra/dependency, or observable behavior change.

## Capabilities

### New Capabilities
<!-- none -->

### Modified Capabilities
- `format-7z` — no behavior change; the "Bound 7z header count fields before
  allocation" requirement is **strengthened to be representation-independent**
  (the hostile-input bound must hold whether the header is walked by cursor or
  stream, and per-field truncation raises `CorruptionError`). This makes the
  invariant the refactor must preserve an explicit, testable contract. All other
  `format-7z` requirements (including "Parse 7-Zip headers natively") are
  unchanged.

## Impact

- **Modules:** `src/archivey/internal/backends/sevenzip_parser.py` only (parser
  internals). `sevenzip_reader.py` is unaffected — it consumes the parsed
  `SevenZipArchive` / `SevenZipFileRecord` objects, whose shapes do not change.
- **Public API / behavior:** none. Same members, metadata, errors, and error
  types for every fixture.
- **Extras / deps:** none.
- **Tests:** the existing 7z reader suite and the Atheris fuzz targets must stay
  green unchanged; `review/performance/listing_probe.py sevenzip` (the
  `read_exact` / parse census + ratio) is the acceptance metric; add focused unit
  coverage that truncated / out-of-range cursor reads raise `CorruptionError`.
- **Out of scope (investigated):** ZIP and TAR parse headers via the C stdlib
  (`zipfile` / `tarfile`) with no archivey-side per-field byte loop; RAR already
  parses from a `bytes` buffer with a cursor. 7z is the only backend with this
  lever.
