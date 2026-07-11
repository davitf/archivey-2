"""Traditional ZipCrypto (PKWARE) stream cipher — keystream only.

Used by multi-candidate password confirmation for STORED members (parallel CRC
over raw ciphertext). Deliberately independent of :mod:`zipfile`'s private
``_ZipDecrypter`` so the same helper can back other callers later.

APPNOTE.txt §6.1: a 96-bit key state seeded from the password, a 12-byte
encryption header whose final plaintext byte is the verification value, then
payload bytes, each XORed with a keystream byte derived from the evolving state.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import BinaryIO


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


class ZipCryptoKeys:
    """The 96-bit ZipCrypto key state."""

    __slots__ = ("k0", "k1", "k2")

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


def password_matches_check_byte(
    password: bytes, header_ciphertext: bytes, check_byte: int
) -> bool:
    """Whether ``password`` satisfies ZipCrypto's 1-byte header check.

    ``header_ciphertext`` must be the first 12 encrypted bytes of the member.
    """
    if len(header_ciphertext) < 12:
        return False
    keys = ZipCryptoKeys(password)
    plain_last = 0
    for i in range(12):
        plain = header_ciphertext[i] ^ keys.keystream_byte()
        keys.update(plain)
        plain_last = plain
    return plain_last == check_byte


def parallel_plaintext_crc32(
    passwords: Sequence[bytes],
    header_ciphertext: bytes,
    body: BinaryIO,
    *,
    chunk_size: int = 64 * 1024,
) -> list[tuple[bytes, int]]:
    """Decrypt ``body`` with each password in parallel; return ``(password, crc32)``.

    ``body`` is the ZipCrypto ciphertext *after* the 12-byte encryption header (e.g. a
    :class:`~archivey.internal.streams.streamtools.slice.SlicingStream` over that range).
    Constant memory beyond the current chunk: one ZipCrypto state and running CRC per
    candidate. Candidate order is preserved (ties resolved by the caller via first match).
    """
    import zlib

    states: list[ZipCryptoKeys] = []
    for password in passwords:
        keys = ZipCryptoKeys(password)
        for i in range(12):
            plain = header_ciphertext[i] ^ keys.keystream_byte()
            keys.update(plain)
        states.append(keys)
    crcs = [0] * len(states)
    while True:
        chunk = body.read(chunk_size)
        if not chunk:
            break
        for i, keys in enumerate(states):
            plain = bytearray(len(chunk))
            for j, cipher in enumerate(chunk):
                p = cipher ^ keys.keystream_byte()
                keys.update(p)
                plain[j] = p
            crcs[i] = zlib.crc32(plain, crcs[i])
    return [
        (password, crc & 0xFFFFFFFF)
        for password, crc in zip(passwords, crcs, strict=True)
    ]
