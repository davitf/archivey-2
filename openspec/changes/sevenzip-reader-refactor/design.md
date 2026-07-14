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

**Sibling implementation (PR #93, `refactor-sevenzip-reader`).** A parallel,
already-merged-green implementation attacked the same problem. It is empirical
evidence, and two of its results steer this design:

| PR #93 result | What we take / change |
| --- | --- |
| Split into **four** modules (`methods` + `parser` + `pipeline` + `reader`); line count **grew** ~2.1k → ~2.3k — "module-boundary + two-phase API overhead offset the table collapse." | Keep **three** modules (registry + parser + reader); pipeline stays in the reader. Drop the "half size" target as disproven. |
| `aliases: tuple[bytes, ...]` on the registry row + `_bcj()`/`_single()` table constructors register each BCJ method once (long id as alias). | Adopt verbatim — tightest table shape. |
| Concrete two-phase API: `Signature`, `PlainHeader \| EncodedHeader`, `read_signature_and_next_header`, `parse_header_block`, `materialize_archive`, `encoded_folder_slices`, plus a thin all-in-one `parse_sevenzip_archive(fp, password=...)` for harnesses. | Adopt this shape (Decision 2) — proven, minimal harness diff. |
| Table-driven `FILES_INFO` handlers. | Adopt (Decision 5). |
| Registry helpers typed as `folder_is_encrypted(folder: object)` with `getattr(...)`, to dodge a registry→parser import cycle. | **Reject** — keep them typed on the parser dataclasses (Decision 1); the cycle is avoided by keeping the registry a pure leaf, not by erasing types. |
| `group_coders` returns stages, but each LZMA-family stage re-dispatches into a nested `_open_lzma_family` that re-scans `has_lzma1/has_lzma2/has_bcj`. | Go further (Decision 3): the planner **flattens** the LZMA1+BCJ sub-staging into concrete stages so `execute` is a rescan-free fold — with a fallback to PR #93's proven grouped shape if flattening proves fragile. |

## Decisions

### 1. New leaf module `sevenzip_coders.py` holding one method registry
A `@dataclass(frozen=True, slots=True) SevenZipMethod` with `method_id: bytes`,
`algorithm: CompressionAlgorithm`, `kind: MethodKind`, `codec: Codec | None`,
`lzma_filter_id: int | None`, `pybcj_attr: str | None`, and
`aliases: tuple[bytes, ...] = ()`. `MethodKind` is an `Enum`
(`COPY`, `AES`, `BCJ2`, `LZMA_FAMILY`, `SINGLE`). A tuple of rows built with
`_bcj(short, long, …)` / `_single(id, algo, codec)` helpers is indexed into
`_BY_ID` (each row under its `method_id` and every alias). Public surface:
`lookup(id) -> SevenZipMethod | None`, `require(id) -> SevenZipMethod` (raises
`UnsupportedFeatureError` naming the id), the `METHOD_COPY/LZMA/LZMA2/DELTA/AES`
singletons, and small predicates `is_bcj`/`is_lzma_family`.

**The module is a pure leaf**: it imports only `Codec`, `CompressionAlgorithm`,
`CompressionMethod`, and `lzma`. It does **not** import the parser dataclasses.
Therefore the folder-level helpers `folder_is_encrypted(folder: SevenZipFolder)`
and `compression_method_for_coder(coder: SevenZipCoder)` stay **typed** in the
parser and call `coders.lookup` — avoiding the registry→parser import cycle
*without* the sibling PR's `object`/`getattr` type erasure. The reader stops
importing `_METHOD_*` from the parser and imports from `sevenzip_coders` instead.

**Rejected:** keeping the table in the parser (reader keeps reaching into parser
privates); attaching the data to the `Codec` enum in `codecs.py` (mixes 7z
method-id semantics into the shared codec layer other formats use); PR #93's
untyped `folder: object` helpers (loses the types the current code has).

### 2. Invert control: pure parser + reader-driven header loop
Adopt the sibling PR's concrete two-phase shape (proven, minimal harness diff).
Parser exposes pure structure functions with an explicit sum type for the header:
```python
def read_signature_and_next_header(fp) -> Signature        # magic + CRCs + all bounds
def parse_header_block(data: bytes) -> PlainHeader | EncodedHeader
def materialize_archive(sig, plain, *, is_header_encrypted) -> SevenZipArchive
def empty_archive(sig) -> SevenZipArchive
def encoded_folder_slices(enc: EncodedHeader) -> Iterable[tuple[folder, offset, csize, usize]]
```
The reader drives the loop (bounded; nested encoded headers still terminate):
```
sig   = parse.read_signature_and_next_header(fp)
block = parse.parse_header_block(sig.header_data)
while isinstance(block, EncodedHeader):
    raw   = self._decode_encoded_header(sig, block)   # pipeline + passwords + key cache
    block = parse.parse_header_block(raw)
archive = parse.materialize_archive(sig, block, is_header_encrypted=…)
```
`parse_sevenzip_archive` loses `decode_folder=`. `DecodeFolder` is deleted. A thin
all-in-one `parse_sevenzip_archive(fp, *, password=None, key_cache=None, …)` that
runs this loop with a single static password is kept for the fuzz/atheris harnesses,
so their diff is a one-line call-site change, not a rewrite.

**Rejected:** keeping the callback but moving it to a Protocol (still one impl,
still threaded through the parser); making the parser import the reader lazily
(hides the cycle instead of removing it); a boolean `is_encoded_header` flag on one
intermediate struct (the `PlainHeader | EncodedHeader` sum type reads better and
type-narrows).

### 3. Split `open_folder_pipeline` into `plan_pipeline` + `execute`
`plan_pipeline(folder) -> list[Stage]` is pure: it runs all validation (num
in/out == 1, `_check_linear_coder_chain`, BCJ2 → `UnsupportedFeatureError`) and
emits typed stage descriptors — `AesStage`, `LzmaChainStage(filters, codec)`,
`BcjStage(decoder_attr, cap_size)`, `CodecStage(codec, properties)`; COPY produces
no stage. `execute(source, stages, *, password, key_cache, …)` is a left fold that
opens each stage and is the only code touching `open_codec_stream` / crypto /
pybcj. `plan_pipeline` is unit-testable without opening a real stream.

**Going beyond the sibling PR:** its `group_coders` returns stages but each
LZMA-family stage still re-dispatches into a nested `_open_lzma_family` that
re-scans `has_lzma1/has_lzma2/has_bcj`. Here the planner **flattens** the LZMA1+BCJ
decision into concrete stages up front — an `LzmaChainStage` for each contiguous
stdlib LZMA1/Delta run, then a `BcjStage(cap_size=run_output)` per BCJ coder — so
`execute` never rescans and the BPO-21872 workaround is expressed as data, not
control flow. LZMA2+BCJ stays one `LzmaChainStage`; BCJ-alone is a series of
`BcjStage`s.

**Fallback:** if flattening the LZMA1+BCJ sub-staging (the one correctness-critical
subtlety, with its `SlicingStream` output caps) proves fragile against the oracle
fixtures, fall back to the sibling PR's shape — a single `LzmaChainStage` kind whose
`execute` handler contains the nested staging. Either way the split (pure planning +
folding execution) and the byte-for-byte oracle result are the acceptance bar.

**Rejected:** leaving the flow inline (the interleave is the complaint); pushing
grouping into `codecs.py` (7z-specific); extracting a separate `pipeline` module
(the sibling PR did and the code grew — keep it in the reader).

### 4. Rename the colliding `_folder_unpack_size`
Parser keeps the bind-pair computation; rename the reader's member-size sum
(e.g. `_folder_decoded_size` / `_folder_member_total`). Purely local rename.

### 5. Parser cleanup without rewriting the format walk
Table-drive the `FILES_INFO` property handlers (a `dict[_Property, handler]`
instead of the long `if/elif` in `_read_files_info`), keeping every allocation and
read bound. Keep the sequential `PACK_INFO → UNPACK_INFO → SUBSTREAMS_INFO` if-chain
(it matches on-wire order) and keep building complete `SevenZipFileRecord`s once at
materialization rather than half-empty-then-mutated. Fold the two password+CRC
confirm paths (encoded header vs. member folder) into one helper. All threat-model
comments (file-count bomb, `_read_exact` ceiling, CRC-vs-attacker) stay verbatim.

**Rejected:** a hand-rolled bit-parser DSL — not enough repetition to pay for it.

### 6. Spec representation: a `testing-contract` preservation gate, not a `format-7z` delta
This refactor changes no caller-visible behavior, so no `format-7z` requirement
changes. OpenSpec still requires ≥1 delta, so express the change where it is
honest: a new `testing-contract` requirement stating the restructure SHALL preserve
the existing 7z suite, with only relocated-symbol call-site test edits allowed. This
mirrors the sibling PR and avoids a misleading no-op edit to a behavior spec.

**Rejected:** a MODIFIED `format-7z` requirement re-asserting unchanged behavior
(signals a change that didn't happen); a `minimalist` no-delta change (loses the
explicit, checkable preservation gate that makes "strictly better, no regressions"
verifiable at apply time).

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
