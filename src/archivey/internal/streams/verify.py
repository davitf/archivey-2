"""Decompressed-output digest verification stage.

Wraps a *sequential* decompressed stream and checks the member's container-supplied
digests (``ArchiveMember.hashes``) against the bytes actually read. Per the
``compressed-streams`` spec:

- Verification runs **only on a full sequential read to clean EOF**. A partial read is
  never verified (the digest of partial content is undefined), so this stage applies to
  the sequential read path, not to random-access reads.
- The mismatch is raised from the terminal read — the call that would return ``b""`` —
  *after* every data chunk (including the last) has already been delivered. A
  ``while chunk := f.read(n)`` consumer thus receives all bytes, then gets
  ``CorruptionError`` instead of the final empty read.
- An algorithm whose backend is missing (or that is unknown) is **skipped with a
  ``DIGEST_UNVERIFIABLE`` diagnostic** rather than failing the read; algorithms that can
  be computed (always including CRC32 via stdlib) are still verified.
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
from archivey.exceptions import CorruptionError
from archivey.internal.diagnostics_collector import (
    DiagnosticCollector,
    resolve_collector,
)
from archivey.internal.logs import integrity as logger
from archivey.internal.streams.streamtools import ReadOnlyIOStream

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
    """Wrap ``inner`` and verify ``expected`` digests at clean end-of-stream.

    Sequential-only: ``read`` hashes the bytes it returns; ``readinto``/``readall`` come from
    :class:`ReadOnlyIOStream` (built on this ``read``, so they hash too).
    """

    def __init__(
        self,
        inner: BinaryIO,
        expected: Mapping[str, int | bytes],
        *,
        collector: DiagnosticCollector | None = None,
        member: ArchiveMember | None = None,
        archive_name: str | None = None,
    ) -> None:
        super().__init__()
        self._inner = inner
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

    def _verify(self) -> None:
        """Check every computable digest; raise on the first mismatch."""
        self._verified = True
        for algorithm, expected in self._expected.items():
            if self._hashers[algorithm].digest() != expected:
                raise CorruptionError(
                    f"Digest mismatch for {algorithm!r}: stored value does not match the "
                    f"decompressed content."
                )

    def read(self, n: int = -1, /) -> bytes:
        data = self._inner.read(n)
        if data:
            for hasher in self._hashers.values():
                hasher.update(data)
            return data
        if not self._verified:
            self._verify()
        return data

    def seekable(self) -> bool:
        return False

    def tell(self) -> int:
        return self._inner.tell()

    def close(self) -> None:
        if not self.closed:
            # ``archive.read()`` often does a single ``read()`` that returns all
            # bytes without a follow-up empty read, so verify here when the inner
            # stream is already at clean EOF (partial reads still skip verification).
            if not self._verified and not self._inner.read(1):
                self._verify()
            self._inner.close()
            super().close()
