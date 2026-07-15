# KDF & cipher correctness (Brief §B) + const-time (§E)

## Oracle diffs — everything bit-exact

Every KDF and cipher was diffed against the project's stated oracle. All passed with **0
mismatches**.

### 7z SHA-256 cycle KDF — `derive_sevenzip_aes_key` vs `py7zr.helpers.calculate_key`

archivey's function is line-for-line `py7zr._calculate_key3` (same `cat_cycle = 6` batching,
same `salt+password`, same 8-byte little-endian counter, same `0x3F` "no-hash" sentinel).
Diff over the cartesian product of `cycles ∈ {0,1,5,6,7,8,12,19,0x3F}`,
`salt ∈ {b"", b"\x00", 16-random}`, `password ∈ {b"", "pw"·utf16le, "münchen"·utf16le}`:

```
KDF done, mismatches: 0
```

Covered explicitly: the `cat_cycle` batching boundary (cycles 6 vs 7), the empty-salt case,
and the `cycles == 0x3F` sentinel (returns `(salt+password+zeros)[:32]`, matching py7zr).

### `parse_sevenzip_aes_properties`

Salt/IV flag decode and the 2 + salt + iv length check are correct: `bytes([0xC0, 0x00])`
(both flags set, sizes 0) correctly raises `ValueError: length 2 != expected 4`; a
well-formed `[0x80|0x40|cycles, 0x00] + salt + iv` parses and zero-pads the IV to 16.

### RAR3 / RAR5 s2k vs `rarfile`

`_rar3_s2k` vs `rarfile.rar3_s2k` and `_rar5_s2k` vs `rarfile.rar5_s2k`, over
`password ∈ {"pw","münchen","","correct horse"}`, random + zero salts, and RAR5
`kdf_count ∈ {2¹⁵, 2¹⁵+32}`:

```
RAR s2k mismatches: 0
```

This confirms the **password-normalization split** the brief flagged as high-risk:
`_normalize_password_utf16le` (RAR3: UTF-16LE, truncate to `127*2` bytes, **no** re-encode)
vs `_normalize_password_utf8` (RAR5: UTF-16LE-truncate → decode → **UTF-8**) both match
rarfile exactly, including the `münchen` non-ASCII case. A swap here would silently fail
valid passwords; it is correct. The `_Rar3Sha1(rarbug=True)` deliberate-corruption path is
exercised transitively by the RAR3 diff (it feeds the s2k) and matches.

### WinZip AES PBKDF2 — `derive_winzip_aes_keys`

PBKDF2-HMAC-SHA1, 1000 iterations, `dklen = key_len*2 + 2`, split `enc ‖ auth ‖ verify(2)`.
Per-strength geometry is correct: `salt_len = key_bits//16` → 8/12/16 bytes for
AE-128/192/256, `key_len = key_bits//8` → 16/24/32, 2-byte pw-verify. Round-trips through the
existing `test_zip_aes.py` builders and the manual AE-1/AE-2 STORED+deflate archives built
during this review.

### ZipCrypto vs stdlib `zipfile._ZipDecrypter`

Keystream/decrypt identical over 64 random bytes; `parallel_plaintext_crc32` produces the
correct plaintext CRC for the right password and `password_matches_check_byte` matches the
1-byte header check:

```
ZipCrypto decrypt matches stdlib: True
parallel crc for correct pw: 0x194bc2c  expected 0x194bc2c  match: True
check_byte match: True
```

The `_crc32_update`/`_make_crc32_table` reflected-polynomial `0xEDB88320` implementation is
correct (it has to be, or the stdlib diff would fail).

---

## F3 (Medium) — 7z `NumCyclesPower` is uncapped → CPU DoS from a hostile archive

`derive_sevenzip_aes_key` (`crypto.py:184`) validates only `0 <= cycles <= 0x3F` and treats
`0x3F` as the no-hash sentinel. Every value `0x01..0x3E` is taken literally as `2^cycles`
SHA-256 rounds. There is **no upper bound below the sentinel**, so a crafted AES coder
property byte with `cycles = 0x3E` forces ~2⁶² hash rounds:

```python
first  = 0x80 | 0x40 | 0x3E      # salt+iv flags, cycles = 62
props  = bytes([first, 0x00]) + b"\xAA" + b"\xBB"
cycles, salt, iv = parse_sevenzip_aes_properties(props)   # -> cycles = 62
# derive_sevenzip_aes_key(...) would attempt 2**62 SHA-256 rounds before returning
```

This runs during **password confirmation** (`_password_for_folder` → `confirm`) and during
**encrypted-header decode** (`decode_encoded_header`), i.e. on attacker-controlled input as
soon as a password is supplied — a memory-safe but unbounded-CPU hostile input that hangs the
calling thread. It undercuts VISION #2 (parse untrusted archives safely). Real archives use
`NumCyclesPower = 19` (2¹⁹, ~sub-millisecond); 7-Zip's own UI caps the slider well below 24.

**Contrast:** RAR5 got this right — `_rar5_decrypt_header` and `_check_rar5_password` both
enforce `_RAR_MAX_KDF_SHIFT = 24` (`rar_parser.py:49,1382,1395`). 7z has no equivalent.

**Fix:** cap `cycles` in `derive_sevenzip_aes_key` (or in `parse_sevenzip_aes_properties`) at
a sane maximum — 24 mirrors RAR5 and is far above any real archive — raising
`UnsupportedFeatureError`/`CorruptionError` above it. `py7zr` shares the missing cap, so this
is not an oracle divergence; it is a hardening gap the brief's threat model asks for. See
QUESTIONS Q3 (also: is py7zr's `0x3F` "no-hash" shortcut actually spec-correct vs the 7-Zip
C++ reference, which may intend `0x3F` as `2^63` rather than a sentinel? — a crafted-only
concern, since no creator emits `0x3F`).

---

## F5 (Low, hardening) — RAR5 password-check compared with `!=`

`_check_rar5_password` (`rar_parser.py:1406`) compares the key-derived 8-byte password-check
value with a plain `!=`:

```python
if bytes(pwd_check) != hdr_check:
    raise EncryptionError("Wrong password for RAR5 header encryption")
```

`pwd_check` is derived from the PBKDF2 output, so it is a secret-derived comparison; a
constant-time `hmac.compare_digest` would be the hardened form. Per the brief this is a
**low-severity note for a local archive library**, not a blocker — flagged only because it is
a secret-derived compare with an `==`/`!=` where a drop-in `compare_digest` exists (the
`sha256(hdr_check)[:4] != hdr_sum` check on the same line is over a non-secret and does not
matter). WinZip AES already uses `hmac.compare_digest` for both its pw-verify and its HMAC;
7z uses CRC (non-secret) so `!=` is fine there.
