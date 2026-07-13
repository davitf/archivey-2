# Tasks — Refactor native 7z reader/parser

> Run tools through uv: `uv run --no-sync pytest`, `uv run --no-sync pyrefly check`,
> `uv run --no-sync ty check`, `uv run --no-sync ruff`. Read `design.md` first —
> decisions 1–7 lock module layout, two-phase parse, registry, and pipeline.
> Preservation gate: `specs/testing-contract/spec.md`.

## 1. Method registry

- [x] 1.1 Add `sevenzip_methods.py` with `MethodKind`, `SevenZipMethod`, and a single
      registry covering COPY / LZMA1 / LZMA2 / Delta / BCJ (short+long aliases) / BCJ2 /
      Deflate / Deflate64 / BZip2 / Zstd / Brotli / LZ4 / PPMd / AES.
- [x] 1.2 Implement `lookup(method_id)`, `folder_is_encrypted`, and
      `compression_method_for_coder` on the registry; delete parser `_METHOD_*` +
      `_METHOD_ALGORITHMS` and reader `_BCJ_*` / `_SINGLE_STAGE_CODECS` duplicates.
- [x] 1.3 Update imports in parser/reader/tests that referenced private `_METHOD_*`.

## 2. Two-phase header parse

- [x] 2.1 Split signature/next-header read (bounds + CRC) from `parse_header_block` that
      returns `PlainHeader | EncodedHeader` (no `decode_folder` parameter).
- [x] 2.2 Move encoded-header pack-stream location helpers onto the encoded descriptor;
      keep external/alternative-coder rejects and all allocation/read caps.
- [x] 2.3 Wire `SevenZipReader` to loop: decode encoded folders via the pipeline +
      password helper, re-parse until plain; remove `DecodeFolder` / recursive callback.
- [x] 2.4 Retarget hostile-header unit tests to call the parse surface directly (no boom
      `decode_folder` stubs).

## 3. Pipeline regroup

- [x] 3.1 Move `open_folder_pipeline` / `decode_folder_to_bytes` (and LZMA/AES stage
      helpers) into `sevenzip_pipeline.py` (or equivalent) consuming the method registry.
- [x] 3.2 Implement `group_coders` + kind handlers; preserve LZMA2±BCJ combined,
      LZMA1+BCJ pybcj staging, BCJ-only pybcj, linear-chain check, BCJ2 reject.
- [x] 3.3 Share password-attempt + folder/member CRC confirm between encrypted header and
      encrypted members; drop pure-forwarding wrappers.

## 4. Parser / reader tidy

- [x] 4.1 Table-drive `FILES_INFO` property handlers; materialize complete
      `SevenZipFileRecord`s in one step after folder/substream mapping.
- [x] 4.2 Keep sequential streams-info walk; remove dead intermediate mutation patterns
      called out in design §5–6.
- [x] 4.3 Confirm public `SevenZipReadBackend` registration and seekable-source reject are
      unchanged.

## 5. Verify

- [x] 5.1 `uv run --no-sync pytest tests/test_sevenzip_reader.py tests/test_sevenzip_oracle.py tests/test_py7zr_corpus.py tests/test_codecs.py tests/test_password.py -q`
- [x] 5.2 `uv run --no-sync pyrefly check` and `uv run --no-sync ty check` clean on touched modules
- [x] 5.3 `uv run --no-sync ruff check` / `ruff format --check` on touched paths
- [x] 5.4 `openspec validate --strict refactor-sevenzip-reader`
- [x] 5.5 Spot-check line counts vs design target (~1.0–1.2k across 7z backend modules);
      do not delete safety comments to hit the number
