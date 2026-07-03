"""A pass-through stream wrapper that counts bytes read from its inner stream.

Used to measure *compressed input pressure* — how many bytes a decompressor has pulled
from the archive's raw source — so extraction can compute a **live** decompression ratio
even when the source's total size is not known (a non-seekable pipe). See the
``safe-extraction`` live-ratio guard.
"""

from __future__ import annotations

from typing import BinaryIO

from archivey.internal.streams.streamtools import DelegatingStream


class CountingReader(DelegatingStream):
    """Wrap ``inner`` and count the total number of bytes returned by reads.

    ``readinto_passthrough=False`` routes ``readinto`` through ``read`` so the count is
    updated on every read path (see :class:`DelegatingStream`). The count reflects bytes
    *pulled from the source*; with a decompressor reading fixed-size chunks it overshoots
    the strictly-decoded amount by at most one read, which is negligible against the output
    of a real decompression bomb.
    """

    def __init__(self, inner: BinaryIO) -> None:
        super().__init__(inner, readinto_passthrough=False)
        self._bytes_read = 0

    @property
    def bytes_read(self) -> int:
        return self._bytes_read

    def read(self, n: int = -1, /) -> bytes:
        data = self._inner.read(n)
        self._bytes_read += len(data)
        return data
