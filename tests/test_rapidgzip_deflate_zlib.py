"""rapidgzip acceleration for raw deflate and zlib (and the AUTO size gate).

Covers the ``rapidgzip-deflate-zlib-acceleration`` change: parity with stdlib, gating
(OFF / absent / below-AUTO-threshold / ON-forces), error translation, and the
bounded-input contract. Gzip's accelerator path is already covered by
``test_accelerator_corruption.py``; these tests focus on the DEFLATE-family extension
and the shared size gate.
"""

from __future__ import annotations

import gzip
import io
import os
import zlib

import pytest

from archivey.config import RAPIDGZIP_AUTO_MIN_COMPRESSED_SIZE
from archivey.exceptions import CorruptionError
from archivey.internal.config import AcceleratorMode, StreamConfig
from archivey.internal.streams.codecs import (
    Codec,
    _AcceleratorStream,
    open_codec_stream,
)
from archivey.internal.streams.decompressor_stream import DecompressorStream
from archivey.internal.streams.streamtools import SlicingStream

# Large enough that compressed size exceeds the AUTO threshold for less-compressible
# payloads; used when a test needs AUTO to select rapidgzip.
_LARGE = os.urandom(2 * 1024 * 1024)
_SMALL = b"the quick brown fox jumps over the lazy dog\n" * 50


def _raw_deflate(data: bytes) -> bytes:
    co = zlib.compressobj(wbits=-15)
    return co.compress(data) + co.flush()


def _assert_accelerator(stream: object) -> None:
    assert isinstance(getattr(stream, "_inner", None), _AcceleratorStream)


def _assert_stdlib_zlib(stream: object) -> None:
    assert isinstance(getattr(stream, "_inner", None), DecompressorStream)


# --- 4.1 Parity ----------------------------------------------------------------------


def _read_exact(stream: object, n: int) -> bytes:
    """Gather exactly ``n`` bytes (stdlib decompressor reads may return short)."""
    read = getattr(stream, "read")
    buf = bytearray()
    while len(buf) < n:
        chunk = read(n - len(buf))
        if not chunk:
            break
        buf.extend(chunk)
    return bytes(buf)


@pytest.mark.parametrize(
    ("codec", "compress"),
    [
        (Codec.DEFLATE, _raw_deflate),
        (Codec.ZLIB, zlib.compress),
    ],
)
def test_accelerated_deflate_zlib_decode_and_seek_match_stdlib(
    codec: Codec, compress: object
) -> None:
    pytest.importorskip("rapidgzip")
    payload = _LARGE
    compressed = compress(payload)  # type: ignore[operator]
    assert len(compressed) >= RAPIDGZIP_AUTO_MIN_COMPRESSED_SIZE
    mid = len(payload) // 3
    on = StreamConfig(use_rapidgzip=AcceleratorMode.ON, seekable=True)
    off = StreamConfig(use_rapidgzip=AcceleratorMode.OFF, seekable=True)

    with open_codec_stream(codec, io.BytesIO(compressed), config=on) as accel:
        _assert_accelerator(accel)
        head = _read_exact(accel, mid)
        assert accel.seek(mid // 2) == mid // 2
        mid_chunk = _read_exact(accel, 100)
        assert accel.seek(0) == 0
        full = accel.read()

    with open_codec_stream(codec, io.BytesIO(compressed), config=off) as std:
        _assert_stdlib_zlib(std)
        assert _read_exact(std, mid) == head == payload[:mid]
        assert std.seek(mid // 2) == mid // 2
        assert _read_exact(std, 100) == mid_chunk == payload[mid // 2 : mid // 2 + 100]
        assert std.seek(0) == 0
        assert std.read() == full == payload


# --- 4.2 Gating ----------------------------------------------------------------------


@pytest.mark.parametrize("codec", [Codec.DEFLATE, Codec.ZLIB, Codec.GZIP])
def test_off_and_below_auto_threshold_use_stdlib(codec: Codec) -> None:
    pytest.importorskip("rapidgzip")
    if codec is Codec.GZIP:
        compressed = gzip.compress(_SMALL)
    elif codec is Codec.ZLIB:
        compressed = zlib.compress(_SMALL)
    else:
        compressed = _raw_deflate(_SMALL)
    assert len(compressed) < RAPIDGZIP_AUTO_MIN_COMPRESSED_SIZE

    off = StreamConfig(use_rapidgzip=AcceleratorMode.OFF, seekable=True)
    auto = StreamConfig(use_rapidgzip=AcceleratorMode.AUTO, seekable=True)

    with open_codec_stream(codec, io.BytesIO(compressed), config=off) as stream:
        if codec is Codec.GZIP:
            assert not isinstance(stream._inner, _AcceleratorStream)
        else:
            _assert_stdlib_zlib(stream)
        assert stream.read()  # non-empty

    with open_codec_stream(codec, io.BytesIO(compressed), config=auto) as stream:
        assert not isinstance(stream._inner, _AcceleratorStream)


@pytest.mark.parametrize("codec", [Codec.DEFLATE, Codec.ZLIB, Codec.GZIP])
def test_on_forces_rapidgzip_below_threshold(codec: Codec) -> None:
    pytest.importorskip("rapidgzip")
    if codec is Codec.GZIP:
        compressed = gzip.compress(_SMALL)
    elif codec is Codec.ZLIB:
        compressed = zlib.compress(_SMALL)
    else:
        compressed = _raw_deflate(_SMALL)
    assert len(compressed) < RAPIDGZIP_AUTO_MIN_COMPRESSED_SIZE

    on = StreamConfig(use_rapidgzip=AcceleratorMode.ON, seekable=True)
    with open_codec_stream(codec, io.BytesIO(compressed), config=on) as stream:
        _assert_accelerator(stream)
        expected = (
            gzip.decompress(compressed)
            if codec is Codec.GZIP
            else zlib.decompress(
                compressed, -15 if codec is Codec.DEFLATE else zlib.MAX_WBITS
            )
        )
        assert stream.read() == expected


def test_auto_selects_rapidgzip_above_threshold() -> None:
    pytest.importorskip("rapidgzip")
    compressed = zlib.compress(_LARGE)
    assert len(compressed) >= RAPIDGZIP_AUTO_MIN_COMPRESSED_SIZE
    auto = StreamConfig(use_rapidgzip=AcceleratorMode.AUTO, seekable=True)
    with open_codec_stream(Codec.ZLIB, io.BytesIO(compressed), config=auto) as stream:
        _assert_accelerator(stream)
        assert stream.read() == _LARGE


# --- 4.3 Error translation + truncation limitation -----------------------------------


@pytest.mark.parametrize(
    ("codec", "compress"),
    [
        (Codec.DEFLATE, _raw_deflate),
        (Codec.ZLIB, zlib.compress),
    ],
)
def test_corrupt_deflate_zlib_body_translates_to_corruption(
    codec: Codec, compress: object
) -> None:
    pytest.importorskip("rapidgzip")
    payload = _SMALL * 20
    corrupt = bytearray(compress(payload))  # type: ignore[operator]
    # Clobber past any zlib/deflate header bytes.
    corrupt[10:40] = b"\x00" * 30
    on = StreamConfig(use_rapidgzip=AcceleratorMode.ON, seekable=True)
    with open_codec_stream(codec, io.BytesIO(bytes(corrupt)), config=on) as stream:
        with pytest.raises(CorruptionError):
            stream.read()


def test_standalone_zlib_midcut_may_short_read_through_rapidgzip() -> None:
    """Accepted limitation: rapidgzip may silently short-read a mid-stream zlib cut.

    Stdlib ``zlib`` would raise ``incomplete or truncated stream``; the accelerator
    path has no Adler-32 / ISIZE backstop. Either a short read or a translated
    ``CorruptionError``/``TruncatedError`` is acceptable — a raw rapidgzip exception
    is not.
    """
    pytest.importorskip("rapidgzip")
    full = zlib.compress(_SMALL * 100)
    # Mid-stream cut that leaves a partially-decodable prefix (not just a missing
    # Adler trailer).
    cut = full[: max(len(full) // 2, 20)]
    on = StreamConfig(use_rapidgzip=AcceleratorMode.ON, seekable=True)
    try:
        with open_codec_stream(Codec.ZLIB, io.BytesIO(cut), config=on) as stream:
            out = stream.read()
    except CorruptionError:
        return
    # Silent short read: decompressed less than the full payload would have been.
    assert len(out) < len(_SMALL * 100)


# --- 4.4 Bounded input ---------------------------------------------------------------


def test_bounded_deflate_with_trailing_bytes_decodes() -> None:
    """A deflate blob + trailing junk, fed through a length-bounded slice, decodes cleanly."""
    pytest.importorskip("rapidgzip")
    payload = _SMALL * 10
    raw = _raw_deflate(payload)
    padded = raw + b"PK\x03\x04" + b"trailing-junk-not-deflate"
    bounded = SlicingStream(io.BytesIO(padded), start=0, length=len(raw))
    on = StreamConfig(use_rapidgzip=AcceleratorMode.ON, seekable=True)
    with open_codec_stream(Codec.DEFLATE, bounded, config=on) as stream:
        _assert_accelerator(stream)
        assert stream.read() == payload
        # Mid-stream seek still works on the bounded accelerator path.
        assert stream.seek(len(payload) // 2) == len(payload) // 2
        assert stream.read() == payload[len(payload) // 2 :]
