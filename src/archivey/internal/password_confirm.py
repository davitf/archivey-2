"""Format-agnostic helpers for weak password-check confirmation.

Used by ZIP ZipCrypto multi-candidate disambiguation today; kept free of
``zipfile`` / format types so a future 7z (or other) reader can reuse the same
ladder rungs: bounded decompress-prefix discard, accept-only compressibility
probe, and first-match CRC selection.
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

# STORED compressibility probe: chunk size equals the minimum member size that
# uses the probe. Below this, a full CRC pass is cheaper than compressing a
# whole-member "chunk", and compressor headers dominate tiny inputs.
STORED_PROBE_CHUNK = 64 << 10
STORED_PROBE_MIN_MEMBER = STORED_PROBE_CHUNK

# Accept when compressed_len / raw_len <= 7/8 (at least 12.5% shrinkage).
# Wrong-key (random) chunks never shrink under zlib level 1; text shrinks far more.
_STORED_ACCEPT_NUM = 7
_STORED_ACCEPT_DEN = 8

_T = TypeVar("_T")


def compressibility_accepts(plaintext: bytes) -> bool:
    """Whether ``plaintext`` shrinks enough to treat as a STORED accept signal.

    Accept-only: returning False means "no signal", never "reject this password".
    """
    if not plaintext:
        return False
    compressed = zlib.compress(plaintext, level=1)
    return len(compressed) * _STORED_ACCEPT_DEN <= len(plaintext) * _STORED_ACCEPT_NUM


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


def unique_accepted(items: Sequence[tuple[_T, bool]]) -> _T | None:
    """Return the sole item whose flag is True, or None if none/ambiguous."""
    accepted = [item for item, ok in items if ok]
    if len(accepted) == 1:
        return accepted[0]
    return None


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
