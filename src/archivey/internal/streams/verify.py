"""Decompressed-output digest (and length) verification.

Container digests (``ArchiveMember.hashes``) and optional declared decompressed
length are checked on a clean sequential read to EOF.

Two delivery shapes (same rules, different wrappers):

- :class:`MemberVerifier` — the logic object. **Fused into**
  :class:`~archivey.internal.streams.archive_stream.ArchiveStream` on the member
  hot path (one fewer Python layer; nested codec ``ArchiveStream``s can collapse).
- :class:`VerifyingStream` — standalone ``BinaryIO`` wrapper around an inner +
  verifier. Kept for codec length backstops and tests; prefer fusion for members.

Per the ``compressed-streams`` spec:

- Verification runs **only on a full sequential read to clean EOF**. A partial
  read is never verified. ``read(0)`` is a no-op (not EOF).
- A seek off the sequential frontier disables verification for the rest of the
  handle's life.
- On bounded ``read(n)``, the mismatch / hash-less short raises from the terminal
  empty read *after* every data chunk has been delivered.
- On ``read(-1)`` / ``readall``, the complete-stream call includes the EOF verdict
  and raises (so ``read(); close()`` cannot silently accept bad content).
- ``close()`` / ``finish_on_close`` MUST NOT introduce a first content
  ``TruncatedError`` / ``CorruptionError`` (teardown errors may still propagate).
- Missing/unknown digest algorithms emit ``DIGEST_UNVERIFIABLE`` and are skipped.
"""

from __future__ import annotations

import hashlib
import zlib
from typing import TYPE_CHECKING, Any, BinaryIO, Callable, Mapping, Protocol

from archivey.diagnostics import DiagnosticCode, DigestContext
from archivey.exceptions import ArchiveyError, CorruptionError, TruncatedError
from archivey.internal.diagnostics_collector import (
    DiagnosticCollector,
    resolve_collector,
)
from archivey.internal.hashing.blake2sp import Blake2sp
from archivey.internal.logs import integrity as logger
from archivey.internal.streams.streamtools import (
    ReadOnlyIOStream,
    is_seekable,
)
from archivey.types import HashAlgorithm

if TYPE_CHECKING:
    from archivey.types import ArchiveMember

# Keys: ``HashAlgorithm`` or algorithm name string (``hashlib`` / legacy). Values are
# digest ``bytes`` (CRC-32 as four big-endian bytes).
_ExpectedHashes = Mapping[Any, bytes]
_DigestTransforms = Mapping[Any, Callable[[bytes], bytes]]

# Bounded drain step for sized ``read(-1)``. Must not use ``inner.read(-1)`` on the
# sized branch: ``expected_size`` is a decompression-bomb cap.
_SIZED_DRAIN_CHUNK = 65536


def _algo_key(algorithm: Any) -> str:
    """Normalize a hash key to a lowercase algorithm name.

    ``str(HashAlgorithm.CRC32)`` is ``"HashAlgorithm.CRC32"``; use the enum value
    so registry lookup matches ``"crc32"``.
    """
    if isinstance(algorithm, HashAlgorithm):
        return algorithm.value
    return str(algorithm).lower()


class _IncrementalHasher(Protocol):
    """The subset of the ``hashlib`` hash interface this stage uses."""

    @property
    def digest_size(self) -> int: ...
    def update(self, data: bytes, /) -> None: ...
    def digest(self) -> bytes: ...


class _Crc32Hasher:
    """A ``hashlib``-shaped wrapper over ``zlib.crc32`` so all algorithms share an interface."""

    digest_size = 4

    def __init__(self) -> None:
        self._value = 0

    def update(self, data: bytes, /) -> None:
        self._value = zlib.crc32(data, self._value)

    def digest(self) -> bytes:
        return (self._value & 0xFFFFFFFF).to_bytes(self.digest_size, "big")


class _Adler32Hasher:
    """A ``hashlib``-shaped wrapper over ``zlib.adler32`` (RFC 1950 / standalone zlib)."""

    digest_size = 4

    def __init__(self) -> None:
        self._value = 1  # zlib.adler32(b"")

    def update(self, data: bytes, /) -> None:
        self._value = zlib.adler32(data, self._value)

    def digest(self) -> bytes:
        return (self._value & 0xFFFFFFFF).to_bytes(self.digest_size, "big")


def _make_hasher(algorithm: Any) -> Callable[[], _IncrementalHasher] | None:
    """Return a zero-arg factory for an incremental hasher, or ``None`` if unavailable."""
    name = _algo_key(algorithm)
    if name == "crc32":
        return _Crc32Hasher
    if name == "adler32":
        return _Adler32Hasher
    if name == "blake2sp":
        # RAR5 BLAKE2sp — not in hashlib; zero-dep tree hash on blake2s (see hashing/).
        return Blake2sp
    if name in hashlib.algorithms_available:
        return lambda: hashlib.new(name)
    return None


class MemberVerifier:
    """Incremental digest/length checker over bytes read from an inner stream.

    Not a ``BinaryIO`` itself — :class:`VerifyingStream` and
    :class:`~archivey.internal.streams.archive_stream.ArchiveStream` own one and
    call :meth:`read` / :meth:`note_seek` / :meth:`finish_on_close` against their
    inner handle.
    """

    def __init__(
        self,
        expected: _ExpectedHashes,
        *,
        expected_size: int | None = None,
        collector: DiagnosticCollector | None = None,
        member: ArchiveMember | None = None,
        archive_name: str | None = None,
        digest_transforms: _DigestTransforms | None = None,
    ) -> None:
        self._expected_size = expected_size
        self._expected: dict[str, bytes] = {}
        self._hashers: dict[str, _IncrementalHasher] = {}
        self._digest_transforms: dict[str, Callable[[bytes], bytes]] = {}
        if digest_transforms:
            for key, transform in digest_transforms.items():
                self._digest_transforms[_algo_key(key)] = transform
        for algorithm, value in expected.items():
            key = _algo_key(algorithm)
            factory = _make_hasher(key)
            if factory is None:
                message = (
                    f"Cannot verify digest {key!r} (unknown algorithm or backend "
                    f"not installed); skipping integrity check for it."
                )
                resolve_collector(collector).emit(
                    code=DiagnosticCode.DIGEST_UNVERIFIABLE,
                    message=message,
                    context=DigestContext(
                        archive_name=archive_name,
                        member_name=member.name if member is not None else "",
                        member_id=member._member_id if member is not None else None,
                        algorithm=key,
                        reason="unknown_algorithm_or_backend",
                    ),
                    member=member,
                    attach_to_member=member is not None,
                    logger=logger,
                )
                continue
            hasher = factory()
            self._hashers[key] = hasher
            self._expected[key] = value
        self._verified = False
        self._pos = 0  # bytes read so far — the sequential verification frontier
        self._verify_enabled = True  # cleared by a seek off the frontier

    @property
    def enabled(self) -> bool:
        return self._verify_enabled

    @property
    def pos(self) -> int:
        return self._pos

    def _verify_digests(self) -> None:
        """Check every computable digest; raise on the first mismatch."""
        for algorithm, expected in self._expected.items():
            computed = self._hashers[algorithm].digest()
            transform = self._digest_transforms.get(algorithm)
            if transform is not None:
                computed = transform(computed)
            if computed != expected:
                raise CorruptionError(
                    f"Digest mismatch for {algorithm!r}: stored value does not match the "
                    f"decompressed content."
                )

    def _finish(self, inner: BinaryIO, *, raise_content_faults: bool) -> None:
        """Run end-of-stream checks once.

        When ``raise_content_faults`` is true (read path), short bodies raise
        :class:`~archivey.exceptions.TruncatedError` and digest mismatches raise
        :class:`~archivey.exceptions.CorruptionError`. A short body that also
        carries a hash raises ``TruncatedError`` (best-effort: shortfall vs digest
        mismatch are not always separable). When false (close path), content faults
        are never raised — ``finish_on_close`` must not be a first content-fault
        surface.
        """
        self._verified = True
        if self._expected_size is not None and self._pos >= self._expected_size:
            # Delivered the declared size; the underlying must have nothing more.
            # This probe also drains post-payload authenticators (e.g. WinZip AES HMAC).
            try:
                trailing = inner.read(1)
            except ArchiveyError:
                raise
            except Exception:  # noqa: BLE001 - opaque accel errors ≈ no trailing data
                trailing = b""
            if trailing:
                if raise_content_faults:
                    raise CorruptionError(
                        "Decompressed content exceeds its declared size of "
                        f"{self._expected_size} bytes."
                    )
                return
        if self._expected_size is not None and self._pos < self._expected_size:
            # Short — TruncatedError even when a hash is present (best-effort verdict).
            if raise_content_faults:
                raise TruncatedError(
                    f"Decompressed content ended after {self._pos} of "
                    f"{self._expected_size} expected bytes."
                )
            return
        if raise_content_faults:
            self._verify_digests()

    def _read_sized_all(self, inner: BinaryIO) -> bytes:
        """Drain to genuine EOF in bounded steps, capped by the declared size.

        ``expected_size`` is a decompression-bomb bound: do **not** delegate to
        ``inner.read(-1)``, which would pull an over-long adversarial payload into
        RAM. A single ``inner.read(remaining)`` is also insufficient — ``BinaryIO``
        may short-read without EOF.
        """
        assert self._expected_size is not None
        chunks: list[bytes] = []
        while self._pos < self._expected_size:
            remaining = self._expected_size - self._pos
            want = min(_SIZED_DRAIN_CHUNK, remaining)
            try:
                piece = inner.read(want)
            except ArchiveyError:
                self._verify_enabled = False
                raise
            except Exception as exc:
                # Opaque accelerator EOF while still short of the declared size
                # (macOS rapidgzip often raises instead of returning b""). Surface
                # TruncatedError on this read path so close need not raise it.
                raise TruncatedError(
                    f"Decompressed content ended after {self._pos} of "
                    f"{self._expected_size} expected bytes."
                ) from exc
            if not piece:
                break
            self._pos += len(piece)
            if self._verify_enabled:
                for hasher in self._hashers.values():
                    hasher.update(piece)
            chunks.append(piece)
        # Run the EOF verdict in this complete-stream call.
        if self._verify_enabled and not self._verified:
            self._finish(inner, raise_content_faults=True)
        return b"".join(chunks)

    def read(self, inner: BinaryIO, n: int = -1) -> bytes:
        """Read from ``inner``, update digests/bounds, and verify on clean EOF."""
        # read(0) is a no-op — never treat it as EOF (stdlib file / BytesIO contract).
        if n == 0:
            return b""
        if n < 0:
            # Complete-stream read: include the EOF verdict in this call.
            if (
                self._verify_enabled
                and self._expected_size is not None
                and not self._verified
            ):
                return self._read_sized_all(inner)
            try:
                data = inner.read(-1)
            except Exception:  # noqa: BLE001 - abandon verify; re-raise decoder error
                self._verify_enabled = False
                raise
            if data:
                self._pos += len(data)
                if self._verify_enabled:
                    for hasher in self._hashers.values():
                        hasher.update(data)
            if self._verify_enabled and not self._verified:
                self._finish(inner, raise_content_faults=True)
            return data

        if (
            self._verify_enabled
            and self._expected_size is not None
            and not self._verified
        ):
            remaining = self._expected_size - self._pos
            if remaining <= 0:
                data = (
                    b""  # at the declared size — fall through to end-of-stream checks
                )
            else:
                want = min(n, remaining)
                try:
                    data = inner.read(want)
                except Exception:  # noqa: BLE001 - abandon verify; re-raise decoder error
                    # Abandon length/digest checks: close must not raise a second fault
                    # after the caller already saw this decode error (macOS rapidgzip
                    # mid-cut often raises here, then again on finish_on_close).
                    self._verify_enabled = False
                    raise
        else:
            try:
                data = inner.read(n)
            except Exception:  # noqa: BLE001 - abandon verify; re-raise decoder error
                self._verify_enabled = False
                raise
        if data:
            self._pos += len(data)
            if self._verify_enabled:
                for hasher in self._hashers.values():
                    hasher.update(data)
            return data
        if self._verify_enabled and not self._verified:
            self._finish(inner, raise_content_faults=True)
        return data

    def note_seek(self, result: int) -> None:
        """Update the frontier after a successful inner seek; disable if off-frontier."""
        if result != self._pos:
            self._verify_enabled = False
        self._pos = result

    def finish_on_close(self, inner: BinaryIO) -> None:
        """Close ``inner`` — teardown only, never a content-fault surface.

        Every content verdict (digest mismatch, hash-less short, over-length) fires
        from the completing read: the terminal empty ``read`` of a chunked drain, or
        ``read(-1)`` / ``readall``. ``close`` therefore does **not** read, probe, or
        drain the inner to force a late verdict — a partial read before clean EOF is
        a deliberate abandon with no verdict, so ``close`` stays quiet and lazy, and
        never surfaces a first ``TruncatedError`` / ``CorruptionError`` (including an
        inner decoder's *deferred* truncation, which a bare ``read(1)`` probe would
        otherwise trip). A teardown error raised by ``inner.close()`` itself — a
        subprocess exit code, or an inner stream that authenticates in its own
        ``close`` (e.g. WinZip AES HMAC) — still propagates.
        """
        inner.close()


def build_member_verifier(
    expected: _ExpectedHashes | None,
    *,
    expected_size: int | None = None,
    collector: DiagnosticCollector | None = None,
    member: ArchiveMember | None = None,
    archive_name: str | None = None,
    digest_transforms: _DigestTransforms | None = None,
) -> MemberVerifier | None:
    """Return a :class:`MemberVerifier` when there is something to check, else ``None``."""
    hashes = expected if expected is not None else {}
    if not hashes and expected_size is None:
        return None
    return MemberVerifier(
        hashes,
        expected_size=expected_size,
        collector=collector,
        member=member,
        archive_name=archive_name,
        digest_transforms=digest_transforms,
    )


class VerifyingStream(ReadOnlyIOStream):
    """Wrap ``inner`` and verify digests/length via a :class:`MemberVerifier`.

    Kept for codec length backstops and tests. Member backends fuse the same
    verifier into :class:`~archivey.internal.streams.archive_stream.ArchiveStream`.
    """

    def __init__(
        self,
        inner: BinaryIO,
        expected: _ExpectedHashes,
        *,
        expected_size: int | None = None,
        collector: DiagnosticCollector | None = None,
        member: ArchiveMember | None = None,
        archive_name: str | None = None,
        digest_transforms: _DigestTransforms | None = None,
    ) -> None:
        super().__init__()
        self._inner = inner
        # This wrapper is used explicitly (codec length backstops, tests), so it always
        # owns a verifier even when there is nothing to check — unlike the fused path,
        # which uses ``build_member_verifier`` to skip the wrapper entirely in that case.
        self._verifier = MemberVerifier(
            expected,
            expected_size=expected_size,
            collector=collector,
            member=member,
            archive_name=archive_name,
            digest_transforms=digest_transforms,
        )

    # Compat for tests / diagnostics that inspect frontier state.
    @property
    def _verify_enabled(self) -> bool:
        return self._verifier.enabled

    @property
    def _pos(self) -> int:
        return self._verifier.pos

    @property
    def _expected_size(self) -> int | None:
        return self._verifier._expected_size

    def read(self, n: int = -1, /) -> bytes:
        return self._verifier.read(self._inner, n)

    def seekable(self) -> bool:
        return is_seekable(self._inner)

    def seek(self, offset: int, whence: int = 0, /) -> int:
        result = self._inner.seek(offset, whence)
        self._verifier.note_seek(result)
        return result

    def tell(self) -> int:
        return self._inner.tell()

    def close(self) -> None:
        if self.closed:
            return
        try:
            self._verifier.finish_on_close(self._inner)
        finally:
            # finish_on_close closes the inner; always mark the wrapper closed even
            # when a typed probe error propagates (F2).
            if not self.closed:
                super().close()
