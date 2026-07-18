## Context

api-coherence Q6 investigated stored-digest parity for single-file compressors
(`review/api-coherence/QUESTIONS.md`). Today:

- Gzip peeks a single-member trailer CRC-32; multi-member → omit.
- Lzip’s `_read_index_backwards` walks every trailer (`crc32`, `data_size`,
  `member_size`) but **discards** the CRC; metadata surfaces CRC only when the
  file is a single member (`member_size == compressed_size`).
- Zlib is documented as “no cheap whole-member CRC” — wrong: RFC 1950 stores
  Adler-32 of uncompressed data as the last 4 bytes (network order).
- XZ index knows per-block sizes + check *type* but does not read check bytes;
  default check is CRC64; SHA-256 is not combinable.
- `zlib.crc32_combine` / `adler32_combine` exist in CPython only from 3.15;
  archivey pins 3.11+.

The **type** change (`HashAlgorithm`, values always `bytes`) is a separate
api-coherence review fix and is a **prerequisite** for this change’s public
surface. This design assumes that typing is already landed.

## Goals / Non-Goals

**Goals:**

- Cheap zlib `ADLER32` on seekable single-stream sources.
- Whole-member `CRC32` for multi-member lzip via combine over index trailers.
- Verify path can check `adler32` when expected.
- Spec/docs/sweep matrix match reality.

**Non-Goals:**

- Introducing `HashAlgorithm` / migrating crc32 `int`→`bytes` (review fix).
- Gzip multi-member combine (mid-member trailers not cheap without decompress;
  ISIZE mod 2³²).
- XZ multi-block / CRC64 / SHA-256 whole-stream digests.
- Zstd content checksums (no frame parser yet).
- Claiming derived combined digests are “stored as one field by the format” —
  they are derived from stored per-unit digests; still valid for dedupe.

## Investigations

### Combine algebra (verified on CPython 3.11)

| Algorithm | Combine with `(d1, d2, len2)`? | Stdlib on 3.11 |
|---|---|---|
| Adler-32 | Yes (`adler32_combine`) | No — implement ~15 lines |
| CRC-32 (ISO/zlib poly) | Yes (`crc32_combine`) | No — implement GF(2) helper |
| CRC32c / CRC64 | Same idea, different poly | N/A this change |
| SHA-256 | No | — |

Adler combine matched `zlib.adler32(a+b)` in a smoke check. CRC combine is the
well-known zlib `crc32_combine_` (matrix powers of the zero operator).

### Per-format acquisition

| Format | Single-unit peek | Multi-unit combine |
|---|---|---|
| zlib | Last 4 bytes = Adler-32 if complete single stream | Concatenated zlib rare; omit unless we detect splits |
| lzip | Already | Index has every CRC + exact u64 size → **do it** |
| gzip | Already | Blocked on finding mid trailers without decompress |
| xz | Single-block CRC32 possible later | Default CRC64; SHA-256 no; out of scope |

### Lzip index today

`_read_index_backwards` unpacks `<IQQ` then keeps only `(start, data_size,
member_size)`. Changing the entry to retain `crc32` is localized; combine folds
left-to-right over members in archive order (same order as decompressed
concatenation).

## Decisions

### 1. Prerequisite: HashAlgorithm typing from api-coherence

Implement against `Mapping[HashAlgorithm, bytes]` and
`HashAlgorithm.ADLER32` / `CRC32`. **Rejected:** shipping string keys `"adler32"`
in this change then migrating twice.

### 2. Small pure-Python combine helpers under `internal/hashing/`

Add `crc32_combine` and `adler32_combine` (and tests) rather than waiting for
3.15 or adding a native dep. **Rejected:** soft-depend on 3.15-only stdlib;
**Rejected:** shell out / copy entire zlib C.

### 3. Zlib: peek last 4 bytes, gate like gzip

Seekable/path, file long enough for header+deflate+Adler, treat as single
stream (no concat detector unless we already have one). Store
`hashes[ADLER32]` as 4 bytes big-endian (matches wire + digest convention).
Omit on non-seekable. **Rejected:** forcing a decompress pass to “confirm”
Adler placement.

### 4. Lzip: always surface combined CRC32 when index is available

Whether one or many members: fold trailer CRCs with `crc32_combine` and exact
`data_size`s. Single-member degenerates to the trailer CRC. SEEKABLE/path gate
unchanged (index already requires that). **Rejected:** exposing only the last
member’s CRC (lies about whole synthetic member). **Rejected:** leaving
multi-member empty forever.

### 5. Docs: call multi-member lzip digest “derived” where it matters

`docs/formats.md` matrix: zlib → `adler32`; lzip → `crc32` (combined when
multi-member). One sentence that multi-member lzip’s value equals
`crc32(concat(parts))` derived from stored per-member CRCs. **Rejected:**
hiding the derivation (VISION: no surprises as data/docs).

### 6. Defer gzip/xz multi-unit

Keep current single-unit-only rules. Revisit if a cheap member walker appears.
**Rejected:** shipping a heuristic gzip magic-scan combiner (false boundaries).

### 7. Verify registry gains `adler32`

Mirror `crc32` hasher wrapping `zlib.adler32`, digest 4 bytes big-endian.
Needed so a future path that installs expected Adler (or standalone codec
streams) verifies instead of `DIGEST_UNVERIFIABLE`.

## Risks / Trade-offs

- **[Risk] Trailing junk after zlib stream** → last-4-byte peek is wrong.
  **Mitigation:** same class of risk as trusting gzip EOF trailer; omit if we
  later detect trailing bytes; document “complete single zlib file” assumption.
- **[Risk] Combined lzip CRC used for dedupe across tools that hash only the
  last member** → mismatch with naive tools.
  **Mitigation:** document derivation; value matches full decompressed concat.
- **[Risk] Stacking before HashAlgorithm lands** → churn.
  **Mitigation:** tasks explicitly wait on / assume the type PR.

## Open Questions

None blocking — endianness of crc32 bytes is owned by the typing prerequisite
change; Adler wire order is already BE.
