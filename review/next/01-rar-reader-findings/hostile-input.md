# Hostile-input findings (parser)

All line numbers are against `src/archivey/internal/backends/rar_parser.py` at
HEAD `e74d227`. Reproducers in `repro.py`.

---

## F2 (Med-High) — RAR5 header-size vint pre-read is uncapped and O(n²)

`_read_rar5_block` reads the fixed 4-byte CRC + 1 byte, then grows a byte string
one byte at a time to cover the header-length vint:

```python
# rar_parser.py:1333-1344
header_offset = fd.tell()
preload = 4 + 1
start_bytes = fd.read(preload)
...
while start_bytes[-1] & 0x80:
    b = fd.read(1)
    if not b:
        raise CorruptionError("Unexpected EOF while reading RAR5 header size")
    start_bytes += b            # <-- O(n) copy each iteration => O(n^2) total
header_crc, pos = _load_le32(start_bytes, 0)
hdrlen, pos = _load_vint(start_bytes, pos)   # only NOW is 11-byte cap applied
if hdrlen > _RAR5_MAX_HEADER: ...            # and only NOW is the 2 MiB cap applied
```

Two problems:

1. **No length cap on the loop.** A RAR5 header-size vint is at most a few bytes
   (`_RAR5_MAX_HEADER = 2 MiB` fits in 3 continuation bytes). The loop instead reads
   continuation bytes until a byte < `0x80` or EOF — i.e. bounded only by the input
   length. The 11-byte cap in `_load_vint` and the 2 MiB cap on `hdrlen` both run
   *after* the loop has already consumed the whole run, so neither protects it.
2. **Quadratic growth.** `start_bytes += b` rebuilds the buffer every iteration.

**Crafted input:** `RAR5_ID + b"\x00\x00\x00\x00" + b"\x80" * N` (magic at offset 0,
so the SFX scan is skipped and control goes straight into the block loop).

**Measured (repro.py F2):** `n=20000 → 0.005s`, `n=40000 → 0.021s`,
`n=80000 → 0.087s` — ~4× per input doubling (quadratic). Extrapolating, a ~2 MB
all-`0x80` file is on the order of a minute of single-threaded CPU; 8 MB is ~15
minutes. Reproduces in **all three dependency configs** (pure parser, no optional
libs). The Atheris `rar_header` target would not surface it: its seeds are small
fixtures and bit-flip/append mutations, so a multi-kilobyte run of `0x80` is not
something the mutator produces.

**Why it matters (VISION #2).** "Parse untrusted archives with memory-safe,
bounded hostile-input parsing" — an uncapped superlinear loop on attacker bytes is
the canonical bounded-parsing violation, and it is the *only* place the parser
reads a length prefix without the `_load_vint` 11-byte guard in front of it.

**Suggested fix.** Cap the loop (e.g. stop after ≤3–4 continuation bytes → the max a
`_RAR5_MAX_HEADER`-bounded vint can occupy, then `CorruptionError`), and/or read into
a small `bytearray` instead of `+=` on `bytes`. Either removes both the quadratic
factor and the unbounded read.

---

## F1 (High) — wrong header password surfaces as `CorruptionError`, breaking candidate iteration

*(Cross-references Brief 2 for the crypto correctness of the KDF/decrypt; this is
the structural/contract half — what happens on a wrong/absent password.)*

When a header is encrypted and the supplied password is wrong, RAR3 (and RAR5
without a check value) has no way to *verify* the key, so decryption proceeds on
garbage and the failure shows up as a **structural** error inside the block walk —
not as an `EncryptionError`:

- RAR3: the decrypted "header size" is garbage. Either `_HeaderDecryptStream.read`
  rejects an >8 KiB read (`rar_parser.py:598-601`, `"Encrypted RAR header read too
  large — wrong password?"` — a `CorruptionError`), or the block-header CRC check at
  `rar_parser.py:877-883` fires (`CorruptionError`).
- RAR5 **with** a check value: `_check_rar5_password` (`1390-1407`) raises
  `EncryptionError` up front — correct. **Without** a check value (`enc_flags &
  HAS_CHECKVAL == 0`), that check is skipped (`1249`: `if check_value is not None and
  password is not None`), and the wrong key is caught later by the block CRC in
  `_read_rar5_block` (`1355`). Crucially, `_read_rar5_block` is called at
  `_parse_rar5:1197` **outside** the `try/except Exception → EncryptionError` that
  wraps only `_rar5_decrypt_header` (`1188-1195`), so that `CorruptionError`
  propagates unmapped.

**Downstream contract break.** `RarReader._parse_archive` first parses with
`password=None` (→ `EncryptionError`, good), then retries candidates via
`self._passwords.attempt(None, confirm)` (`rar_reader.py:321-330`).
`_PasswordCandidates.attempt` only treats `EncryptionError` as "wrong password, try
next" (`password.py:178`); any other exception propagates straight out. So a wrong
RAR3 candidate raises `CorruptionError`, which is **not** `_PasswordCandidatesExhausted`
and **not** caught at `rar_reader.py:331` — it aborts the whole open and the *next*
candidate is never tried.

**Repro (repro.py F1, confirmed, no `unrar` needed):**

```
[F1] RAR3 header-encrypted: wrong password -> CorruptionError ("...read too large — wrong password?")
[F1] Reader candidate list [wrong, correct] -> CorruptionError  (correct pw never tried)
```

Contrast: the RAR5 fixture (has a check value) correctly returns `EncryptionError`
and would iterate candidates.

**Why it matters.** Two VISION/contract violations at once: (a) the documented error
contract (`EncryptionError` distinguished from `CorruptionError` on a wrong-password
header — brief item C) is inverted for the RAR3 path, and (b) a user who passes a
list of candidate passwords, or a `PasswordProvider`, gets a hard `CorruptionError`
abort the moment the first candidate is wrong, instead of "try the rest / prompt
again". This is the commonest real multi-password workflow.

**Suggested fix.** Treat a decrypt-then-structural-failure on an encrypted header as
`EncryptionError`: either widen the `_parse_rar5` guard to include `_read_rar5_block`
when `hdr_enc is not None`, and wrap the RAR3 post-decrypt block read/CRC the same
way, or have `_parse_archive` map "structural failure while `has_header_encryption`
and a password was supplied" to `EncryptionError`. See `QUESTIONS.md` Q1 for the
contract decision (a wrong password and genuine corruption are genuinely
indistinguishable here, so which one wins is a maintainer call).

---

## F5 (Low-Med) — RAR3 `FILE_LARGE` >4 GiB member under-seeks the packed data

For RAR3, the packed-data skip uses `add_size`, read as a single little-endian
32-bit value from the LONG_BLOCK field:

```python
# rar_parser.py:851-854
if flags & _RAR3_LONG_BLOCK:
    add_size, pos = _load_le32(hdata, pos)   # low 32 bits only
else:
    add_size = 0
...
_seek_after_packed(source, data_offset, add_size)   # rar_parser.py:945
```

When `FILE_LARGE` is set, `_parse_rar3_file_header` reads the high dwords and extends
the *reported* sizes:

```python
# rar_parser.py:1009-1013
if flags & _RAR3_FILE_LARGE:
    h1, pos = _load_le32(hdata, pos)
    h2, pos = _load_le32(hdata, pos)
    compress_size |= h1 << 32
    file_size |= h2 << 32
```

…but the outer `add_size` used for the physical skip is **never** extended by `h1`.
So for a member whose packed size ≥ 4 GiB, the walk seeks only `packed_size mod 2^32`
bytes forward, lands in the middle of the packed data, and every subsequent block
header fails its CRC — the listing effectively truncates at (or corrupts past) the
first >4 GiB member. `member.compress_size` is reported correctly, but the archive
walk that depends on the skip is wrong.

Code-traced only — a behavioural repro needs a >4 GiB packed member, which is not a
practical fixture. Marked PLAUSIBLE. RAR3 large-file archives are uncommon but valid.

**Suggested fix.** Extend the skip size with the high dword for FILE blocks when
`FILE_LARGE` is set (compute the true 64-bit packed size and pass that to
`_seek_after_packed`), mirroring what `_parse_rar3_file_header` already does for the
reported size.

---

## F6 (Low) — `_merge_split_member` folds a continuation into the previous member with no identity check

`_merge_split_member` accumulates `compress_size`, overwrites CRC/BLAKE2sp, and marks
`spanned_volumes`, keyed purely on the `split_before` flag of the incoming member and
"there is a previous member":

```python
# rar_parser.py:564-573
def _merge_split_member(old, new):
    old.compress_size += new.compress_size
    if new.crc32 is not None: old.crc32 = new.crc32
    ...
    old.spanned_volumes = True
```

Called from three places, all flag-driven with no check that `old` and `new` are the
same logical file (same name / same split lineage):

- `parse_rar_volumes` (`291-295`) — cross-volume merge into `merged.members[-1]`.
- RAR3 in-volume (`918-923`) and RAR5 in-volume (`1285-1289`).

A crafted archive whose first FILE is complete (`split_after=False`) followed by a
second FILE carrying `split_before=True` will silently fold the second file's size
and CRC into the first and drop it as a distinct member. Within a single volume this
is malformed input; across volumes a mismatched continuation flag mis-joins two
unrelated members. `rarfile` collapses splits similarly, so this is a fidelity/robustness
nit, not a memory-safety issue — but the merge is worth gating on a name (or
split-lineage) match so a bad flag surfaces as a `CorruptionError` rather than a
silently wrong member list.
