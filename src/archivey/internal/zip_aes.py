"""WinZip AES (AE-1 / AE-2) decryption for ZIP members.

Wire format (APPNOTE / WinZip AE-x)::

    [salt][pw_verify(2)][AES-CTR ciphertext][HMAC-SHA1(10)]

The member's ``compress_type`` is 99; extra field ``0x9901`` carries vendor version
(AE-1=1 / AE-2=2), strength (1/2/3 → 128/192/256), and the actual compression method.
Key material is PBKDF2-HMAC-SHA1(password, salt, 1000) → enc_key ‖ auth_key ‖ pw_verify.
CTR uses a little-endian counter starting at 1 (no nonce). HMAC covers ciphertext only.
"""

from __future__ import annotations

import hashlib
import hmac
import struct
from dataclasses import dataclass
from typing import BinaryIO

from archivey.exceptions import (
    CorruptionError,
    EncryptionError,
    PackageNotInstalledError,
)
from archivey.internal.streams.crypto import CRYPTO_PACKAGE, _crypto_available
from archivey.internal.streams.streamtools import ReadOnlyIOStream, read_exact

# WinZip AES extra-field header id.
_AES_EXTRA_ID = 0x9901
_HMAC_LEN = 10
_PBKDF2_ITERS = 1000


@dataclass(frozen=True)
class WinZipAesInfo:
    """Parsed AE-x parameters from extra field ``0x9901``."""

    vendor_version: int  # 1 = AE-1, 2 = AE-2
    strength: int  # 1 / 2 / 3 → 128 / 192 / 256 bits
    actual_method: int  # underlying ZIP compression method id

    @property
    def key_bits(self) -> int:
        return {1: 128, 2: 192, 3: 256}[self.strength]

    @property
    def key_len(self) -> int:
        return self.key_bits // 8

    @property
    def salt_len(self) -> int:
        return self.key_bits // 16

    @property
    def is_ae2(self) -> bool:
        return self.vendor_version == 2


def parse_winzip_aes_extra(extra: bytes) -> WinZipAesInfo | None:
    """Return AE info from a ZIP extra field blob, or ``None`` when absent/malformed."""
    i = 0
    while i + 4 <= len(extra):
        hdr_id, size = struct.unpack_from("<HH", extra, i)
        if i + 4 + size > len(extra):
            break
        data = extra[i + 4 : i + 4 + size]
        if hdr_id == _AES_EXTRA_ID and size >= 7:
            vendor_version, vendor_id, strength, actual_method = struct.unpack(
                "<H2sBH", data[:7]
            )
            if vendor_id != b"AE" or strength not in (1, 2, 3):
                return None
            if vendor_version not in (1, 2):
                return None
            return WinZipAesInfo(vendor_version, strength, actual_method)
        i += 4 + size
    return None


def derive_winzip_aes_keys(
    password: bytes, *, salt: bytes, key_len: int
) -> tuple[bytes, bytes, bytes]:
    """PBKDF2-HMAC-SHA1 → ``(enc_key, auth_key, pw_verify)``."""
    derived = hashlib.pbkdf2_hmac(
        "sha1", password, salt, _PBKDF2_ITERS, dklen=key_len * 2 + 2
    )
    enc_key = derived[:key_len]
    auth_key = derived[key_len : key_len * 2]
    pw_verify = derived[key_len * 2 :]
    return enc_key, auth_key, pw_verify


class _AesCtrLe:
    """AES-CTR with a little-endian counter starting at 1 (WinZip AE convention)."""

    def __init__(self, key: bytes) -> None:
        # Local import: only the crypto wrapper may import cryptography.
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        if not _crypto_available():
            raise PackageNotInstalledError(
                f"The {CRYPTO_PACKAGE!r} package is required for WinZip AES decryption "
                f"(install the 'crypto' extra)."
            )
        self._encryptor = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
        self._counter = 1
        self._keystream = b""
        self._pos = 0

    def process(self, data: bytes) -> bytes:
        if not data:
            return b""
        out = bytearray(len(data))
        for i, byte in enumerate(data):
            if self._pos >= len(self._keystream):
                block = self._counter.to_bytes(16, "little")
                self._keystream = self._encryptor.update(block)
                self._pos = 0
                self._counter += 1
            out[i] = byte ^ self._keystream[self._pos]
            self._pos += 1
        return bytes(out)


class WinZipAesDecryptStream(ReadOnlyIOStream):
    """Decrypt an AE ciphertext body and verify the trailing HMAC-SHA1(10).

    ``source`` must be positioned at the start of the ciphertext (after salt +
    pw_verify) and bounded to ``cipher_len + 10`` (ciphertext + MAC). The HMAC is
    checked when the stream is fully consumed or closed after a clean EOF.
    """

    def __init__(
        self,
        source: BinaryIO,
        *,
        enc_key: bytes,
        auth_key: bytes,
        cipher_len: int,
    ) -> None:
        super().__init__()
        if cipher_len < 0:
            raise ValueError("cipher_len must be non-negative")
        self._source = source
        self._ctr = _AesCtrLe(enc_key)
        self._hmac = hmac.new(auth_key, digestmod=hashlib.sha1)
        self._cipher_remaining = cipher_len
        self._mac = b""
        self._mac_needed = _HMAC_LEN
        self._verified = False
        self._buf = bytearray()

    def _pull(self) -> None:
        if self._cipher_remaining > 0:
            chunk = self._source.read(min(65536, self._cipher_remaining))
            if not chunk:
                raise CorruptionError("Truncated WinZip AES ciphertext before HMAC")
            self._cipher_remaining -= len(chunk)
            self._hmac.update(chunk)
            self._buf.extend(self._ctr.process(chunk))
            return
        if self._mac_needed > 0:
            mac = read_exact(self._source, self._mac_needed)
            if len(mac) != self._mac_needed:
                raise CorruptionError("Truncated WinZip AES HMAC")
            self._mac += mac
            self._mac_needed = 0
            expected = self._hmac.digest()[:_HMAC_LEN]
            if not hmac.compare_digest(self._mac, expected):
                raise CorruptionError(
                    "WinZip AES HMAC mismatch (wrong password or tampered ciphertext)"
                )
            self._verified = True

    def read(self, size: int = -1) -> bytes:
        if size == 0:
            return b""
        while size < 0 or len(self._buf) < size:
            if self._cipher_remaining <= 0 and self._mac_needed <= 0:
                break
            before = len(self._buf)
            self._pull()
            if len(self._buf) == before and self._cipher_remaining <= 0:
                break
        if size < 0:
            out = bytes(self._buf)
            self._buf.clear()
            return out
        out = bytes(self._buf[:size])
        del self._buf[:size]
        return out

    def close(self) -> None:
        if not self.closed:
            # Drain remaining ciphertext + MAC so a short-read caller still gets HMAC checked.
            try:
                while self._cipher_remaining > 0 or self._mac_needed > 0:
                    self._pull()
            except CorruptionError:
                self._source.close()
                super().close()
                raise
            self._source.close()
        super().close()


def open_winzip_aes_member(
    raw: BinaryIO,
    *,
    aes: WinZipAesInfo,
    password: bytes,
    compress_size: int,
) -> BinaryIO:
    """Peel salt/pw_verify from ``raw``, verify the password, return a decrypt stream.

    ``raw`` is the full member payload (salt + verify + ciphertext + HMAC) of length
    ``compress_size``. Raises ``EncryptionError`` on a wrong password (fast-fail on the
    2-byte verification value) and ``PackageNotInstalledError`` when ``[crypto]`` is absent.
    """
    if not _crypto_available():
        raise PackageNotInstalledError(
            f"The {CRYPTO_PACKAGE!r} package is required for WinZip AES decryption "
            f"(install the 'crypto' extra)."
        )
    salt_len = aes.salt_len
    overhead = salt_len + 2 + _HMAC_LEN
    if compress_size < overhead:
        raise CorruptionError(
            f"WinZip AES member too short for salt/verify/HMAC ({compress_size} < {overhead})"
        )
    salt = read_exact(raw, salt_len)
    if len(salt) != salt_len:
        raise CorruptionError("Truncated WinZip AES salt")
    stored_verify = read_exact(raw, 2)
    if len(stored_verify) != 2:
        raise CorruptionError("Truncated WinZip AES password-verification value")

    enc_key, auth_key, pw_verify = derive_winzip_aes_keys(
        password, salt=salt, key_len=aes.key_len
    )
    if not hmac.compare_digest(stored_verify, pw_verify):
        raise EncryptionError("Wrong password for this ZIP member")

    cipher_len = compress_size - overhead
    return WinZipAesDecryptStream(
        raw, enc_key=enc_key, auth_key=auth_key, cipher_len=cipher_len
    )
