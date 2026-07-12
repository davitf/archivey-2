## 1. Data model + crypto/codec stubs

- [x] 1.1 Add `ArchiveMember.is_anti: bool = False` (raw ANTI bit) and `ArchiveMember.is_current: bool = True` (derived last-entry-wins) — types + archive-data-model consumers/tests; both included in equality
- [x] 1.2 Implement the format-agnostic AES-CBC decrypt stage on the shared crypto surface, and a **7z-local** SHA-256 KDF helper beside it (UTF-16LE pw + salt + `1 << NumCyclesPower`, `0x3f` special case) — not on the generic crypto backend; emits `AesParams`; key cache helper by `(password, salt, cycles)`; reader never imports `cryptography`
- [x] 1.3 Finish `PpmdCodec.open` for PPMd var.H parameters from 7z coder properties
- [x] 1.4 Update stale "Phase 7" comments in crypto/PPMd paths to Phase 6

## 2. Native header parser

- [x] 2.1 Add `sevenzip_parser.py`: signature header, end-header CRC, `HEADER` / `ENCODED_HEADER`
- [x] 2.2 Parse `PACK_INFO`, `UNPACK_INFO` (folders, coders, bind pairs), `SUBSTREAMS_INFO`, `FILES_INFO` (names, times, attrs, empty/anti bitmasks, comment)
- [x] 2.3 Map files → `(folder_index, file_in_folder)`, sizes/CRCs, `is_solid`, per-folder encryption, `compression` chains
- [x] 2.4 Header-encrypted archives: decrypt via candidates + KDF cache before parse; no password → `EncryptionError`

## 3. Reader backend + folder decode

- [x] 3.1 Implement `SevenZipReader` / `SevenZipReadBackend`; register; require seek; use `SharedSource` for pack views
- [x] 3.2 Folder pipeline: compose `compressed-streams` (+ AES stage) in reverse coder order; verify per-member CRC32
- [x] 3.3 `stream_members()`: decode each folder once, slice by substream size (pull, no spool)
- [x] 3.4 `open()`: re-decode folder from start and skip to member; no disk/RAM decoded-folder cache
- [x] 3.5 Reject BCJ2 / unknown IDs / unimplemented combinations (incl. LZMA1+BCJ if not validated) with `UnsupportedFeatureError`
- [x] 3.6 Wire password candidates for header + per-folder units; promote known-good; never wrong bytes
- [x] 3.7 Populate `ArchiveInfo` (solid, encrypted, comment, multivolume) and `CostReceipt` folder signals

## 4. Volumes + anti-items + extraction

- [x] 4.1 Join multi-volume sets (discovered siblings or explicit list) by concatenation; error on incomplete sets
- [x] 4.2 Expose anti members with `is_anti=True`; empty payload on open; compute `is_current` from the ANTI bitmask + same-name shadowing (superseded content → `is_current=False`)
- [x] 4.3 Extraction: skip `is_current=False` members by default (SKIPPED result; no limit counting); anti-items delete **only** a path this same extraction wrote (`lstat`/`unlink`, file/empty-dir only), never pre-existing/populated/out-of-root data; missing or not-written dest = success no-op

## 5. Tests, oracles, fuzz

- [ ] 5.1 Activate corpus 7z builders/sweep; native ↔ `py7zr` metadata+bytes cross-check (skip if absent)
- [ ] 5.2 Per-codec fixtures: STORED, LZMA2, LZMA2+BCJ, LZMA2+Delta, Deflate, BZip2, Zstd, Brotli, PPMd, Deflate64, AES, solid, multi-password, header-encrypted, multi-volume
- [ ] 5.3 LZMA1+BCJ fixture: either correct decode vs oracle or asserted `UnsupportedFeatureError` + short design note
- [ ] 5.4 BCJ2 / unknown method rejection tests
- [ ] 5.5 Anti-item fixtures via `7z` CLI; list + `is_current` computation; extract into a fresh dest and compare final tree vs `7z x` into a fresh dest (skip without `7z`); assert an anti-item leaves a pre-existing not-written destination untouched; do not require py7zr for these
- [ ] 5.6 Core-only / `[7z]` / `[crypto]` gating tests (`PackageNotInstalledError` paths)
- [ ] 5.7 Atheris (or env-gated harness) for header parser seeded from corpus + adversarial bytes
- [ ] 5.8 `openspec validate native-7z-reader --strict`; `ruff` / `pyrefly` / `ty`; three-config pytest gate
