## 1. Native parser

- [x] 1.1 Add `rar_parser.py` with `RarMemberInfo` (sizes, method, CRC/Blake2sp, timestamps, mode, solid/encrypt flags, `file_redir`, header/data offsets, volume index)
- [x] 1.2 Implement RAR5 parser (vint, block walk, extras: encryption/hash/time/redir/owner, main solid/volume flags, `RAR5_BLOCK_ENCRYPTION`)
- [x] 1.3 Implement RAR4/RAR3 parser (block headers, Unicode filename decode, solid/password/volume flags, Unix symlink mode `0xA000`)
- [x] 1.4 SFX prefix scan (≤2 MiB for `RAR_ID` / `RAR5_ID`); reject extract version ≤ 20 with `UnsupportedFeatureError`
- [x] 1.5 Header decryption (RAR5 PBKDF2 + AES; RAR3 key derivation) behind `[rar]`/`[crypto]`; no password → `EncryptionError`

## 2. unrar helper + reader backend

- [x] 2.1 Add RARLAB `unrar` locator/identifier; missing or non-RARLAB → `PackageNotInstalledError`; no tool fallbacks
- [x] 2.2 Implement `RarReader` / `RarReadBackend`: `_MEMBER_LIST_UPFRONT`, `_SUPPORTS_RANDOM_ACCESS`, `SUPPORTS_PASSWORD`, seek required; register format
- [x] 2.3 `_iter_members` → `ArchiveMember` mapping; resolve `link_target` when possible (RAR5 `file_redir`; RAR4 stored direct); hardlink/`FILE_COPY` → `HARDLINK`
- [x] 2.4 Solid `_iter_with_data`: one unnamed `unrar p -inul`; `SolidBlockReader` over payload FILE sizes only; verify CRC32/Blake2sp via shared stage
- [x] 2.5 `_open_member`: stored M0 direct read when possible; else single-name `unrar p … <member>`; solid random MAY use declared `unrar x` tempdir cleaned on close
- [x] 2.6 Nonsolid sequential: default lazy `_iter_with_data` (per-member named open); never ALL-pipe demux on mixed-password archives
- [x] 2.7 `ArchiveInfo` / `CostReceipt`: `is_solid`, `solid_block_count=None`, encryption, version, comment; fix stale multi-volume “Phase 7” stub in `core.py`

## 3. Volumes + packaging docs

- [ ] 3.1 Multi-volume join via existing discovery; parse across volumes; point `unrar` at volume 1 for path sources
- [ ] 3.2 Stream/in-memory volume sets: materialize ordered volumes for `unrar`; clean up on close; incomplete/out-of-order → typed error
- [ ] 3.3 Close threat-model C1 as won’t-do (RARLAB-only); note extract-hack deferred in ADR/design comments as needed

## 4. Tests, oracles, fuzz

- [ ] 4.1 Activate corpus RAR builders/sweep; native ↔ `rarfile`/`unrar` metadata+bytes cross-check (skip if absent)
- [ ] 4.2 Solid+symlink and solid+hardlink demux tests (pipe alignment + resolved `link_target`)
- [ ] 4.3 Header-encrypted RAR5, Blake2sp-only, stored M0, multi-volume, RAR2 rejection, non-RARLAB `unrar` rejection
- [ ] 4.4 Atheris (or env-gated) harness for RAR header parser seeded from corpus + adversarial bytes
- [ ] 4.5 Core-only / `[rar]` / `[crypto]` gating tests

## 5. Verify

- [ ] 5.1 Targeted pytest for RAR reader + solid demux + volumes
- [ ] 5.2 `openspec validate --strict native-rar-reader`
- [ ] 5.3 `ruff` / `pyrefly` / `ty`; three-config pytest gate per `CONTRIBUTING.md`
