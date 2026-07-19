"""Unit tests for pure-Python crc32/adler32 combine helpers."""

from __future__ import annotations

import zlib

import pytest

from archivey.internal.hashing import adler32_combine, crc32_combine

_CASES = [
    (b"", b""),
    (b"a", b""),
    (b"", b"a"),
    (b"hello", b"world"),
    (b"x" * 100, b"y" * 50),
    (b"abc", b"def" * 1000),
    (bytes(range(256)), b"\xff" * 17),
]


@pytest.mark.parametrize(("left", "right"), _CASES)
def test_crc32_combine_matches_zlib(left: bytes, right: bytes) -> None:
    c1 = zlib.crc32(left) & 0xFFFFFFFF
    c2 = zlib.crc32(right) & 0xFFFFFFFF
    assert crc32_combine(c1, c2, len(right)) == (zlib.crc32(left + right) & 0xFFFFFFFF)


@pytest.mark.parametrize(("left", "right"), _CASES)
def test_adler32_combine_matches_zlib(left: bytes, right: bytes) -> None:
    a1 = zlib.adler32(left) & 0xFFFFFFFF
    a2 = zlib.adler32(right) & 0xFFFFFFFF
    assert adler32_combine(a1, a2, len(right)) == (
        zlib.adler32(left + right) & 0xFFFFFFFF
    )


def test_multi_chunk_fold_matches_full_digest() -> None:
    parts = [b"one", b"two", b"three" * 10, b"", b"tail"]
    crc = 0
    adler = 1  # zlib.adler32(b"")
    for part in parts:
        crc = crc32_combine(crc, zlib.crc32(part) & 0xFFFFFFFF, len(part))
        adler = adler32_combine(adler, zlib.adler32(part) & 0xFFFFFFFF, len(part))
    full = b"".join(parts)
    assert crc == (zlib.crc32(full) & 0xFFFFFFFF)
    assert adler == (zlib.adler32(full) & 0xFFFFFFFF)
