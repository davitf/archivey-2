## Why

Phase 6 needs a zero-dependency native 7z reader so listing and decoding no longer wait on (or import) `py7zr`. The DEV `sevenzip-native-reader` exploration already proved feasibility: stdlib `lzma` `FORMAT_RAW` covers LZMA1/LZMA2 + BCJ + Delta, and the v2 spine (`compressed-streams`, `SharedSource`, password candidates, volume discovery) is ready to host it.

## What Changes

- Implement a **native 7z header parser** and **`SevenZipReader` backend** that registers `ArchiveFormat.SEVEN_Z` (seek required; listing O(1); no third-party import on the read path).
- Decode folders by **composing `compressed-streams`** only (never calling `lzma`/`pyppmd`/crypto from the reader). Finish the PPMd open path and the AES-CBC decrypt stage + 7z SHA-256 KDF.
- **Solid `open()`**: always re-decode from the folder start — **no spool, no disk writes**, no decoded-folder cache.
- **Multi-volume**: join `.7z.001`+ siblings (or an explicit ordered list) by concatenation; incomplete sets error cleanly.
- **Passwords**: candidate model + derived-key cache keyed by `(password, salt, cycles)`; prefer correctness over speculative fast-reject.
- **Anti-items / superseded revisions**: parse the `ANTI` bitmask, expose `is_anti`, and compute a derived `is_current` (last-entry-wins by name; a content member deleted by a later anti-item or re-added later is `is_current=False`). Default extraction reproduces the archive's **final tree** — non-current members are skipped, and an anti-item **never deletes data the extraction did not create** (it is a no-op on disk in the common case). `7z x`-style deletion over a pre-existing tree is a future explicit opt-in, not the default. `py7zr` cannot parse `ANTI` — oracle for this path is the `7z` CLI.
- **Unsupported combinations** (notably BCJ2, and any coder chain we cannot decode correctly without custom/non-stdlib code such as LZMA1+BCJ if stdlib composition fails): raise `UnsupportedFeatureError`, never wrong bytes; document for later.
- Wire **py7zr** (and `7z` CLI where useful) as **test oracles**; activate corpus 7z entries; add an Atheris harness for the header parser.
- **Out of scope (siblings):** RAR native reader; ZIP Deflate64/PPMd shared-codec wiring; 7z writing (`[7z-write]` / Phase 9); computing `is_current` shadowing for other formats (ZIP/TAR duplicate names — they default `is_current=True` until a sibling change); the opt-in `7z x`-style differential-restore extraction mode.

## Capabilities

### New Capabilities

- (none)

### Modified Capabilities

- `format-7z`: lock solid random-access = re-decode only; specify anti-item list + `is_current` computation; document unsupported codec combinations.
- `archive-data-model`: add `ArchiveMember.is_anti` (raw ANTI bit) and `ArchiveMember.is_current` (derived last-entry-wins by name).
- `safe-extraction`: skip non-current members by default; anti-item extraction never deletes data the extraction did not create (no-op on disk in the common case) instead of writing bytes.
- `compressed-streams`: complete PPMd stream open + AES decrypt stage (7z KDF feeds `AesParams`).
- `testing-contract`: activate native↔py7zr/7z CLI cross-validation for 7z; anti-item fixtures via `7z` CLI when py7zr cannot parse them.

## Impact

- New: `internal/backends/sevenzip_reader.py`, `internal/backends/sevenzip_parser.py` (names may fold), folder-pipeline helper.
- Touch: `streams/codecs.py` (PpmdCodec), `streams/crypto.py` (AES stage + KDF helpers), `volumes.py` (join), registry/detection, corpus builders/sweep.
- Deps unchanged for core reads; `[7z]` / `[crypto]` remain optional; `py7zr` stays **dev oracle** (+ future `[7z-write]`).
- Public API: new `ArchiveMember.is_anti` field (default `False`) and `ArchiveMember.is_current` field (default `True`); both additive, no other breaking surface.
