"""Raw PPMd stream coverage — no 7z/ZIP container.

These tests exercise ``pyppmd`` and archivey's PPMd codec/stream adapters directly so
Windows/Linux native aborts can be chased on the *minimal* surface (see
``docs/internal/known-issues.md`` and ``scripts/ppmd_native_stress.py``).
"""

from __future__ import annotations

import io
import struct

from archivey.internal.streams.codecs import Codec, CodecParams, open_codec_stream
from archivey.internal.streams.decompress import PpmdDecompressorStream
from tests.conftest import requires

pytestmark = requires("pyppmd")

_ORDER = 6
_MEM = 1 << 20
_CONTENT = b"the quick brown fox jumps over the lazy dog\n" * 40


def _encode_ppmd7(data: bytes, *, order: int = _ORDER, mem: int = _MEM) -> bytes:
    import pyppmd

    enc = pyppmd.Ppmd7Encoder(order, mem)
    return enc.encode(data) + enc.flush()


def _encode_ppmd8(data: bytes, *, order: int = _ORDER, mem: int = _MEM) -> bytes:
    import pyppmd

    enc = pyppmd.Ppmd8Encoder(order, mem, 0)
    return enc.encode(data) + enc.flush(True)


def _decode_ppmd7_raw(
    packed: bytes, size: int, *, order: int = _ORDER, mem: int = _MEM
) -> bytes:
    import pyppmd

    dec = pyppmd.Ppmd7Decoder(order, mem)
    out = bytearray(dec.decode(packed, size))
    while len(out) < size:
        need = size - len(out)
        chunk = dec.decode(b"\0" if dec.needs_input else b"", need)
        if not chunk:
            break
        out.extend(chunk)
    return bytes(out)


def test_raw_pyppmd7_roundtrip() -> None:
    """Bare ``Ppmd7Encoder``/``Ppmd7Decoder`` (no archivey, no 7z)."""
    packed = _encode_ppmd7(_CONTENT)
    assert _decode_ppmd7_raw(packed, len(_CONTENT)) == _CONTENT


def test_raw_pyppmd8_roundtrip() -> None:
    """Bare ``Ppmd8Encoder``/``Ppmd8Decoder`` with end-mark flush."""
    import pyppmd

    packed = _encode_ppmd8(_CONTENT)
    dec = pyppmd.Ppmd8Decoder(_ORDER, _MEM, 0)
    out = dec.decode(packed, -1)
    while not dec.eof:
        more = dec.decode(b"\0" if dec.needs_input else b"", -1)
        if not more:
            break
        out += more
    assert out == _CONTENT


def test_archivey_ppmd7_decompressor_stream_sized_read() -> None:
    """Archivey PPMd7 stream with an explicit uncompressed-size bound (no 7z)."""
    packed = _encode_ppmd7(_CONTENT)
    with PpmdDecompressorStream(
        io.BytesIO(packed), order=_ORDER, mem_size=_MEM, variant=7
    ) as stream:
        assert stream.read(len(_CONTENT)) == _CONTENT


def test_archivey_ppmd7_via_open_codec_stream_properties() -> None:
    """``open_codec_stream(Codec.PPMD)`` with 7z var.H properties blob."""
    packed = _encode_ppmd7(_CONTENT)
    props = struct.pack("<BL", _ORDER, _MEM)
    with open_codec_stream(
        Codec.PPMD, io.BytesIO(packed), params=CodecParams(properties=props)
    ) as stream:
        # PPMd7 has no end mark; stop at the known size (container would slice).
        assert stream.read(len(_CONTENT)) == _CONTENT


def test_archivey_ppmd8_via_open_codec_stream() -> None:
    """``open_codec_stream(Codec.PPMD)`` ZIP/PPMd8 params — end-marked, read-to-EOF."""
    packed = _encode_ppmd8(_CONTENT)
    with open_codec_stream(
        Codec.PPMD,
        io.BytesIO(packed),
        params=CodecParams(ppmd_order=_ORDER, ppmd_mem_size=_MEM),
    ) as stream:
        assert stream.read() == _CONTENT


def test_archivey_ppmd7_repeated_construct_destroy() -> None:
    """Create/destroy many PPMd7 streams in one process (minimal reuse surface)."""
    for _ in range(25):
        packed = _encode_ppmd7(_CONTENT)
        with PpmdDecompressorStream(
            io.BytesIO(packed), order=_ORDER, mem_size=_MEM, variant=7
        ) as stream:
            assert stream.read(len(_CONTENT)) == _CONTENT


def test_archivey_ppmd8_repeated_construct_destroy() -> None:
    for _ in range(25):
        packed = _encode_ppmd8(_CONTENT)
        with open_codec_stream(
            Codec.PPMD,
            io.BytesIO(packed),
            params=CodecParams(ppmd_order=_ORDER, ppmd_mem_size=_MEM),
        ) as stream:
            assert stream.read() == _CONTENT
