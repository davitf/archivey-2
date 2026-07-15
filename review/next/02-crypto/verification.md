# Silent-acceptance & verification analysis (Brief §A)

This is the headline surface: for every native path, does authentication actually run and
gate output, or can wrong/tampered data reach the caller without an honest error?

---

## F1 (High) — RAR5 tweaked-checksum BLAKE2sp raises a spurious `CorruptionError` on *correct* data

### What the code does

`rar_reader.py:119`:

```python
def _crc_is_tweaked(info: RarMemberInfo) -> bool:
    enc = info.file_encryption
    if enc is None:
        return False
    return bool(enc.flags & _RAR_ENCDATA_FLAG_TWEAKED_CHECKSUMS)  # 0x02

def _member_hashes(info: RarMemberInfo) -> dict[str, int | bytes]:
    hashes: dict[str, int | bytes] = {}
    if info.crc32 is not None and not _crc_is_tweaked(info):   # crc32 guarded
        hashes["crc32"] = info.crc32
    if info.blake2sp_hash is not None:                          # blake2sp NOT guarded
        hashes["blake2sp"] = info.blake2sp_hash
    return hashes
```

When a RAR5 member is encrypted with the "tweaked checksums" option
(`RAR5_XENC_TWEAKED = 0x02`, set by WinRAR whenever file *data* is encrypted), WinRAR stores
`checksum XOR key_derived_tweak` rather than the raw checksum, so identical plaintext under
different passwords produces different stored checksums. The correct handling is to **not**
verify the stored value against the plaintext hash unless you reproduce the inverse tweak.

`_member_hashes` does this correctly for **crc32** (drops it when tweaked) but **not for
blake2sp** — the BLAKE2sp hash is surfaced unconditionally. It then flows to
`VerifyingStream` in `rar_reader.py:509` (`_wrap_payload_stream`), which recomputes BLAKE2sp
over the plaintext `unrar` emits and compares it to the *tweaked* stored value → guaranteed
mismatch → `CorruptionError` on a member that decrypted perfectly.

### Oracle: rarfile does the opposite

`rarfile 4.3` only arms BLAKE2sp verification when the member is **not** tweaked:

```python
# rarfile.py, RAR5 file-hash parse
if (h.file_encryption[1] & RAR5_XENC_TWEAKED) == 0:
    h._md_class = Blake2SP
    h._md_expect = h.blake2sp_hash
```

i.e. rarfile treats a tweaked BLAKE2sp as unverifiable and skips it. archivey should do the
same (it already does for crc32).

### Why this bites `-htb` archives specifically

RAR5 stores *either* CRC32 *or* BLAKE2sp per file, not both. WinRAR uses BLAKE2sp only with
`-htb`. So for a `-htb`-encrypted member: `crc32 is None` (dropped anyway) and
`blake2sp_hash` is present-but-tweaked → `hashes == {"blake2sp": <tweaked>}` → false
corruption. There is no crc32 fallback. The existing `encryption__.rar` fixture happens to
use CRC32 (not `-htb`), so it stores `crc32` + tweaked flag and dodges the bug — which is
exactly why the suite is green.

### Reproductions

**(a) reader-level asymmetry** — a tweaked member keeps blake2sp, drops crc32:

```
tweaked? True
surfaced hashes keys: ['blake2sp']
crc32 present: False   blake2sp present: True
```
(constructed a `RarMemberInfo` with `RarEncryptionInfo(flags=0x02)`, `crc32=…`,
`blake2sp_hash=…`; called `_member_hashes`.)

**(b) mechanism end-to-end at the stream level** — `VerifyingStream` raises on *correct*
plaintext when the stored blake2sp is tweaked:

```python
plaintext = b"stored payload"
real = blake2sp(plaintext)
tweaked_stored = bytes(b ^ 0x5A for b in real)          # WinRAR stores real XOR tweak
vs = VerifyingStream(io.BytesIO(plaintext), {"blake2sp": tweaked_stored})
vs.read(); vs.read()          # -> CorruptionError: Digest mismatch for 'blake2sp'
# control: {"blake2sp": real} verifies cleanly
```

A full archive→`unrar`→VerifyingStream repro needs the `unrar` binary (absent here) and a
`-htb`-encrypted RAR5 fixture; the two units above pin the exact defect and the oracle pins
the intended behaviour.

### Fix

Guard blake2sp with the same predicate: `if info.blake2sp_hash is not None and not
_crc_is_tweaked(info):`. (Rename `_crc_is_tweaked` → `_checksums_tweaked` since it now covers
both.) Optionally add a `DIGEST_UNVERIFIABLE` diagnostic so a tweaked member reports "not
verified" rather than silently unverified — matching how `VerifyingStream` already emits that
code for unknown algorithms.

---

## F2 (Medium) — 7z confirms *any* password when the folder carries no integrity anchor

### What the code does

`sevenzip_reader.py:119`:

```python
def _verify_decoded_folder(folder, decoded, *, member_digests=None):
    if folder.digest_defined:
        expected = (folder.crc if folder.crc is not None else 0) & 0xFFFFFFFF
        if zlib.crc32(decoded) & 0xFFFFFFFF != expected:
            raise EncryptionError("Wrong password or corrupt 7z folder")
        return
    if not member_digests:
        return                      # <-- no folder digest AND no member digests: accept
    offset = 0
    for size, raw_expected in member_digests:
        chunk = decoded[offset:offset+size]; offset += size
        if raw_expected is None:
            continue                # <-- CRC-less member: skipped
        ...
```

This is the wrong-password oracle for 7z: `_password_for_folder` (`sevenzip_reader.py:505`)
decodes the whole folder with a candidate key and calls `_verify_decoded_folder`. If the
folder has **no folder-level digest** *and* **every member is CRC-less** (`raw_expected is
None`), the function returns without raising, so `confirm()` returns the candidate as the
accepted password. The member read then goes through `_wrap_folder_member`
(`sevenzip_reader.py:544`), which only wraps `VerifyingStream` `if member.hashes:` — empty
here — so there is **no second gate either**. Result: a wrong password yields garbage
plaintext returned with no error.

### Why it is reachable but narrow

The normal 7z coder chain is AES→LZMA2, and a wrong AES-CBC key feeds garbage to the LZMA2
decoder, which raises `LZMAError` (→ translated). So a *compressed* encrypted folder is
gated by the codec even without CRC. The gap is a folder whose chain is **AES + COPY**
(stored, no compressing codec) — `plan_folder` accepts it (COPY is skipped, AES stage runs;
`sevenzip_pipeline.py:122`) — combined with **no folder digest and CRC-less members**. All of
`digest_defined = False`, absent member CRCs, and AES-only coders are legal in the 7z
container; `py7zr` never emits them (it always writes CRCs and defaults to LZMA2), so this is
a *hostile/crafted-archive* finding, squarely in the brief's threat model ("an archive with
CRC absent doesn't silently accept any password").

### Reproduction

```python
folder = SevenZipFolder(coders=[], bind_pairs=[], packed_indices=[0],
                        unpack_sizes=[16], crc=None, digest_defined=False)
garbage = b"\x00"*16                      # AES(wrong key) over a COPY folder
_verify_decoded_folder(folder, garbage, member_digests=[(16, None)])
# -> returns, no exception  == wrong password accepted, garbage handed back
# control: digest_defined=True, crc=0x12345678 -> EncryptionError (correctly rejected)
```

### Fix

Fail closed: when an *encrypted* folder has neither a folder digest nor any member CRC,
`_password_for_folder` should refuse to confirm a password (raise a typed
`EncryptionError`/`UnsupportedFeatureError` — "cannot verify password: this encrypted 7z
folder has no checksum") rather than accept the first candidate. This preserves VISION #3
(honest error) at the cost of refusing a pathological archive that no real tool produces.
See QUESTIONS Q2 — this trades against any "recover what you can" goal.

---

## What is actually fine (verified good — do not "fix")

- **WinZip AES HMAC genuinely gates output.** `WinZipAesDecryptStream.close()`
  (`zip_aes.py:194`) drains the remaining ciphertext + MAC and verifies, so even a caller
  that does a single `read(member.size)` (which returns all plaintext without pulling the
  MAC in `read`) still gets a `CorruptionError` on the `with`-block close. Verified with a
  *correctly* constructed tamper (valid MAC over the original ciphertext, then one ciphertext
  byte flipped) for both AE-2 STORED and AE-2 deflate — both raised
  `CorruptionError: WinZip AES HMAC mismatch`. (An earlier "silent acceptance" reading was a
  flawed test that recomputed the MAC over the already-tampered ciphertext.) Bytes are
  delivered before the terminal raise, which is the same documented AE/`VerifyingStream`
  streaming trade-off, not a new bug. Both the 2-byte pw-verify and the HMAC use
  `hmac.compare_digest`.
- **WinZip AES members are non-seekable.** `WinZipAesDecryptStream` extends
  `ReadOnlyIOStream` (not `DelegatingStream`), so `seekable()` is `False` and `seek` raises
  `io.UnsupportedOperation`; the STORED-over-AES member likewise reports `seekable() == False`
  and refuses `seek(500)` / `seek(0)` cleanly. The hand-rolled sequential `_AesCtrLe` counter
  is therefore never asked to reposition, so there is no wrong-plaintext-after-seek.
- **`_AesCtrLe` counter is correct across the 32/128-bit boundary** — it is a Python
  arbitrary-precision `int` serialized with `to_bytes(16, "little")` and `+= 1`, so no 32-bit
  wrap bug is possible (and 2³² blocks = 64 GiB is unreachable anyway).
- **BLAKE2sp is bit-exact** — `test_blake2sp.py` already checks official KATs at lengths
  0/1/63/64/65/127/128/255, i.e. across the 64-byte block boundary and the 512-byte
  8-lane-stride boundary (the classic blake2sp bug locations), plus an odd-7-byte incremental
  feed. All pass on the baseline.
- **7z folder CRC gates wrong passwords on the normal path** (`digest_defined = True`,
  `sevenzip_reader.py:127`) — a wrong key over a compressed folder is rejected by both the
  codec and the CRC.
