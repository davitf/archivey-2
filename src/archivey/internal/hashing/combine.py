"""Pure-Python CRC-32 / Adler-32 combine (CPython adds stdlib helpers only in 3.15+).

These match zlib's ``crc32_combine_`` / ``adler32_combine_``: given digests of
``a`` and ``b`` plus ``len(b)``, return the digest of ``a + b`` without seeing the
bytes. Used to surface whole-stream digests from per-unit trailers (multi-member
lzip).
"""

from __future__ import annotations

# ISO/zlib CRC-32 polynomial (reflected).
_CRC32_POLY = 0xEDB88320
_MOD_ADLER = 65521


def _gf2_matrix_times(mat: list[int], vec: int) -> int:
    summary = 0
    i = 0
    while vec:
        if vec & 1:
            summary ^= mat[i]
        vec >>= 1
        i += 1
    return summary


def _gf2_matrix_square(square: list[int], mat: list[int]) -> None:
    for n in range(32):
        square[n] = _gf2_matrix_times(mat, mat[n])


def crc32_combine(crc1: int, crc2: int, len2: int) -> int:
    """Return ``zlib.crc32(a + b)`` given ``crc32(a)``, ``crc32(b)``, and ``len(b)``."""
    if len2 <= 0:
        return crc1 & 0xFFFFFFFF

    odd = [0] * 32
    even = [0] * 32
    odd[0] = _CRC32_POLY
    row = 1
    for n in range(1, 32):
        odd[n] = row
        row <<= 1
    _gf2_matrix_square(even, odd)
    _gf2_matrix_square(odd, even)

    crc1 &= 0xFFFFFFFF
    remaining = len2
    while True:
        _gf2_matrix_square(even, odd)
        if remaining & 1:
            crc1 = _gf2_matrix_times(even, crc1)
        remaining >>= 1
        if remaining == 0:
            break
        _gf2_matrix_square(odd, even)
        if remaining & 1:
            crc1 = _gf2_matrix_times(odd, crc1)
        remaining >>= 1
        if remaining == 0:
            break
    return (crc1 ^ (crc2 & 0xFFFFFFFF)) & 0xFFFFFFFF


def adler32_combine(adler1: int, adler2: int, len2: int) -> int:
    """Return ``zlib.adler32(a + b)`` given ``adler32(a)``, ``adler32(b)``, ``len(b)``."""
    if len2 <= 0:
        return adler1 & 0xFFFFFFFF

    rem = len2 % _MOD_ADLER
    sum1 = adler1 & 0xFFFF
    sum2 = (rem * sum1) % _MOD_ADLER
    sum1 += (adler2 & 0xFFFF) + _MOD_ADLER - 1
    sum2 += ((adler1 >> 16) & 0xFFFF) + ((adler2 >> 16) & 0xFFFF) + _MOD_ADLER - rem
    if sum1 >= _MOD_ADLER:
        sum1 -= _MOD_ADLER
    if sum1 >= _MOD_ADLER:
        sum1 -= _MOD_ADLER
    if sum2 >= (_MOD_ADLER << 1):
        sum2 -= _MOD_ADLER << 1
    if sum2 >= _MOD_ADLER:
        sum2 -= _MOD_ADLER
    return (sum1 | (sum2 << 16)) & 0xFFFFFFFF
