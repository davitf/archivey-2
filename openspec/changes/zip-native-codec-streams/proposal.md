## Why

ZIP member decompression currently goes through stdlib `zipfile.ZipFile.open()`, which
only decodes DEFLATE / BZIP2 / LZMA. But archivey's codec layer already supports
DEFLATE64 (via the `[7z]` `inflate64` backend), ZSTD, and PPMD, and the registry already
advertises `ZIP → (DEFLATE64, ZSTD, PPMD)` as optional codecs — so today ZIP *claims*
codecs it cannot actually decode: a Deflate64/Zstd/PPMd ZIP member raises
`NotImplementedError` from stdlib `zipfile` instead of decoding. Routing ZIP member data
through the shared `compressed-streams` codec layer (instead of `zipfile`'s internal
decoders) closes that gap, **widens ZIP compatibility beyond the stdlib**, and unifies
error translation + digest verification with every other format. It is also the pragmatic
first step toward a fully native ZIP parser (deferred): the member-data path becomes
archivey's, while stdlib `zipfile` is retained only for central-directory parsing for now.

## What Changes

- Keep stdlib `zipfile` for **central-directory parsing / listing** (unchanged), but read
  each member's **raw compressed bytes** from the source (a bounded local-file-header parse
  to find the data region + a `SlicingStream`) and decode through the shared codec layer
  rather than `ZipExtFile`.
- **Unlock extended codecs for ZIP:** DEFLATE64 (method 9, via `inflate64`), ZSTD (method
  93), PPMD (method 98) decode when the backing extra is installed; a missing backend
  raises `PackageNotInstalledError` (consistent with every other format) instead of
  `zipfile`'s `NotImplementedError`.
- **Unified verification/error translation:** ZIP member reads run through the same
  `VerifyingStream` (`member.hashes["crc32"]`) and decompression-error → `CorruptionError`
  translation as the other backends, replacing the ZIP-specific `NotImplementedError` /
  bare-`EOFError` mapping for the codec path.
- **Encryption boundary (see design):** STORED/DEFLATE members under ZipCrypto and AE-x
  are the one area where `zipfile`'s decryptor is currently load-bearing; the initial cut
  keeps encrypted-member decryption on the existing path and routes only unencrypted
  members through the codec layer, with native ZipCrypto/AE composition scoped as an
  explicit follow-up rather than bundled here.

## Capabilities

### New Capabilities

<!-- none -->

### Modified Capabilities

- `format-zip`: member data decodes through the shared codec layer; DEFLATE64/ZSTD/PPMD
  ZIP members are supported (backend-gated); missing backend → `PackageNotInstalledError`;
  verification/error translation unified with other backends. (Realizes the existing
  `compressed-streams` "future native ZIP … same `inflate64`-backed backend" clause — no
  `compressed-streams` requirement change needed.)

## Impact

- `zip_reader.py`: new raw-member-data path (local-header parse + `SlicingStream` +
  `StreamCodec` dispatch); central-directory listing unchanged. Retain the encrypted-member
  path on `zipfile` for now.
- Public surface: previously-unreadable Deflate64/Zstd/PPMd ZIP members now read (with the
  right extra); error type for a missing codec becomes `PackageNotInstalledError`; a
  corrupt member body still raises `CorruptionError`.
- Registry: the advertised ZIP optional codecs become truthful.
- Corpus: add Deflate64/Zstd/PPMd ZIP fixtures to the sweep.
- **In v1** (user decision): widens ZIP compatibility beyond stdlib for the first release.
