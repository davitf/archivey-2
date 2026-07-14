# Design — Refactor native 7z reader/parser

## Context

Native 7z reading landed as `sevenzip_parser.py` (~1065 lines) +
`sevenzip_reader.py` (~1068 lines). Behavior matches `format-7z` (native header
parse, shared `compressed-streams` decode, solid folders, AES via `[crypto]`,
LZMA1+BCJ via `pybcj`, BCJ2 rejected). ADR 0001 forbids falling back to `py7zr`
for reads.

The code works, but review/maintain cost is high relative to capability:

- Method IDs are duplicated across `_METHOD_*` constants, `_METHOD_ALGORITHMS`,
  `_BCJ_METHODS`, `_BCJ_PYBCJ_DECODERS`, and `_SINGLE_STAGE_CODECS` (BCJ short/long
  forms appear four times). The reader imports private `_METHOD_*` from the parser.
- Encoded-header materialization is injected as
  `decode_folder: Callable[[BinaryIO, SevenZipFolder, int, int], bytes]` into
  recursive `_parse_header`, with a single production implementation
  (`SevenZipReader._decode_header_folder` → `decode_folder_to_bytes`).
- Folder decode flow is nested (`open_folder_pipeline` → `_open_lzma_run` →
  combined / staged / BCJ-only branches) and recomputes `has_lzma1` /
  `has_lzma2` in multiple helpers.
- `SevenZipFileRecord` is constructed half-empty then mutated by
  `_map_files_to_folders`; thin wrappers (`_open_folder_pipeline`) only forward
  kwargs; password+CRC confirm is duplicated for header vs members.

Provenance: explore session on the live modules; `archivey-dev`
`docs/sevenzip-native-reader-design.md` for the original native-first shape
(linear chains, BCJ2 reject, pull-based folder pipeline).

## Goals / Non-Goals

**Goals:**

- Same caller-visible behavior and the same safety envelope (header bounds, CRC
  gates, linear-chain validation, LZMA1+BCJ staging, BCJ2 / non-linear reject,
  password confirm via folder/member CRC).
- Target ~half the current line count (~1.0–1.2k across the 7z backend modules)
  by collapsing tables and indirection — not by deleting codecs.
- Make the decode path skimmable top-to-bottom: registry → grouped stages →
  stream.
- Keep the parser codec/crypto-free without a callback.

**Non-Goals:**

- New codecs, BCJ2 support, multi-pack / non-linear coder graphs.
- Public API, extras matrix, or `format-7z` requirement changes.
- Rewriting shared `compressed-streams` / crypto helpers.
- Performance work beyond “no intentional regression.”

## Investigations

### Current size and hotspots

| Module / area | ~Lines | Notes |
|---|---:|---|
| `sevenzip_parser.py` | 1065 | 39 funcs; largest: `parse_sevenzip_archive`, `_read_folder`, `_read_substreams_info`, `_read_files_info` |
| `sevenzip_reader.py` | 1068 | Pipeline helpers ~350; `SevenZipReader` methods (esp. `_to_member` ~90) |
| Method ID sprawl | ~120 | Four parallel maps + 13 cross-module private imports |
| `DecodeFolder` DI + recursion | ~80 | One real impl; tests use boom stubs for pre-decode hostile headers |
| LZMA staging nest | ~150 | Three special cases; duplicated family scans |

### Why the callback exists today

Parser must not import codecs/passwords. Encoded headers need the same folder
pipeline as members (including AES + password prompting). Inlining decode into
`_parse_header` forced a dependency inversion via `decode_folder=`. RAR does not
use this pattern; a two-phase parse owned by the reader achieves the same
layering without a `Callable` arg.

### Safety checklist (must not regress)

| Invariant | Where today |
|---|---|
| `nextHeader` size/offset caps; `_read_exact` vs remaining buffer | parser |
| `num_files ≤ header_size`; `_MAX_NUM_STREAMS` / UTF-16 cap | parser |
| Signature + next-header CRC | parser |
| Folder/substream CRC on decode; wrong-password CRC confirm | reader |
| Linear `packed_indices == [0]` + bind pairs `(i+1, i)` | reader |
| LZMA1+BCJ staged via pybcj (not combined liblzma) | reader |
| BCJ2 / multi-in/out / external refs → typed errors | both |

Existing `tests/test_sevenzip_*.py`, corpus, password, and codec suites are the
behavioral oracle for this change (`testing-contract` delta).

## Decisions

### 1. Module split: methods + parser + pipeline + reader

Keep a clear layering:

| Module | Owns |
|---|---|
| `sevenzip_methods.py` (new) | Method registry + method-id lookups (`lookup`/`require`, `is_bcj`/`is_lzma_family`) — a **pure leaf** importing no other 7z module |
| `sevenzip_parser.py` | Signature/header structure only; returns plain or encoded header descriptors. Also hosts the folder-level helpers `folder_is_encrypted` / `compression_method_for_coder` (typed against the folder/coder dataclasses; they call `methods.lookup`) |
| `sevenzip_pipeline.py` (new, or kept as top-level funcs moved out of reader) | `open_folder_pipeline`, `decode_folder_to_bytes` |
| `sevenzip_reader.py` | `SevenZipReader` / backend: passwords, members, solid, open |

Exact filenames can be `sevenzip_methods.py` + moving pipeline helpers out of the
reader file; avoid a deep package unless imports get noisy.

**Rejected:** One mega-file “so the flow is obvious” — the split is right; the
callback across the split was wrong. **Rejected:** Plugin/codec-provider DI for
one backend.

### 2. Two-phase header parse (kill `decode_folder=`)

```
read_signature_and_next_header(fp) -> bytes   # bounds + CRCs
parse_header_block(bytes) -> PlainHeader | EncodedHeader(streams)

Reader:
  block = parse_header_block(next_header_bytes)
  while isinstance(block, EncodedHeader):
      raw = decode_with_passwords(...)   # uses pipeline + key cache
      block = parse_header_block(raw)
  archive = materialize(block.plain)
```

Parser stays codec-free. Password prompting stays in the reader. Hostile-header
unit tests call signature/block parse directly (no boom stub). Nested encoded
headers still work via the loop.

**Rejected:** Keep `DecodeFolder` “for testability” — tests can target the smaller
parse surface. **Rejected:** Parser imports reader pipeline (circular / layering
violation).

### 3. Single method registry

```python
class MethodKind(Enum):
    COPY = ...
    AES = ...
    BCJ2 = ...
    LZMA_FAMILY = ...  # LZMA1, LZMA2, Delta, BCJ
    SINGLE = ...       # Deflate, BZip2, Zstd, …

@dataclass(frozen=True)
class SevenZipMethod:
    method_id: bytes
    algorithm: CompressionAlgorithm
    kind: MethodKind
    codec: Codec | None = None
    lzma_filter_id: int | None = None
    pybcj_attr: str | None = None
    aliases: tuple[bytes, ...] = ()
```

Register each logical method once; BCJ short (`0x04`–`0x09`) and long
(`0x03030103`…) IDs are aliases of one entry. Pipeline and metadata mapping both
`lookup(coder.method)`.

IDs stay `bytes` (variable-length) — not an `IntEnum`.

The registry is a **pure leaf**: it imports only `Codec` / `CompressionAlgorithm`
and knows nothing of the parser dataclasses. The two folder-level helpers that need
those types — `folder_is_encrypted(folder)` and `compression_method_for_coder(coder)`
— therefore live in `sevenzip_parser` and call `lookup`, so they stay fully typed
against `SevenZipFolder` / `SevenZipCoder` instead of `object` + `getattr` (which
would be the price of putting them in the leaf and importing parser types back).

**Rejected:** Four parallel dicts “because concerns differ.” **Rejected:**
Per-coder strategy classes for ~15 methods. **Rejected:** keeping the folder helpers
in the registry with `folder: object` / `getattr` typing to dodge the import cycle.

### 4. Registry-driven pipeline: pure `plan_folder` + `execute` fold

Split the folder decode into a pure planning pass and a stream-opening fold, so the
coder-grouping / staging decisions are inspectable and never interleaved with I/O:

```
plan_folder(folder) -> list[_Stage]     # PURE: validate wiring, flatten the chain
    # _AesStage | _CodecStage | _LzmaChainStage(filters, cap_size) | _BcjStage(attr, size)

open_folder_pipeline(source, folder, …):
    stages = plan_folder(folder)
    if any(_BcjStage in stages): _require_pybcj()   # fail fast, before opening a stream
    stream = source
    for stage in stages:                            # the only I/O-touching code
        stream = _execute_stage(stream, stage, …)
    return stream
```

Planning flattens the special cases into concrete stage *data* — crucially, the
LZMA1+BCJ workaround becomes a capped `_LzmaChainStage` plus per-BCJ `_BcjStage`s
rather than control flow, so `execute` needs no rescans of `has_lzma1/has_lzma2/has_bcj`:

| Case | Emitted stages |
|---|---|
| LZMA2 ± Delta ± BCJ | one `_LzmaChainStage` (BCJ folded into the `FORMAT_RAW` filter list; no pybcj) |
| LZMA1 + BCJ | `_LzmaChainStage(cap_size=run output)` per stdlib LZMA1/Delta run + `_BcjStage` per BCJ (BPO-21872) |
| BCJ alone | `_BcjStage` each |
| AES | `_AesStage` → `open_aes_decrypt_stream` |
| SINGLE | `_CodecStage` → `open_codec_stream(method.codec, …)` |
| COPY | no stage |
| BCJ2 / non-linear / multi-in-out | `UnsupportedFeatureError` (raised during planning) |

`_require_pybcj()` now runs up front when the plan contains a `_BcjStage` — before any
stream opens. The only observable change is precedence in the pathological
"encrypted + LZMA1+BCJ + pybcj absent" case (missing-pybcj now surfaces before the
password prompt); every non-pathological path is byte-identical.

**Rejected:** “Always use pybcj for BCJ” (LZMA2+BCJ is core stdlib per `format-7z`).
**Rejected:** Combined liblzma for LZMA1+BCJ (BPO-21872 silent truncation).
**Rejected:** `group_coders` returning stages that a handler then re-dispatches into a
nested LZMA-family helper (the original shape; it rescanned the run inside execute).

### 5. Parser cleanup without rewriting the format walk

- Keep sequential `PACK_INFO → UNPACK_INFO → SUBSTREAMS_INFO` as an if-chain
  (matches on-wire order).
- Table-drive `FILES_INFO` property handlers.
- Keep `_FileProps` (or equivalent) during FILES_INFO; **materialize** complete
  `SevenZipFileRecord`s once when mapping folders/substreams — no placeholder
  `None` fields on a “finished” record.
- Preserve all allocation/read bounds listed in Investigations.

**Rejected:** Hand-rolled bit-parser DSL — not enough repetition to pay off.

### 6. Shared password + CRC confirm

One internal helper: try `_PasswordCandidates`, decode folder (or header folder),
verify folder digest else per-member digests, cache successful KDF password per
folder index. Header path and `_password_for_folder` both call it.

Drop `_open_folder_pipeline` if it only forwards to `open_folder_pipeline`.

### 7. No `format-7z` / packaging delta

Caller-visible requirements already cover codecs, solid access, encryption, and
bounds. This change adds only a `testing-contract` preservation gate so apply has
an explicit behavioral checklist.

## Risks / Trade-offs

| Risk | Mitigation |
|---|---|
| Accidental behavior change in edge coder chains | Full existing 7z suite + oracle/corpus before merge; no intentional golden updates |
| Registry miss for rare method ID alias | Table includes both short and long BCJ forms; unknown → `UnsupportedFeatureError` (same as today) |
| Two-phase parse mishandles nested encoded headers | Loop until `PlainHeader`; cover encrypted-header fixtures |
| Line-count target tempts deleting comments/safety | Safety checklist is blocking; comments that encode BPO-21872 / bound rationale stay |
| Large diff, hard review | Land methods registry + two-phase parse first if needed; keep commits/task slices small |

## Open Questions

None blocking. Optional follow-up: short decision note beside ADR 0001 documenting
the methods/pipeline/reader split (docs-only; not required to apply).

**Line-count note (post-implement):** landed ~2.3k across the four modules vs ~2.1k
before. The half-size aspiration was not met — module-boundary + two-phase API
overhead offset the table collapse — but the structural goals (one registry, no
`decode_folder` DI, registry-driven pipeline) landed. Further shrinkage would cut
into the irreducible header walk or safety comments; deferred. Clarity, not size, is
the win here (see the two follow-up refinements below).

**Follow-up refinements (post-merge of the initial implementation):** two clarity/
correctness cleanups landed on top of the first implementation, both behavior-preserving
and verified against the full suite + all three dependency legs:
- Typed the folder helpers in the parser instead of `folder: object` + `getattr`
  in the registry (Decision 3), keeping the registry a pure leaf without erasing types.
- Split `open_folder_pipeline` into the pure `plan_folder` + `execute` fold (Decision 4),
  flattening the LZMA1+BCJ staging into stage data so no `has_lzma1/has_lzma2/has_bcj`
  rescan remains inside the execute path.
