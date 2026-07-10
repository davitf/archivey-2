"""Minimal writer for traditional (ZipCrypto / PKWARE) encrypted ZIP members.

The stdlib :mod:`zipfile` can *read* ZipCrypto but cannot *write* encryption, and the
`7z` CLI is not always present, so tests that need to exercise the ZipCrypto read path
(notably the multi-password disambiguation, where a wrong candidate password can pass
the cipher's single verification byte) build their archives here instead.

This deliberately implements only what those tests need: one STORED, DEFLATE, BZIP2, or
LZMA member encrypted with the classic PKWARE stream cipher in a single-entry ZIP. It is
test scaffolding, not a general-purpose ZIP writer.

The cipher (APPNOTE.txt §6.1): a 96-bit key state seeded from the password, a 12-byte
random encryption header whose final byte is a verification value (the high byte of the
CRC-32 when no data descriptor is used), then the file bytes, all run through the same
keystream. The verification byte is only *one* byte, so ~1/256 of wrong passwords pass
it — which is exactly the hazard :func:`find_check_byte_collision` reproduces.
"""

from __future__ import annotations

import bz2
import lzma
import struct
import zipfile
import zlib


def _make_crc_table() -> list[int]:
    table = []
    for i in range(256):
        c = i
        for _ in range(8):
            c = (c >> 1) ^ 0xEDB88320 if (c & 1) else (c >> 1)
        table.append(c)
    return table


_CRC_TABLE = _make_crc_table()


def _crc32_update(crc: int, byte: int) -> int:
    return ((crc >> 8) & 0xFFFFFF) ^ _CRC_TABLE[(crc ^ byte) & 0xFF]


class _Keys:
    """The 96-bit ZipCrypto key state."""

    def __init__(self, password: bytes) -> None:
        self.k0, self.k1, self.k2 = 0x12345678, 0x23456789, 0x34567890
        for b in password:
            self.update(b)

    def update(self, byte: int) -> None:
        self.k0 = _crc32_update(self.k0, byte) & 0xFFFFFFFF
        self.k1 = (self.k1 + (self.k0 & 0xFF)) & 0xFFFFFFFF
        self.k1 = (self.k1 * 134775813 + 1) & 0xFFFFFFFF
        self.k2 = _crc32_update(self.k2, (self.k1 >> 24) & 0xFF) & 0xFFFFFFFF

    def keystream_byte(self) -> int:
        temp = (self.k2 | 2) & 0xFFFF
        return ((temp * (temp ^ 1)) >> 8) & 0xFF


def _encrypt(password: bytes, check_byte: int, payload: bytes) -> bytes:
    keys = _Keys(password)
    out = bytearray()
    # 12-byte encryption header: 11 fixed bytes + the verification byte. Real writers
    # randomize the first 11; fixed here keeps fixtures byte-for-byte reproducible.
    header = bytes(range(11)) + bytes([check_byte])
    for plain in (*header, *payload):
        out.append(plain ^ keys.keystream_byte())
        keys.update(plain)
    return bytes(out)


def build_zipcrypto_zip(
    password: bytes,
    name: bytes,
    data: bytes,
    *,
    compression: int = zipfile.ZIP_DEFLATED,
) -> bytes:
    """A single-entry ZIP whose one member is ZipCrypto-encrypted with ``password``.

    Supports the four compression methods decoded by stdlib ``zipfile``. No data
    descriptor is used, so the verification byte is the high byte of the payload CRC-32.
    """
    crc = zlib.crc32(data) & 0xFFFFFFFF
    flags = 0x1  # bit 0: encrypted; no data descriptor
    if compression == zipfile.ZIP_STORED:
        stored = data
        version_needed = 20
    elif compression == zipfile.ZIP_DEFLATED:
        body = zlib.compressobj(9, zlib.DEFLATED, -15)
        stored = body.compress(data) + body.flush()
        version_needed = 20
    elif compression == zipfile.ZIP_BZIP2:
        stored = bz2.compress(data)
        version_needed = 46
    elif compression == zipfile.ZIP_LZMA:
        body = zipfile.LZMACompressor()
        stored = body.compress(data) + body.flush()
        version_needed = 63
        flags |= 0x2  # ZIP's LZMA end-of-stream marker flag
    else:
        raise ValueError(f"unsupported test compression method: {compression}")
    enc = _encrypt(password, (crc >> 24) & 0xFF, stored)
    comp_size = len(enc)  # encryption header is part of the stored/compressed size
    lfh = (
        struct.pack(
            "<IHHHHHIIIHH",
            0x04034B50,
            version_needed,
            flags,
            compression,
            0,
            0,
            crc,
            comp_size,
            len(data),
            len(name),
            0,
        )
        + name
    )
    data_start = len(lfh)
    cdh = (
        struct.pack(
            "<IHHHHHHIIIHHHHHII",
            0x02014B50,
            20,
            version_needed,
            flags,
            compression,
            0,
            0,
            crc,
            comp_size,
            len(data),
            len(name),
            0,
            0,
            0,
            0,
            0,
            0,
        )
        + name
    )
    cd_off = data_start + len(enc)
    eocd = struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, 1, 1, len(cdh), cd_off, 0)
    return lfh + enc + cdh + eocd


def corrupt_zipcrypto_payload(blob: bytes) -> bytes:
    """Flip encrypted member data after its intact 12-byte ZipCrypto header."""
    name_len, extra_len = struct.unpack_from("<HH", blob, 26)
    payload_start = 30 + name_len + extra_len + 12
    if payload_start >= len(blob):
        raise ValueError("ZIP member has no encrypted payload to corrupt")
    corrupt = bytearray(blob)
    corrupt[payload_start] ^= 0x80
    return bytes(corrupt)


def find_check_byte_collision(
    blob: bytes, name: str, right_password: bytes, *, search: int = 20000
) -> bytes:
    """A *wrong* password whose ZipCrypto verification byte matches ``blob``'s member.

    Such a password passes :meth:`zipfile.ZipFile.open` (the 1-byte check) but fails the
    CRC when the member is actually read — the false-accept the disambiguation guards
    against. Deterministic for a fixed ``blob`` (fixed search order). Raises if none is
    found within ``search`` attempts (astronomically unlikely: ~1/256 hit rate).
    """
    return find_check_byte_collisions(
        blob, name, right_password, count=1, search=search
    )[0]


def find_check_byte_collisions(
    blob: bytes,
    name: str,
    right_password: bytes,
    *,
    count: int,
    search: int = 20000,
) -> list[bytes]:
    """Return ``count`` distinct wrong passwords passing the one-byte open check."""
    import io

    collisions: list[bytes] = []
    for i in range(search):
        wrong = f"collide-{i}".encode()
        if wrong == right_password:
            continue
        with zipfile.ZipFile(io.BytesIO(blob)) as zf:
            info = zf.getinfo(name)
            try:
                handle = zf.open(info, pwd=wrong)  # only the 1-byte check runs here
            except RuntimeError:
                continue  # verification byte mismatched: correctly rejected
            try:
                with handle:
                    handle.read()
            except (zipfile.BadZipFile, zlib.error, lzma.LZMAError):
                # Passed the 1-byte check but failed the real check: a corrupt
                # decompressor stream (compressed member) or a CRC mismatch (stored).
                collisions.append(wrong)
            except OSError as exc:
                # bz2 uniquely reports invalid compressed bytes as this message-specific
                # OSError. Never hide an unrelated source/filesystem OSError in a test.
                if str(exc) != "Invalid data stream":
                    raise
                collisions.append(wrong)
            if len(collisions) == count:
                return collisions
    raise AssertionError(
        f"found only {len(collisions)} of {count} verification-byte collisions "
        f"in {search} attempts (unlucky)"
    )
