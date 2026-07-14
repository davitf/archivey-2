## 1. Coder registry (Decision 1)

- [ ] 1.1 Add leaf module `src/archivey/internal/backends/sevenzip_coders.py`:
      `MethodKind` enum (`COPY`, `AES`, `BCJ2`, `LZMA_FAMILY`, `SINGLE`), a frozen
      `SevenZipMethod` dataclass (`method_id`, `algorithm`, `kind`, `codec`,
      `lzma_filter_id`, `pybcj_attr`, `aliases`), `_bcj()`/`_single()` row helpers,
      the `_BY_ID` index (id + every alias), `lookup`/`require`, the
      `METHOD_COPY/LZMA/LZMA2/DELTA/AES` singletons, and `is_bcj`/`is_lzma_family`.
      Import only `Codec`/`CompressionAlgorithm`/`CompressionMethod`/`lzma` — no
      parser import (keeps it a leaf).
- [ ] 1.2 Populate the table from the current five maps, folding each BCJ method's
      long id as an `alias` of its short-id row.
- [ ] 1.3 Add a table test asserting each method id from the old five maps resolves
      with identical `algorithm`/`codec`/`pybcj_attr`, and that
      `compression_method_for_coder` output is unchanged for every id.
- [ ] 1.4 Delete the five maps; point parser and reader lookups at `lookup`/`require`;
      keep `folder_is_encrypted(folder: SevenZipFolder)` and
      `compression_method_for_coder(coder: SevenZipCoder)` **typed** in the parser
      (calling `coders.lookup`); stop the reader importing `_METHOD_*` from the parser.

## 2. Invert control: pure parser + reader loop (Decision 2)

- [ ] 2.1 Add pure parser entry points with a `PlainHeader | EncodedHeader` sum type:
      `read_signature_and_next_header(fp) -> Signature` (all CRC + bounds guards +
      comments), `parse_header_block(bytes) -> PlainHeader | EncodedHeader`,
      `materialize_archive(sig, plain, *, is_header_encrypted)`, `empty_archive(sig)`,
      and `encoded_folder_slices(encoded)`.
- [ ] 2.2 Remove `DecodeFolder` and the `decode_folder=` parameter from the parser.
- [ ] 2.3 Drive the "decode encoded header → re-parse" loop in `SevenZipReader`
      (bounded `while isinstance(block, EncodedHeader)`), with password prompting in
      the reader's decode step.
- [ ] 2.4 Keep a thin all-in-one `parse_sevenzip_archive(fp, *, password=None,
      key_cache=None, …)` running the loop with one static password, for harnesses.
- [ ] 2.5 Update the three harnesses (`tests/fuzz_sevenzip_parser.py`,
      `tests/atheris_fuzz/targets.py`, `tests/test_atheris_crc_fixup.py`) to the new
      entry point (one-line call-site change, no `decode_folder=`).

## 3. Split folder pipeline (Decision 3)

- [ ] 3.1 Add pure `plan_pipeline(folder) -> list[Stage]` with typed stages
      (`AesStage`, `LzmaChainStage`, `BcjStage(decoder_attr, cap_size)`, `CodecStage`;
      COPY emits nothing); move all validation into it (num in/out == 1,
      linear-chain check, BCJ2 rejection) preserving error types and messages.
- [ ] 3.2 Encode the LZMA1+BCJ workaround as the emitted stage sequence (stdlib
      LZMA1/Delta chain, then per-BCJ `pybcj` stages with `SlicingStream` output caps);
      LZMA2+BCJ stays one liblzma filter chain.
- [ ] 3.3 Reduce `open_folder_pipeline` to `execute(source, stages, ...)` — a left
      fold that opens each stage (the only I/O-touching part); rewire
      `decode_folder_to_bytes` and the reader through it. If flattening the LZMA1+BCJ
      sub-staging proves fragile against the oracle fixtures, fall back to a single
      `LzmaChainStage` kind whose handler holds the nested staging (Decision 3).
- [ ] 3.4 Add a `plan_pipeline` unit test asserting the stage sequence for BCJ+LZMA1,
      BCJ+LZMA2, AES+LZMA2, Delta+LZMA2, plain LZMA2, and COPY.

## 4. Cleanup (Decisions 4, 5)

- [ ] 4.1 Rename the reader's `_folder_unpack_size` (member-size sum) to a distinct
      name; leave the parser's bind-pair `_folder_unpack_size` as is.
- [ ] 4.2 Table-drive the `FILES_INFO` property handlers (`dict[_Property, handler]`),
      preserving every allocation/read bound and threat-model comment.
- [ ] 4.3 Fold the two password+CRC confirm paths (encoded header vs. member folder)
      into one helper; drop `_open_folder_pipeline` if it only forwards kwargs.

## 5. Verify

- [ ] 5.1 `uv run pytest tests/test_sevenzip_reader.py tests/test_sevenzip_oracle.py
      tests/test_py7zr_corpus.py tests/fuzz_sevenzip_parser.py
      tests/test_atheris_crc_fixup.py` — all green, unchanged assertions.
- [ ] 5.2 `uv run pyrefly check` and `uv run ty check` clean on both modules and the
      new one.
- [ ] 5.3 Run the suite in `[all]`, `[all-lowest]`, and `[core-only]` configs
      (per CONTRIBUTING "Before pushing") — confirm `[7z]`-gated (pybcj/PPMd/…) and
      core paths behave identically to before. This is the `testing-contract`
      preservation gate: no weakened/removed 7z assertions.
- [ ] 5.4 `openspec validate --strict sevenzip-reader-refactor`.
