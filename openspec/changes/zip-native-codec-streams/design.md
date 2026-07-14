## Context

`zip_reader.py` builds a `zipfile.ZipFile` and serves members via `ZipFile.open()`
(`ZipExtFile`), which decodes only DEFLATE/BZIP2/LZMA and decrypts ZipCrypto. The codec
layer (`compressed-streams`) already has `StreamCodec` descriptors + default backends for
STORED/DEFLATE/DEFLATE64(`inflate64`)/BZIP2/LZMA/ZSTD/PPMD, plus `VerifyingStream` and a
uniform decompression-error → `CorruptionError` translation. `internal/registry.py`
already lists `ZIP → (DEFLATE64, ZSTD, PPMD)` optional codecs and maps method ids in
`zip_reader` (`9→DEFLATE64`, etc.), but the decode path never reaches those backends.
`format-zip` §56 already **rejects non-seekable ZIP sources**, so the member-data path can
always seek — no forward-only complication.

## Goals / Non-Goals

**Goals:**
- Decode ZIP member bodies through the shared codec layer, unlocking DEFLATE64/ZSTD/PPMD.
- Unify ZIP verification + decompression-error translation with the other backends.
- Make the registry's advertised ZIP codecs truthful.
- Lay the groundwork for a future fully-native ZIP parser without committing to it now.

**Non-Goals:**
- A native central-directory / EOCD parser (deferred — separate later change; stdlib
  `zipfile` keeps doing the parsing/listing here).
- Native ZipCrypto/AE decryption (scoped as an explicit follow-up; see decisions).
- ZIP writing (separate phase).
- Salvage / local-header-walk reading (separate later change).

## Key decisions

- **Locate raw member data with a bounded local-header parse.** `ZipInfo.header_offset`
  gives the local file header start; the data region begins after the fixed 30-byte header
  + `n`(name) + `m`(extra) as read from the *local* header (central-directory extra can
  differ). Parse only those bytes, then `SlicingStream(source, data_start, compress_size)`
  is the raw compressed input. This is a small, well-bounded parse — not the full native
  parser — and must apply the same count/length bounds discipline as the 7z parser
  (reject absurd name/extra lengths).
- **Dispatch by method id through `StreamCodec`.** Reuse the existing method-id →
  `Codec`/`CompressionAlgorithm` map; hand the sliced raw stream to the codec's default
  backend. STORED becomes a passthrough slice. Missing optional backend →
  `PackageNotInstalledError`, exactly like 7z/single-file.
- **Verify via the shared stage.** Wrap the decoded stream in `VerifyingStream` with
  `member.hashes` (`crc32` already populated from the central directory), replacing
  reliance on `ZipExtFile`'s internal CRC check. One verification path across all formats.
- **Encryption stays on `zipfile` initially.** ZipCrypto (traditional) and AE-x (WinZip
  AES) prepend/também wrap the compressed stream; composing them with the codec layer is
  real work (AE-1/AE-2 header, HMAC, key derivation; the `[crypto]` AES stage exists but is
  wired for 7z/RAR, not ZIP's AE framing). To keep this change bounded and low-risk, an
  **encrypted** member keeps decoding via the existing `zipfile` path; only **unencrypted**
  members route through the codec layer. Native ZipCrypto/AE composition is a named
  follow-up. This preserves current encrypted-ZIP behavior exactly.
- **Concurrency.** The current reader already serializes `ZipFile.open`/`close` under
  CONCURRENT and relies on `_SharedFile` for parallel member reads. The raw-slice path
  reads from the shared source through the existing handle-lock/`SharedSource` discipline;
  reuse it so independent members still decode in parallel and free-threading stays correct.

## Open questions (resolve during apply)

- **Deflate64 backend gating:** `inflate64` currently lives in the `[7z]` extra. Decide
  whether ZIP Deflate64 reuses `[7z]` as-is (documented) or `inflate64` gets promoted into
  a shared/`[recommended]` grouping so "ZIP compat" doesn't read as "install the 7z extra."
- **Zstd/PPMd ZIP method framing:** confirm ZIP's method-93 zstd and method-98 PPMd wire
  formats match what the existing `ZSTD`/`PPMD` backends expect (ZIP PPMd carries its own
  2-byte parameter header — verify against a real fixture/oracle before enabling).
- **Fixtures:** generating Deflate64/Zstd/PPMd ZIPs needs a producer (7-Zip/WinZip);
  decide oracle + on-demand generation, skip-when-absent like the other oracle paths.
- Whether STORED encrypted members (ZipCrypto over STORED — the existing hand-rolled
  password path) also stay on the current path (yes, per the encryption decision).
