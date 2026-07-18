## 1. Baseline the accept metric

- [x] 1.1 Run `review/performance/listing_probe.py sevenzip` on `main` and record the `read_exact` / parse census and the archivey-vs-py7zr ratio as the before-numbers.
- [x] 1.2 Confirm the current 7z reader suite + Atheris 7z fuzz targets are green as the invariance baseline.

## 2. Introduce the cursor

- [x] 2.1 Add an internal `_Cursor` in `sevenzip_parser.py` over `memoryview(header_data)` with mutable `pos` and methods `read(n)`, `byte()`, `uint32()`, `real_uint64()`, `uint64()` (7z varint), `remaining()`, and a bounded `slice(n)` sub-view (design Decision 1/2).
- [x] 2.2 Route all bounds enforcement through `read`/`slice`: raise `CorruptionError` with the existing `context` messages on `pos + n > len(buf)`, and keep the `_MAX_NEXT_HEADER_SIZE` / negative-length / `_MAX_SEEK_OFFSET` / `_MAX_UTF16_CHARS` guards on the cursor (design Decision 3).

## 3. Port the in-memory parsers

- [x] 3.1 Convert `parse_header_block` to build a `_Cursor` from `header_data` (plain and decoded encoded-header blobs use the same path — design Decision 4).
- [x] 3.2 Convert the streams/folders parsers: `_read_streams_info`, `_read_unpack_info`, `_read_folder`, `_read_substreams_info`, `_read_digests`, `_read_boolean`, `_skip_archive_properties`, `_read_comment`.
- [x] 3.3 Convert `_read_files_info` to slice each property payload with `_Cursor.slice(size)` instead of `io.BytesIO(_read_exact(...))`, and keep the `num_files`/table-count bound against `len(buf)` (was `_buffer_len`).
- [x] 3.4 Convert the property handlers `_handle_empty_file` / `_handle_anti` / `_handle_name` / `_handle_time` / `_handle_attributes` / `_handle_start_pos` to take a `_Cursor`; `_handle_name` passes its payload view to `_decode_utf16_names` via `bytes(view)` at the single decode call.
- [x] 3.5 Convert the field primitives `_read_property` / `_read_byte` / `_read_uint32` / `_read_real_uint64` / `_read_uint64` / `_read_utf16` to the cursor.
- [x] 3.6a Hot per-member field reads: prototype the loop with the `_Cursor` methods vs. free functions holding `pos` as a local (RAR's `_load_*` shape) and pick the faster on the probe (design Decision 4); keep only format-agnostic primitives shareable, 7z/RAR5 varints stay per-format.
- [x] 3.6 Delete the now-dead stream scaffolding: `_buffer_len`, `_buffer_remaining`, `_read_exact`'s stream branch, and the per-property `io.BytesIO` wraps. Leave `read_signature_and_next_header` on `BinaryIO` (real-file seeks).

## 4. Verify

- [x] 4.1 Add focused unit tests: a truncated property payload and an out-of-range count each raise `CorruptionError`; a byte-for-byte-identical parse of a representative fixture before/after (member names, sizes, times, attrs, CRCs, comment).
- [x] 4.2 Run the full 7z reader suite + `py7zr` oracle + Atheris 7z targets; require zero fixture/oracle output diffs (any diff is a port bug, not a spec change — design Risks).
- [x] 4.3 Re-run `listing_probe.py sevenzip`; record the census drop and new ratio against the 1.1 baseline (accept gate is the census drop + green suite, not a specific band — design Non-Goals).
- [x] 4.4 Run the suite in `[all]`, `[all-lowest]`, and `[core-only]` per `CONTRIBUTING.md`; `ruff format` + `pyrefly` + `ty` clean on `sevenzip_parser.py`.
- [x] 4.5 `openspec validate --strict sevenzip-header-cursor-parse`.
