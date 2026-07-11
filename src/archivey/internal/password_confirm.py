"""Format-agnostic helpers for weak password-check confirmation.

Used by ZIP ZipCrypto multi-candidate disambiguation today; kept free of
``zipfile`` / format types so a future 7z (or other) reader can reuse the same
ladder rungs: bounded decompress-prefix discard and first-match CRC selection.
"""

from __future__ import annotations

import zlib
from collections.abc import Iterable, Sequence
from typing import BinaryIO, TypeVar

# Decompressed plaintext budget for compressed-member confirmation. Empirically
# stdlib DEFLATE/BZIP2/LZMA reject wrong-key garbage within tens of bytes (see
# openspec/changes/zip-multipassword-disambiguation/design.md); 1 MiB leaves a
# wide margin and covers typical members exactly (EOF → CRC).
CONFIRM_PREFIX_BYTES = 1 << 20

_T = TypeVar("_T")


def read_and_discard(stream: BinaryIO, bound: int, *, chunk_size: int = 65536) -> int:
    """Read and discard up to ``bound`` bytes from ``stream``. Return bytes read.

    Stops early on EOF. Propagates whatever the stream raises (codec errors, I/O).
    """
    if bound <= 0:
        return 0
    total = 0
    remaining = bound
    while remaining > 0:
        chunk = stream.read(min(chunk_size, remaining))
        if not chunk:
            break
        total += len(chunk)
        remaining -= len(chunk)
    return total


def first_crc_match(
    expected_crc: int, items: Sequence[tuple[_T, int]]
) -> _T | None:
    """Return the first item whose CRC-32 matches ``expected_crc`` (candidate order)."""
    expected = expected_crc & 0xFFFFFFFF
    for item, crc in items:
        if (crc & 0xFFFFFFFF) == expected:
            return item
    return None


def crc32_over_chunks(chunks: Iterable[bytes], *, start: int = 0) -> int:
    """Accumulate CRC-32 over ``chunks`` starting from ``start`` (zlib convention)."""
    crc = start
    for chunk in chunks:
        crc = zlib.crc32(chunk, crc)
    return crc & 0xFFFFFFFF
