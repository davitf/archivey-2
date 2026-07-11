"""Format-agnostic helpers for weak password-check confirmation.

Used by ZIP ZipCrypto multi-candidate disambiguation today; kept free of
``zipfile`` / format types so a future 7z (or other) reader can reuse the same
ladder rungs: bounded decompress-prefix discard and first-match CRC selection.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TypeVar

# Decompressed plaintext budget for compressed-member confirmation. Empirically
# stdlib DEFLATE/BZIP2/LZMA reject wrong-key garbage within tens of bytes (see
# the archived zip-multipassword-disambiguation design); 64 KiB leaves ample
# margin and covers typical members exactly (EOF → CRC).
CONFIRM_PREFIX_BYTES = 64 * 1024

_T = TypeVar("_T")


def first_crc_match(
    expected_crc: int, items: Sequence[tuple[_T, int]]
) -> _T | None:
    """Return the first item whose CRC-32 matches ``expected_crc`` (candidate order)."""
    expected = expected_crc & 0xFFFFFFFF
    for item, crc in items:
        if (crc & 0xFFFFFFFF) == expected:
            return item
    return None
