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
`_ZipDecrypter`, BLAKE2sp vs official KATs). The bugs are all in the **glue that decides
whether an integrity check runs at all**: one path raises a *dishonest error on good data*,
one path *silently accepts a wrong password*, and one path lets a hostile archive burn
unbounded CPU. None of these is caught by the current suite because the RAR data path needs
an `unrar` binary (absent here) and the 7z/KDF triggers need format-legal-but-crafted
archives that `py7zr` never emits.

## Findings (most severe first)

| # | Sev | Where | One-liner | Repro | Status |
|---|-----|-------|-----------|-------|--------|
| F1 | **High** | `rar_reader.py:119` `_member_hashes` | RAR5 tweaked-checksum members keep the key-tweaked BLAKE2sp in `hashes`, so a correctly-decrypted `-htb`-encrypted member fails `VerifyingStream` with a spurious `CorruptionError` | unit (VerifyingStream + tweaked hash) + `_member_hashes` asymmetry; rarfile oracle guard | Confirmed (logic); full e2e needs `unrar` + `-htb` encrypted fixture |
| F2 | Low *(was Medium — see Q2)* | `sevenzip_reader.py:119` `_verify_decoded_folder` | Encrypted 7z folder with **no folder digest and CRC-less members** (both format-legal) confirms *any* password and returns garbage with no error (no CRC gate, no `VerifyingStream`) | unit (`_verify_decoded_folder` accepts wrong-key garbage) | Confirmed (logic); maintainer: keep best-effort, emit a diagnostic rather than hard-error |
| F3 | **Medium** | `crypto.py:176` `derive_sevenzip_aes_key` | 7z `NumCyclesPower` is uncapped below the `0x3F` sentinel; a hostile value `0x3E` forces ~2⁶² SHA-256 rounds (CPU DoS) during password confirmation / header decode. RAR5 caps its shift at 24; 7z has no equivalent | `parse_sevenzip_aes_properties` + derive | Confirmed |
| F4 | Low (hardening) | `rar_unrar.py:53` `_password_arg` | Password passed to `unrar` as `-p<password>` in argv → visible to other local users via `ps`/`/proc` | code read | Confirmed; matches `rarfile` behaviour |
| F5 | Low (hardening) | `rar_parser.py:1406` `_check_rar5_password` | RAR5 password-check value (key-derived) compared with `!=` rather than `hmac.compare_digest` | code read | Confirmed; WinZip AES correctly uses `compare_digest` |

See `verification.md` (F1, F2, the silent-acceptance analysis — the headline),
`kdf-and-ciphers.md` (F3, F5, and the bit-exactness oracle diffs), and
`availability-and-contract.md` (F4, `[crypto]`-absent contract, `[all-lowest]` API floor).
`QUESTIONS.md` records the resolved maintainer decisions (PR #115 review +
`archivey-dev` reference check) and `7z-source-questions.md` is the checklist for the
7-Zip/UnRAR source agent. The "**what is actually fine**" section is at the end of
`verification.md`.

**Resolution notes (post-review, PR #115):** F1 — fix per DEV's `ConvertHashToMAC`: the tweak
is a *one-way* forward transform, so untweak-and-verify (compute → transform → compare) rather
than "recover the real checksum"; interim, make `_member_hashes` symmetric (drop tweaked
blake2sp like it already drops crc32). F2 — keep best-effort (matches 7-Zip; ZIP always has an
HMAC/CRC anchor so is not exposed), emit a diagnostic instead of hard-erroring; downgraded to
Low. F3 — v2 should cap `NumCyclesPower` (recommend 24, mirroring the RAR5 cap). F4/F5 — see
below.

## Ranking against VISION

F1 and F2 both attack claim #3 ("damaged input → an honest error"): F1 turns *good* data
into a corruption error (dishonest error), F2 turns a *wrong password* into silently
returned garbage (no error at all — the worst outcome the brief names). F3 attacks claim #2
(parse untrusted archives safely): it is memory-safe but CPU-unbounded. F4/F5 are
threat-model hardening, not release blockers.
