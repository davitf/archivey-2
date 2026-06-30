"""The single, wrapped crypto backend (AES decrypt stage).

Per the ``compressed-streams`` spec, Archivey standardises on the ``cryptography``
package (the ``[crypto]`` extra) as its one crypto backend, reached **only** through this
internal abstraction so format parsers never import ``cryptography`` directly and the
backend stays swappable. AES decryption is modelled as a *stage* that composes ahead of a
decompressor in a pipeline (e.g. AES → LZMA2 for an encrypted 7z folder).

Phase 2 builds the **interface** and the missing-dependency behaviour only. The concrete
AES-CBC decryptor (and the 7z/RAR key derivation that feeds it) lands in Phase 7, where it
is exercised end-to-end; until then ``AesParams``-driven decryption raises a clear
"not yet implemented" error while the wrapper boundary and the ``[crypto]`` gating are
already real and tested.
"""

from __future__ import annotations

import importlib.util
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Protocol

from archivey.exceptions import PackageNotInstalledError

# The package name surfaced to users (matches the [crypto] extra).
CRYPTO_PACKAGE = "cryptography"


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


class _CryptographyBackend(CryptoBackend):
    name = CRYPTO_PACKAGE

    def aes_cbc_decrypt_stage(self, params: AesParams) -> DecryptStage:
        # Deferred to Phase 7 (native 7z/RAR), where the key derivation that produces
        # AesParams also lands and the stage is exercised end-to-end. The wrapper and the
        # [crypto] gating below are complete now.
        raise NotImplementedError(
            "AES decryption is implemented in Phase 7 (native 7z/RAR readers)"
        )


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
