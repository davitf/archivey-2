"""AES decrypt stage via the ``[crypto]`` extra (``cryptography`` package).

Format parsers must not import ``cryptography`` directly — only this module does
(the backend stays swappable). AES is a *pipeline stage* ahead of a decompressor
(e.g. AES → LZMA2 for an encrypted 7z folder).

Layers here:

- :class:`DecryptStage` / :func:`open_aes_decrypt_stage` — feed ciphertext chunks,
  get plaintext (used when composing inside a larger open).
- :class:`AesDecryptStream` / :func:`open_aes_decrypt_stream` — pull ``BinaryIO``
  wrapper over a ciphertext source.
- :func:`derive_sevenzip_aes_key` / :func:`parse_sevenzip_aes_properties` —
  **7z-local** KDF helpers. RAR and WinZip-AES derive keys differently, so these
  are not on the generic :class:`CryptoBackend` surface; they live beside it for
  the 7z reader only.
"""

from __future__ import annotations

import hashlib
import importlib.util
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import BinaryIO, Protocol

from archivey.exceptions import PackageNotInstalledError, UnsupportedFeatureError
from archivey.internal.streams.streamtools import ReadOnlyIOStream

# The package name surfaced to users (matches the [crypto] extra).
CRYPTO_PACKAGE = "cryptography"

# 7-Zip's own decoder clamp (7zAes.cpp ``k_NumCyclesPower_Supported_MAX``): accept
# ``NumCyclesPower <= 24`` or the ``0x3F`` no-hash sentinel; reject 25–62.
_SEVENZIP_MAX_CYCLES_POWER = 24
_SEVENZIP_NO_HASH_SENTINEL = 0x3F


@dataclass(frozen=True)
class AesParams:
    """Inputs to an AES-CBC decrypt stage: the derived key and the initialization vector."""

    key: bytes
    iv: bytes


class DecryptStage(Protocol):
    """A streaming decrypt transform: feed ciphertext, get plaintext; ``finalize`` flushes."""

    def update(self, data: bytes) -> bytes: ...
    def finalize(self) -> bytes: ...


class CryptoBackend(ABC):
    """Abstraction over a crypto library. The only thing format code may depend on."""

    name: str

    @abstractmethod
    def aes_cbc_decrypt_stage(self, params: AesParams) -> DecryptStage:
        """Create an AES-256-CBC decrypt stage for ``params``."""
        ...


class _CryptographyDecryptStage:
    """AES-256-CBC decrypt stage backed by ``cryptography`` Cipher."""

    def __init__(self, params: AesParams) -> None:
        # Local import: format parsers never import cryptography; only this wrapper does.
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        if len(params.key) not in (16, 24, 32):
            raise ValueError(
                f"AES key must be 16, 24, or 32 bytes, got {len(params.key)}"
            )
        if len(params.iv) != 16:
            raise ValueError(f"AES-CBC IV must be 16 bytes, got {len(params.iv)}")
        cipher = Cipher(algorithms.AES(params.key), modes.CBC(params.iv))
        self._decryptor = cipher.decryptor()
        self._buf = bytearray()

    def update(self, data: bytes) -> bytes:
        if not data:
            return b""
        self._buf.extend(data)
        # CBC requires 16-byte blocks; hold a partial trailing block until finalize.
        n = len(self._buf) & ~0x0F
        if n == 0:
            return b""
        block = bytes(self._buf[:n])
        del self._buf[:n]
        return self._decryptor.update(block)

    def finalize(self) -> bytes:
        if self._buf:
            # Pad remaining ciphertext to a full block with zeros (7z AES convention).
            padlen = (-len(self._buf)) & 15
            self._buf.extend(bytes(padlen))
            out = self._decryptor.update(bytes(self._buf))
            self._buf.clear()
            out += self._decryptor.finalize()
            return out
        return self._decryptor.finalize()


class _CryptographyBackend(CryptoBackend):
    name = CRYPTO_PACKAGE

    def aes_cbc_decrypt_stage(self, params: AesParams) -> DecryptStage:
        return _CryptographyDecryptStage(params)


def _crypto_available() -> bool:
    """Whether the crypto backend's package is importable.

    Wrapped in a function (rather than an import-time flag) so tests can simulate the
    package being absent by patching this symbol.
    """
    return importlib.util.find_spec(CRYPTO_PACKAGE) is not None


def get_crypto_backend() -> CryptoBackend:
    """Return the crypto backend, or raise ``PackageNotInstalledError`` naming ``cryptography``.

    This is the single entry point to crypto for the whole library; format parsers call it
    rather than importing any crypto library themselves.
    """
    if not _crypto_available():
        raise PackageNotInstalledError(
            f"The {CRYPTO_PACKAGE!r} package is required for AES decryption "
            f"(install the 'crypto' extra)."
        )
    return _CryptographyBackend()


def open_aes_decrypt_stage(params: AesParams) -> DecryptStage:
    """Convenience: resolve the crypto backend and build an AES-CBC decrypt stage."""
    return get_crypto_backend().aes_cbc_decrypt_stage(params)


class AesDecryptStream(ReadOnlyIOStream):
    """Pull ``BinaryIO`` that decrypts an underlying ciphertext stream via AES-CBC."""

    def __init__(self, source: BinaryIO, stage: DecryptStage) -> None:
        super().__init__()
        self._source = source
        self._stage = stage
        self._buf = bytearray()
        self._eof = False

    def read(self, size: int = -1) -> bytes:
        if size == 0:
            return b""
        while not self._eof and (size < 0 or len(self._buf) < size):
            chunk = self._source.read(
                65536 if size < 0 else max(size - len(self._buf), 1)
            )
            if not chunk:
                self._buf.extend(self._stage.finalize())
                self._eof = True
                break
            self._buf.extend(self._stage.update(chunk))
        if size < 0:
            out = bytes(self._buf)
            self._buf.clear()
            return out
        out = bytes(self._buf[:size])
        del self._buf[:size]
        return out

    def close(self) -> None:
        if not self.closed:
            self._source.close()
        super().close()


def open_aes_decrypt_stream(source: BinaryIO, params: AesParams) -> BinaryIO:
    """Wrap ``source`` in an AES-CBC decrypt stream using the shared crypto backend."""
    return AesDecryptStream(source, open_aes_decrypt_stage(params))


# --- 7z-local KDF (not on the generic CryptoBackend surface) ---------------------------


def derive_sevenzip_aes_key(password: bytes, *, salt: bytes, cycles: int) -> bytes:
    """Derive a 32-byte AES-256 key with the 7z SHA-256 scheme.

    ``password`` is the raw password bytes already encoded as UTF-16LE (callers that
    hold a ``str`` should encode first). ``cycles`` is ``NumCyclesPower`` from the AES
    coder properties (``0..0x3f``). The ``0x3f`` special case copies salt+password into
    a 32-byte key without hashing.

    Values ``25..62`` are rejected with :class:`UnsupportedFeatureError`, matching
    7-Zip's own decoder clamp (``k_NumCyclesPower_Supported_MAX = 24``).
    """
    if cycles < 0 or cycles > _SEVENZIP_NO_HASH_SENTINEL:
        raise ValueError(f"NumCyclesPower out of range: {cycles}")
    if cycles == _SEVENZIP_NO_HASH_SENTINEL:
        # The 0x3f sentinel means "no hashing": key = (salt + password), zero-padded to 32.
        return (salt + password + bytes(32))[:32]
    if cycles > _SEVENZIP_MAX_CYCLES_POWER:
        raise UnsupportedFeatureError(
            f"7z NumCyclesPower {cycles} exceeds the supported maximum "
            f"({_SEVENZIP_MAX_CYCLES_POWER}); values 25–62 are rejected to match 7-Zip "
            "(and to bound KDF cost)."
        )
    # Batch rounds to cut hashlib.update call overhead (same approach as py7zr).
    cat_cycle = 6
    if cycles > cat_cycle:
        rounds = 1 << cat_cycle
        stages = 1 << (cycles - cat_cycle)
    else:
        rounds = 1 << cycles
        stages = 1
    digest = hashlib.sha256()
    salt_password = salt + password
    s = 0
    for _ in range(stages):
        digest.update(
            b"".join(
                salt_password + (s + i).to_bytes(8, "little") for i in range(rounds)
            )
        )
        s += rounds
    return digest.digest()


def parse_sevenzip_aes_properties(properties: bytes) -> tuple[int, bytes, bytes]:
    """Parse 7z AES coder properties → ``(num_cycles_power, salt, iv)``.

    Raises ``ValueError`` when the property blob is malformed, and
    :class:`UnsupportedFeatureError` when ``NumCyclesPower`` is 25–62 (7-Zip's
    ``E_NOTIMPL`` clamp).
    """
    if not properties:
        raise ValueError("empty 7z AES properties")
    first = properties[0]
    cycles = first & 0x3F
    if cycles > _SEVENZIP_MAX_CYCLES_POWER and cycles != _SEVENZIP_NO_HASH_SENTINEL:
        raise UnsupportedFeatureError(
            f"7z NumCyclesPower {cycles} exceeds the supported maximum "
            f"({_SEVENZIP_MAX_CYCLES_POWER}); values 25–62 are rejected to match 7-Zip "
            "(and to bound KDF cost)."
        )
    if first & 0xC0 == 0:
        raise ValueError("7z AES properties missing salt/IV flags")
    salt_size = (first >> 7) & 1
    iv_size = (first >> 6) & 1
    if len(properties) < 2:
        raise ValueError("truncated 7z AES properties")
    second = properties[1]
    salt_size += second >> 4
    iv_size += second & 0x0F
    expected = 2 + salt_size + iv_size
    if len(properties) != expected:
        raise ValueError(
            f"7z AES properties length {len(properties)} != expected {expected}"
        )
    salt = properties[2 : 2 + salt_size]
    iv = properties[2 + salt_size : 2 + salt_size + iv_size]
    if len(iv) < 16:
        iv = iv + bytes(16 - len(iv))
    return cycles, salt, iv


@dataclass
class SevenZipKeyCache:
    """Cache derived 7z AES keys keyed by ``(password, salt, cycles)`` for one reader."""

    _cache: dict[tuple[bytes, bytes, int], bytes] = field(default_factory=dict)

    def derive(self, password: bytes, *, salt: bytes, cycles: int) -> bytes:
        key = (password, salt, cycles)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        derived = derive_sevenzip_aes_key(password, salt=salt, cycles=cycles)
        self._cache[key] = derived
        return derived

    def aes_params_from_properties(
        self, password: bytes, properties: bytes
    ) -> AesParams:
        cycles, salt, iv = parse_sevenzip_aes_properties(properties)
        key = self.derive(password, salt=salt, cycles=cycles)
        return AesParams(key=key, iv=iv)
