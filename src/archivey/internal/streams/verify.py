"""Decompressed-output digest (and length) verification stage.

Wraps a *sequential* decompressed stream and checks the member's container-supplied
digests (``ArchiveMember.hashes``) — and, when ``expected_size`` is supplied, its
declared decompressed length — against the bytes actually read. The length check bounds
reads to the declared size (so an over-long decode stops there and raises instead of
running unbounded), raises on a short stream, and runs alongside the digest check so a
member with no stored hash still cannot be silently truncated. Per the
``compressed-streams`` spec:

- Verification runs **only on a full sequential read to clean EOF**. A partial read is
  never verified (the digest of partial content is undefined), so this stage applies to
  the sequential read path, not to random-access reads.
- The stage is transparent to seeking: it delegates ``seek``/``seekable`` to the inner
  stream, but a seek that moves off the sequential frontier disables verification for the
  rest of the stream's life (the running digest is then incomplete). So a member opened
  ``MemberStreams.SEEKABLE`` is still verified if the caller only reads forward, and simply
  skips the check once it seeks — mirroring the gzip truncation backstop in ``codecs.py``.
- The mismatch is raised from the terminal read — the call that would return ``b""`` —
  *after* every data chunk (including the last) has already been delivered. A
  ``while chunk := f.read(n)`` consumer thus receives all bytes, then gets
  ``CorruptionError`` instead of the final empty read.
- An algorithm whose backend is missing (or that is unknown) is **skipped with a
  ``DIGEST_UNVERIFIABLE`` diagnostic** rather than failing the read; algorithms that can
  be computed (always including CRC32 via stdlib, and BLAKE2sp via the internal hasher)
  are still verified.
- ``close()`` also verifies when the inner stream is already at clean EOF, so a single
  ``read()`` that consumed the whole member (as ``ArchiveReader.read`` does) still
  checks digests.

This is distinct from a codec's own internal integrity check (gzip trailer CRC, xz stream
check) — those are surfaced by the codec backend; this verifies the *container's* digest
over the decompressed bytes.
"""

from __future__ import annotations

import hashlib
import zlib
from typing import TYPE_CHECKING, BinaryIO, Callable, Mapping, Protocol

from archivey.diagnostics import DiagnosticCode, DigestContext
from archivey.exceptions import CorruptionError, TruncatedError
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

if TYPE_CHECKING:
    from archivey.types import ArchiveMember


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


def _make_hasher(algorithm: str) -> Callable[[], _IncrementalHasher] | None:
    """Return a zero-arg factory for an incremental hasher, or ``None`` if unavailable."""
    name = algorithm.lower()
    if name == "crc32":
        return _Crc32Hasher
    if name == "blake2sp":
        # RAR5 BLAKE2sp — not in hashlib; zero-dep tree hash on blake2s (see hashing/).
        return Blake2sp
    if name in hashlib.algorithms_available:
        return lambda: hashlib.new(name)
    return None


def _expected_as_bytes(value: int | bytes, hasher: _IncrementalHasher) -> bytes:
    """Normalize a stored digest to ``bytes`` once, in the hasher's own width.

    A digest may be stored as raw bytes or as a big-endian integer (e.g. CRC32 as int).
    Converting the *expected* value up front lets verification be a plain ``bytes ==
    bytes`` compare against ``hasher.digest()`` — no per-read int<->bytes round-trip.
    """
    if isinstance(value, int):
        # Size the buffer to whichever is larger: the hasher's digest width (so a normal,
        # in-range value compares equal to digest()) or the value's own minimum width. A
        # malformed/oversized stored int (e.g. a "crc32" > 2**32) thus produces a longer
        # byte string that simply won't equal the digest — surfacing as a CorruptionError on
        # verify — rather than raising OverflowError here and leaking a non-ArchiveyError.
        width = max(hasher.digest_size, (value.bit_length() + 7) // 8)
        return value.to_bytes(width, "big")
    return value


class VerifyingStream(ReadOnlyIOStream):
    """Wrap ``inner`` and verify the member's ``expected`` digests (and, optionally, its
    declared decompressed length) on a clean sequential read to end-of-stream.

    ``read`` hashes the bytes it returns (``readinto``/``readall`` come from
    :class:`ReadOnlyIOStream`, built on this ``read``, so they hash too) and checks the
    digests once the stream reaches clean EOF. ``seek``/``seekable`` delegate to ``inner``;
    a seek off the sequential frontier disables verification for the rest of the stream's
    life, since the running digest can no longer cover the whole member.

    When ``expected_size`` is given, reads are bounded to that many bytes so an
    over-long decode (a decompressor/pipe emitting more than the member's declared size)
    stops at the declared size and raises :class:`CorruptionError` immediately rather than
    running unbounded; a stream that ends short of it raises :class:`TruncatedError`. The
    length check runs after the digest check (so a hashed short read still surfaces as a
    digest mismatch) and the short verdict is applied at close, after the inner stream is
    closed, so a more specific inner error (e.g. a wrong-password subprocess exit) wins.
    """

    def __init__(
        self,
        inner: BinaryIO,
        expected: Mapping[str, int | bytes],
        *,
        expected_size: int | None = None,
        collector: DiagnosticCollector | None = None,
        member: ArchiveMember | None = None,
        archive_name: str | None = None,
    ) -> None:
        super().__init__()
        self._inner = inner
        self._expected_size = expected_size
        self._short = False
        self._expected: dict[str, bytes] = {}
        self._hashers: dict[str, _IncrementalHasher] = {}
        for algorithm, value in expected.items():
            factory = _make_hasher(algorithm)
            if factory is None:
                message = (
                    f"Cannot verify digest {algorithm!r} (unknown algorithm or backend "
                    f"not installed); skipping integrity check for it."
                )
                resolve_collector(collector).emit(
                    code=DiagnosticCode.DIGEST_UNVERIFIABLE,
                    message=message,
                    context=DigestContext(
                        archive_name=archive_name,
                        member_name=member.name if member is not None else "",
                        member_id=member._member_id if member is not None else None,
                        algorithm=algorithm,
                        reason="unknown_algorithm_or_backend",
                    ),
                    member=member,
                    attach_to_member=member is not None,
                    logger=logger,
                )
                continue
            hasher = factory()
            self._hashers[algorithm] = hasher
            self._expected[algorithm] = _expected_as_bytes(value, hasher)
        self._verified = False
        self._pos = 0  # bytes read so far — the sequential verification frontier
        self._verify_enabled = True  # cleared by a seek off the frontier

    def _verify_digests(self) -> None:
        """Check every computable digest; raise on the first mismatch."""
        for algorithm, expected in self._expected.items():
            if self._hashers[algorithm].digest() != expected:
                raise CorruptionError(
                    f"Digest mismatch for {algorithm!r}: stored value does not match the "
                    f"decompressed content."
                )

    def _finish(self) -> None:
        """Run end-of-stream checks once: over-length, then digests, then note short."""
        self._verified = True
        if self._expected_size is not None and self._pos >= self._expected_size:
            # Delivered the declared size; the underlying must have nothing more.
            if self._inner.read(1):
                raise CorruptionError(
                    "Decompressed content exceeds its declared size of "
                    f"{self._expected_size} bytes."
                )
        self._verify_digests()
        if self._expected_size is not None and self._pos < self._expected_size:
            # Short read. Defer raising to close so a more specific inner error (e.g. a
            # subprocess exit code) takes precedence; a hashed short read already raised
            # a digest mismatch above.
            self._short = True

    def read(self, n: int = -1, /) -> bytes:
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
                want = remaining if n < 0 else min(n, remaining)
                data = self._inner.read(want)
        else:
            data = self._inner.read(n)
        if data:
            self._pos += len(data)
            if self._verify_enabled:
                for hasher in self._hashers.values():
                    hasher.update(data)
            return data
        if self._verify_enabled and not self._verified:
            self._finish()
        return data

    def seekable(self) -> bool:
        return is_seekable(self._inner)

    def seek(self, offset: int, whence: int = 0, /) -> int:
        result = self._inner.seek(offset, whence)
        # A seek off the sequential frontier makes the running digest incomplete; give up
        # on verification (a no-op seek to the current position keeps it armed).
        if result != self._pos:
            self._verify_enabled = False
        self._pos = result
        return result

    def tell(self) -> int:
        return self._inner.tell()

    def close(self) -> None:
        if self.closed:
            return
        # ``archive.read()`` often does a single ``read()`` that returns all bytes without
        # a follow-up empty read, so verify here when the inner stream is already at clean
        # EOF (partial reads still skip verification).
        finish_exc: CorruptionError | None = None
        if self._verify_enabled and not self._verified:
            reached_declared = (
                self._expected_size is not None and self._pos >= self._expected_size
            )
            # ``_finish`` probes for over-length itself; only probe here to decide whether
            # the stream ended cleanly when the declared size was not (yet) reached.
            if reached_declared or not self._inner.read(1):
                try:
                    self._finish()
                except CorruptionError as exc:
                    finish_exc = exc  # still close the inner below before re-raising
        short = self._short
        try:
            self._inner.close()
        finally:
            super().close()
        if finish_exc is not None:
            raise finish_exc
        if short:
            raise TruncatedError(
                f"Decompressed content ended after {self._pos} of "
                f"{self._expected_size} expected bytes."
            )
