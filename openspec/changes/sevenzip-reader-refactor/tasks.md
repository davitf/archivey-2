## 1. Coder registry (Decision 1)

- [ ] 1.1 Add `src/archivey/internal/backends/sevenzip_coders.py` with `CoderKind`
      enum (`COPY`, `LZMA_FILTER`, `SINGLE_STAGE`, `AES`, `REJECT`), a frozen `Coder`
      dataclass (`method`, `algo`, `kind`, `codec`, `pybcj_decoder`), a `CODERS:
      dict[bytes, Coder]` table, and `lookup(method) -> Coder | None`.
- [ ] 1.2 Populate `CODERS` from the current five maps (`_METHOD_*`,
      `_METHOD_ALGORITHMS`, `_BCJ_METHODS`, `_BCJ_PYBCJ_DECODERS`,
      `_SINGLE_STAGE_CODECS`), including every BCJ short and long id.
- [ ] 1.3 Add a table test asserting each method id from the old five maps is present
      with identical `algo`/`codec`/`pybcj_decoder`, and that
      `compression_method_for_coder` output is unchanged for every id.
- [ ] 1.4 Delete the five maps; point `sevenzip_parser.py` and `sevenzip_reader.py`
      lookups at `CODERS`/`lookup`; stop the reader importing `_METHOD_*` privates
      from the parser (re-export thin aliases from the new module only where needed).

## 2. Invert control: pure parser + reader loop (Decision 2)

- [ ] 2.1 Extract pure parser entry points over a header buffer: read signature/next
      header to `bytes` (keep CRC + bounds guards + comments), parse a header buffer
      into an intermediate result exposing `is_encoded_header` and its `_StreamsInfo`,
      and a `finalize` that maps files→folders and builds `SevenZipArchive`.
- [ ] 2.2 Remove `DecodeFolder` and the `decode_folder=` parameter from
      `parse_sevenzip_archive`, `_parse_header`, and `_decode_encoded_header`.
- [ ] 2.3 Move the "decode encoded header → re-parse" loop into `SevenZipReader`,
      replacing the parser's open recursion with an explicitly bounded loop; keep
      password prompting in the reader's decode step.
- [ ] 2.4 Update the three harnesses (`tests/fuzz_sevenzip_parser.py`,
      `tests/atheris_fuzz/targets.py`, `tests/test_atheris_crc_fixup.py`) to call the
      pure parse entry point and run `decode_folder_to_bytes` in their own loop.

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
      `decode_folder_to_bytes` and the reader through it.
- [ ] 3.4 Add a `plan_pipeline` unit test asserting the stage sequence for BCJ+LZMA1,
      BCJ+LZMA2, AES+LZMA2, Delta+LZMA2, plain LZMA2, and COPY.

## 4. Cleanup (Decision 4)

- [ ] 4.1 Rename the reader's `_folder_unpack_size` (member-size sum) to a distinct
      name; leave the parser's bind-pair `_folder_unpack_size` as is.

## 5. Verify

- [ ] 5.1 `uv run pytest tests/test_sevenzip_reader.py tests/test_sevenzip_oracle.py
      tests/test_py7zr_corpus.py tests/fuzz_sevenzip_parser.py
      tests/test_atheris_crc_fixup.py` — all green, unchanged assertions.
- [ ] 5.2 `uv run pyrefly check` and `uv run ty check` clean on both modules and the
      new one.
- [ ] 5.3 Run the suite in `[all]`, `[all-lowest]`, and `[core-only]` configs
      (per CONTRIBUTING "Before pushing") — confirm `[7z]`-gated (pybcj/PPMd/…) and
      core paths behave identically to before.
- [ ] 5.4 `openspec validate --strict sevenzip-reader-refactor`.
