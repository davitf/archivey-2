## Why

Phase 6 needs a zero-dependency native 7z reader so listing and decoding no longer wait on (or import) `py7zr`. The DEV `sevenzip-native-reader` exploration already proved feasibility: stdlib `lzma` `FORMAT_RAW` covers LZMA1/LZMA2 + BCJ + Delta, and the v2 spine (`compressed-streams`, `SharedSource`, password candidates, volume discovery) is ready to host it.

## What Changes

- Implement a **native 7z header parser** and **`SevenZipReader` backend** that registers `ArchiveFormat.SEVEN_Z` (seek required; listing O(1); no third-party import on the read path).
- Decode folders by **composing `compressed-streams`** only (never calling `lzma`/`pyppmd`/crypto from the reader). Finish the PPMd open path and the AES-CBC decrypt stage + 7z SHA-256 KDF.
- **Solid `open()`**: always re-decode from the folder start — **no spool, no disk writes**, no decoded-folder cache.
- **Multi-volume**: join `.7z.001`+ siblings (or an explicit ordered list) by concatenation; incomplete sets error cleanly.
- **Passwords**: candidate model + derived-key cache keyed by `(password, salt, cycles)`; prefer correctness over speculative fast-reject.
- **Anti-items**: list them; on extract, **delete** the destination path (7z CLI parity), under safe-extraction path rules. `py7zr` cannot parse `ANTI` — oracle for this path is the `7z` CLI.
- **Unsupported combinations** (notably BCJ2, and any coder chain we cannot decode correctly without custom/non-stdlib code such as LZMA1+BCJ if stdlib composition fails): raise `UnsupportedFeatureError`, never wrong bytes; document for later.
- Wire **py7zr** (and `7z` CLI where useful) as **test oracles**; activate corpus 7z entries; add an Atheris harness for the header parser.
- **Out of scope (siblings):** RAR native reader; ZIP Deflate64/PPMd shared-codec wiring; 7z writing (`[7z-write]` / Phase 9); general `is_current` / skip-non-current iteration filters.

## Capabilities

### New Capabilities

- (none)

### Modified Capabilities

- `format-7z`: lock solid random-access = re-decode only; specify anti-item list + extract-delete; document unsupported codec combinations.
- `archive-data-model`: add `ArchiveMember.is_anti`.
- `safe-extraction`: anti-item extraction deletes the in-root destination (7z CLI parity) instead of writing bytes.
- `compressed-streams`: complete PPMd stream open + AES decrypt stage (7z KDF feeds `AesParams`).
- `testing-contract`: activate native↔py7zr/7z CLI cross-validation for 7z; anti-item fixtures via `7z` CLI when py7zr cannot parse them.

## Impact

- New: `internal/backends/sevenzip_reader.py`, `internal/backends/sevenzip_parser.py` (names may fold), folder-pipeline helper.
- Touch: `streams/codecs.py` (PpmdCodec), `streams/crypto.py` (AES stage + KDF helpers), `volumes.py` (join), registry/detection, corpus builders/sweep.
- Deps unchanged for core reads; `[7z]` / `[crypto]` remain optional; `py7zr` stays **dev oracle** (+ future `[7z-write]`).
- Public API: new `ArchiveMember.is_anti` field (default `False`); no other breaking surface.
