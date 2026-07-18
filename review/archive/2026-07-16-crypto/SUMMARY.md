# Brief 2 — Native crypto correctness deep review — SUMMARY

**Scope:** every native decryption / key-derivation / integrity-verification path added
since #73 — WinZip AES (`internal/zip_aes.py`), 7z AES + KDF
(`internal/streams/crypto.py`), ZipCrypto (`internal/zipcrypto.py`), RAR3/RAR5 header &
data key derivation (`backends/rar_parser.py`), and native BLAKE2sp
(`internal/hashing/blake2sp.py`), plus the verification wiring
(`internal/streams/verify.py`) and the reader glue that gates member output in
`zip_reader.py` / `sevenzip_reader.py` / `rar_reader.py`.

**Baseline (green):** `uv run pytest` → **1540 passed, 120 skipped**, 4 warnings, 84%
coverage. `pyrefly` 0 errors, `ty` all-pass, `ruff` all-pass. Config: `[all]` (cryptography
49.0.0, py7zr 1.1.3, rarfile 4.3, pyppmd 1.3.1, rapidgzip 0.16.0, pybcj present; zstandard
absent; **no `unrar`/`7z` binary** in the container). Oracle diffs below were run with
py7zr and rarfile as importable libraries.

## Headline

The KDFs and ciphers themselves are **correct** — bit-exact against the project's own
oracles (7z KDF vs `py7zr`, RAR3/RAR5 s2k vs `rarfile`, ZipCrypto vs stdlib
`_ZipDecrypter`, BLAKE2sp vs official KATs). The findings are all in the **glue that decides
whether an integrity check runs at all**: one path raises a *dishonest error on good data*
(F1 — a real bug), one lets a hostile archive burn unbounded CPU (F3 — v2 is more permissive
than 7-Zip's own cap), and one *silently accepts a wrong password* where no checksum exists
(F2 — now source-confirmed to match 7-Zip's own best-effort behaviour, so a diagnostic gap,
not a bug). None is caught by the current suite because the RAR data path needs an `unrar`
binary (absent here) and the 7z/KDF triggers need format-legal-but-crafted archives that
`py7zr` never emits.

## Findings (most severe first)

| # | Sev | Where | One-liner | Repro | Status |
|---|-----|-------|-----------|-------|--------|
| F1 | **High** | `rar_reader.py` `_member_hashes` | RAR5 tweaked-checksum members keep the key-tweaked BLAKE2sp in `hashes`, so a correctly-decrypted `-htb`-encrypted member fails `VerifyingStream` with a spurious `CorruptionError` | unit + e2e (`encryption_blake2sp.rar`) | **Fixed in #127** |
| F2 | Low *(was Medium — see Q2)* | `sevenzip_reader.py` `_to_member` / `_verify_decoded_folder` | Encrypted 7z folder with **no folder digest and CRC-less members** confirms *any* password and returns garbage with no error | unit (`_verify_decoded_folder` + diagnostic emit) | **Fixed in #127** (diagnostic; best-effort kept) |
| F3 | **Medium** | `crypto.py` `derive_sevenzip_aes_key` / `parse_sevenzip_aes_properties` | 7z `NumCyclesPower` uncapped below `0x3F`; hostile `0x3E` → ~2⁶² SHA-256 rounds. 7-Zip rejects 25–62 | unit (parse + derive) | **Fixed in #127** |
| F4 | Low (hardening) | `rar_unrar.py` `open_unrar_p` | Password in `unrar` argv → visible via `ps`/`/proc`. Fix: bare `-p` + stdin | Popen spy + e2e | **Fixed in #127** |
| F5 | Low (hardening) | `rar_parser.py` `_check_rar5_password` | RAR5 PswCheck compared with `!=` rather than `hmac.compare_digest` | unit | **Fixed in #127** |

See `verification.md` (F1, F2), `kdf-and-ciphers.md` (F3, F5), and
`availability-and-contract.md` (F4). `QUESTIONS.md` records the resolved decisions;
`7z-source-questions.md` holds the source-agent checklist with answers. The "**what is
actually fine**" section is at the end of `verification.md`.

## Applied fixes (#127)

All five findings are implemented on `main` via #127 (rebased onto this review). Coverage
lives in `tests/test_crypto_findings.py` plus the vendored fixture
`tests/fixtures/rar/encryption_blake2sp.rar` (`-m0 -htb -ppassword`).

| # | What landed |
|---|-------------|
| **F1** | `_member_hashes` drops both crc32 and blake2sp when HASHMAC (`0x02`) is set; tweaked values stashed in `member.extra` (`rar.tweaked_crc32` / `rar.tweaked_blake2sp`). With a password, `VerifyingStream` verifies via `ConvertHashToMAC` (`digest_transforms` + `rar5_hash_key` / `convert_crc_to_mac` / `convert_blake2sp_to_mac`). Without a password, emit `DIGEST_UNVERIFIABLE` (`reason="tweaked_checksum"`). `member.hashes` stays empty when tweaked (plaintext digests are not recoverable). |
| **F2** | Best-effort accept unchanged. Encrypted CRC-less members in a folder with no digest emit `DIGEST_UNVERIFIABLE` (`reason="no_integrity_anchor"`) at list time. |
| **F3** | `parse_sevenzip_aes_properties` / `derive_sevenzip_aes_key` accept `cycles <= 24` or `== 0x3F`; reject 25–62 with `UnsupportedFeatureError`. Password-confirm no longer remaps that to `EncryptionError`. |
| **F4** | `open_unrar_p` passes bare `-p` and writes `password + "\n"` on the child's stdin (`-p-` when no password). |
| **F5** | `_check_rar5_password` uses `hmac.compare_digest` for both the check-blob SHA-256 prefix and the derived PswCheck. |

**Resolution notes (source-confirmed, 2026-07-16 — 7-Zip + UnRAR source):**
- **F1** — both `ConvertHashToMAC` transforms confirmed in UnRAR `crypt5.cpp`: CRC32 = XOR-fold
  of `HMAC-SHA256(HashKey, crc_le4)`, BLAKE2sp = `HMAC-SHA256(HashKey, digest32)`, HashKey =
  PBKDF2 at `(1<<kdf_count)+16`. Gate is the per-file `HASHMAC` (0x02) flag only.
- **F2** — 7zAES has **no** password check; 7-Zip returns garbage on a CRC-less store member
  too. Best-effort accept is confirmed correct; emit a diagnostic. **Low.**
- **F3** — 7-Zip clamps `NumCyclesPower` to **≤24 or ==0x3F** (`E_NOTIMPL` otherwise); `0x3F`
  no-hash sentinel is real. Fix matches the reference exactly.
- **F4** — bare `-p` + stdin is the non-argv password channel (UnRAR `GetPasswordText`).
- **F5** — timing-only; fixed for consistency with WinZip AES.

## Ranking against VISION

F1 and F2 both attack claim #3 ("damaged input → an honest error"): F1 turns *good* data
into a corruption error (dishonest error), F2 turns a *wrong password* into silently
returned garbage (no error at all — the worst outcome the brief names). F3 attacks claim #2
(parse untrusted archives safely): it is memory-safe but CPU-unbounded. F4/F5 are
threat-model hardening, not release blockers.
