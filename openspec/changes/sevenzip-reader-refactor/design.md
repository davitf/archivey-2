## Context

The native 7z backend is two modules, ~2133 lines total:

- `sevenzip_parser.py` (1065) — signature/header parse → dataclasses
  (`SevenZipArchive`, `SevenZipFolder`, `SevenZipCoder`, `SevenZipFileRecord`).
- `sevenzip_reader.py` (1068) — dataclasses → member list + decode streams,
  password handling, cost/metadata.

The behavior is correct and covered by `test_sevenzip_reader.py`,
`test_sevenzip_oracle.py`, `test_py7zr_corpus.py`, and three fuzz/atheris harnesses.
This change is a **pure refactor**: the `format-7z` spec requirements are unchanged.
Three specific structures are hard to follow:

1. **Injected `decode_folder` callback.** `parse_sevenzip_archive(fp, *, decode_folder)`
   threads a `DecodeFolder = Callable[[BinaryIO, SevenZipFolder, int, int], bytes]`
   through `_parse_header` (recursively) and `_decode_encoded_header`. It has exactly
   one production implementation (`SevenZipReader._decode_header_folder`) plus three
   near-identical test shims. It exists only to keep the parser from importing the
   codec/crypto stack (a parser→reader import would be circular).

2. **Five method-id-keyed maps, split across both files, with duplication:**
   | Map | File | `bytes` → |
   | --- | --- | --- |
   | 13× `_METHOD_*` consts | parser | id literal |
   | `_METHOD_ALGORITHMS` | parser | `CompressionAlgorithm` (BCJ ids inline) |
   | `_BCJ_METHODS` | reader | `Codec` |
   | `_BCJ_PYBCJ_DECODERS` | reader | pybcj class-name `str` |
   | `_SINGLE_STAGE_CODECS` | reader | `Codec` |
   The BCJ ids appear as raw byte literals in both `_METHOD_ALGORITHMS` and `_BCJ_METHODS`.
   The reader imports 11 private `_METHOD_*` names from the parser.

3. **`open_folder_pipeline` interleaves grouping with I/O.** Coder-run grouping,
   rejection of unsupported wiring (multi in/out, non-linear, BCJ2), and the
   LZMA1+BCJ staging workaround are all inline with stream construction in one
   ~80-line function plus `_open_lzma_run` / `_open_lzma_combined`.

## Goals / Non-Goals

**Goals:**
- One coder registry as the single source of truth for method-id → (algorithm,
  codec, staging kind, pybcj decoder, lzma filter id), in a new shared module.
- Parser becomes pure `bytes → structures`; the reader owns the encoded-header
  decode loop. No injected callables.
- Folder decode split into a pure planner (`plan_pipeline`) and a thin `execute` fold.
- Byte-for-byte identical decode output, identical errors/messages, identical
  safety guards and threat-model comments. No public or extras/deps change.

**Non-Goals:**
- No new codec support, no BCJ2 support, no change to the LZMA1+BCJ correctness
  workaround (only its location).
- No change to `codecs.py`, `crypto.py`, or the `Codec` enum.
- Not chasing a literal "half the size": the parse structure walk, member/metadata
  mapping, and threat-model comments are essential. Target is meaningful shrinkage
  of the three named areas with a large clarity gain, not a line quota.

## Investigations

**Callback call sites** (`grep`): production `SevenZipReader._decode_header_folder`
(the only one wrapping passwords); test shims in `tests/fuzz_sevenzip_parser.py`,
`tests/atheris_fuzz/targets.py`, `tests/test_atheris_crc_fixup.py`, each a 3-line
wrapper over `decode_folder_to_bytes` with no password handling. Inverting control
lets the harnesses call the pure `parse_header_buffer` and run the decode loop
themselves — strictly closer to what they fuzz (the structure walk).

**Encoded-header nesting.** The parser recurses `_parse_header` after decoding an
encoded header. Real 7z is single-level (an encoded header decodes to a plain
header). The reader-driven loop replaces open recursion with a bounded loop.

**Registry coverage check.** Every current lookup maps cleanly onto one row:
`_METHOD_ALGORITHMS.get` → `.algo`; `_BCJ_METHODS.get`/`_SINGLE_STAGE_CODECS.get`
→ `.codec`; `_BCJ_PYBCJ_DECODERS.get` → `.pybcj_decoder`; `_is_lzma_family` →
`.kind in {LZMA_FILTER}` (LZMA/LZMA2/Delta/BCJ); `folder_is_encrypted` →
`.kind is AES`; BCJ2 rejection → `.kind is REJECT`. `LZMA_FILTER_IDS` (in
`codecs.py`) stays where it is; the registry references `Codec` members, and the
existing `LZMA_FILTER_IDS[codec]` lookup is reused by the planner.

**Name collision.** `_folder_unpack_size` is defined in both modules:
parser (`sevenzip_parser.py:885`) computes the folder's output size from bind
pairs; reader (`sevenzip_reader.py:808`) sums member sizes for a folder index.
Same name, different concept — a real readability trap.

## Decisions

### 1. New module `sevenzip_coders.py` holding one `Coder` registry
A `@dataclass(frozen=True) Coder` with fields for `method` (id bytes), `algo:
CompressionAlgorithm`, `kind: CoderKind`, `codec: Codec | None`, and
`pybcj_decoder: str | None`; a module-level `CODERS: dict[bytes, Coder]` plus a
small `lookup(method) -> Coder | None`. `CoderKind` is an `Enum`
(`COPY`, `LZMA_FILTER`, `SINGLE_STAGE`, `AES`, `REJECT`). Both parser and reader
import from here; the reader stops importing `_METHOD_*` from the parser. Named
`_METHOD_*` constants that other code references by name are re-exported from the
new module (or kept as thin aliases) so imports stay stable where convenient.

**Rejected:** keeping the table in the parser (reader keeps reaching into parser
privates); attaching the data to the `Codec` enum in `codecs.py` (mixes 7z
method-id semantics into the shared codec layer, which other formats use).

### 2. Invert control: pure parser + reader-driven header loop
Parser exposes pure functions over a header buffer — read the signature/next
header to `bytes`, parse a header buffer into an intermediate result, and expose
whether it is an encoded header plus its `_StreamsInfo`. The reader loop:
```
hdr_bytes = parser.read_next_header(fp)          # signature + CRC + bounds
parsed    = parser.parse_header_buffer(hdr_bytes)
while parsed.is_encoded_header:                   # bounded (cap small N)
    hdr_bytes = self._decode_encoded_header(parsed.encoded_streams)
    parsed    = parser.parse_header_buffer(hdr_bytes)
archive = parser.finalize(parsed)                # map files→folders, build SevenZipArchive
```
`parse_sevenzip_archive` loses `decode_folder=`. `DecodeFolder` is deleted.

**Rejected:** keeping the callback but moving it to a Protocol (still one impl,
still threaded through the parser); making the parser import the reader lazily
(hides the cycle instead of removing it).

### 3. Split `open_folder_pipeline` into `plan_pipeline` + `execute`
`plan_pipeline(folder) -> list[Stage]` is pure: it runs all validation (num
in/out == 1, `_check_linear_coder_chain`, BCJ2 → `UnsupportedFeatureError`),
groups LZMA-family runs, and emits typed stage descriptors — `AesStage`,
`LzmaChainStage(filters, codec)`, `BcjStage(decoder_attr, cap_size)`,
`CodecStage(codec, properties)`, with COPY producing no stage. The LZMA1+BCJ
decision (stdlib LZMA1/Delta chain, then per-BCJ pybcj stages with `SlicingStream`
size caps) is encoded as the sequence of stages the planner emits, so the
correctness workaround is preserved verbatim — only relocated. `execute(source,
stages, *, password, key_cache, ...)` is a left fold opening each stage; it is the
only part that touches `open_codec_stream` / crypto / pybcj. `plan_pipeline` is
unit-testable without opening a real stream.

**Rejected:** leaving the flow inline (the interleave is the complaint); pushing
grouping into `codecs.py` (7z-specific).

### 4. Rename the colliding `_folder_unpack_size`
Parser keeps the bind-pair computation; rename the reader's member-size sum
(e.g. `_folder_decoded_size` / `_folder_member_total`). Purely local rename.

## Risks / Trade-offs

- [Correctness regression in the LZMA1+BCJ workaround] → The staging is the one
  load-bearing subtlety. Mitigation: keep the exact sequence (stdlib LZMA1+Delta
  chain, then pybcj stages with output-size `SlicingStream` caps); rely on the
  existing 7z-CLI and py7zr BCJ+LZMA1 oracle fixtures (`test_sevenzip_oracle.py`,
  the LZMA1+BCJ scenarios) to catch any drift; add a `plan_pipeline` unit test
  asserting the emitted stage sequence for BCJ+LZMA1, BCJ+LZMA2, AES+LZMA2, Delta.
- [Fuzz harness drift] → Update all three harnesses in the same change and run
  them so the parser fuzz path still exercises encoded headers.
- [Registry omission] → A missing row silently loses a codec. Mitigation: a table
  test asserting every method id currently in the five maps is present with the
  same `algo`/`codec`/`pybcj_decoder`, and that `compression_method_for_coder`
  output is unchanged for each.

## Open Questions

None blocking. Whether to keep `_METHOD_*` aliases re-exported for import
stability vs. updating all references to `CODERS`/named members is an
implementation-detail call to be settled during apply (favor the smaller diff
where it does not reintroduce the split).
