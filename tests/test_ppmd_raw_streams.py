"""Raw PPMd stream coverage — no 7z/ZIP container.

These tests exercise ``pyppmd`` and archivey's PPMd codec/stream adapters directly so
Windows/Linux native aborts can be chased on the *minimal* surface (see
``docs/internal/known-issues.md`` and ``scripts/ppmd_native_stress.py``).

Notes:
- In-process PPMd7 create/destroy loops are skipped on Windows (have aborted CI with
  ``STATUS_HEAP_CORRUPTION`` / access violation); that axis lives in the stress job.
- Prefer ``unpack_size`` on archivey PPMd7 streams (7z always passes folder size). That
  bounds ``decode`` ``max_length`` like py7zr and avoids ``decode(..., -1)`` overshoot.
  Unsized PPMd7 ``read()`` remains best-effort and may overshoot on some pyppmd versions.
"""

from __future__ import annotations

import io
import struct
import sys

import pytest

from archivey.internal.streams.codecs import Codec, CodecParams, open_codec_stream
from archivey.internal.streams.decompress import PpmdDecoder, PpmdDecompressorStream
from archivey.internal.streams.streamtools import read_exact
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
        io.BytesIO(packed),
        order=_ORDER,
        mem_size=_MEM,
        variant=7,
        unpack_size=len(_CONTENT),
    ) as stream:
        # DecompressorStream may return short chunks; containers use read_exact.
        assert read_exact(stream, len(_CONTENT)) == _CONTENT


def test_archivey_ppmd7_via_open_codec_stream_properties() -> None:
    """``open_codec_stream(Codec.PPMD)`` with 7z var.H properties blob."""
    packed = _encode_ppmd7(_CONTENT)
    props = struct.pack("<BL", _ORDER, _MEM)
    with open_codec_stream(
        Codec.PPMD,
        io.BytesIO(packed),
        params=CodecParams(properties=props, unpack_size=len(_CONTENT)),
    ) as stream:
        assert read_exact(stream, len(_CONTENT)) == _CONTENT


def test_archivey_ppmd7_trailing_null_payload() -> None:
    """Trailing NUL plaintext: sized decode must not overshoot (pyppmd extra-byte note).

    pyppmd documents that the encoder may omit a final null and the decoder may need
    an extra NUL input when output is short. With ``unpack_size``, archivey passes
    remaining length as ``max_length`` (py7zr-style) so ``decode(..., -1)`` cannot
    invent bytes past the member.
    """
    payload = b"hello world" + b"\0"
    packed = _encode_ppmd7(payload)
    with PpmdDecompressorStream(
        io.BytesIO(packed),
        order=_ORDER,
        mem_size=_MEM,
        variant=7,
        unpack_size=len(payload),
    ) as stream:
        assert read_exact(stream, len(payload)) == payload


def test_archivey_ppmd7_unpack_size_prevents_overshoot() -> None:
    """``unpack_size`` keeps PPMd7 output within the known member/folder length.

    Unsized ``decode(..., -1)`` can overshoot and intermittently abort inside
    ``pyppmd`` 1.3.1 — do not exercise that path in-process here; see
    ``scripts/pyppmd_crash_repro.py --mode overshoot``.
    """
    payload = b"alpha\n" * 100
    packed = _encode_ppmd7(payload)
    with PpmdDecompressorStream(
        io.BytesIO(packed),
        order=_ORDER,
        mem_size=_MEM,
        variant=7,
        unpack_size=len(payload),
    ) as stream:
        assert read_exact(stream, len(payload)) == payload
    # Decoder reports finished at unpack_size; further reads are empty.
    with PpmdDecompressorStream(
        io.BytesIO(packed),
        order=_ORDER,
        mem_size=_MEM,
        variant=7,
        unpack_size=len(payload),
    ) as stream:
        assert read_exact(stream, len(payload)) == payload
        assert stream.read(1) == b""


def test_ppmd_decoder_extra_null_flush_respects_remaining() -> None:
    """Flush feeds at most remaining unpack_size bytes when needs_input (PyPI sample)."""
    payload = b"x" * 50 + b"\0"
    packed = _encode_ppmd7(payload)
    dec = PpmdDecoder(order=_ORDER, mem_size=_MEM, variant=7, unpack_size=len(payload))
    out = dec.feed(packed).data
    out += dec.flush().data
    assert out == payload
    assert dec.finished
    # Further flush must not invent more bytes past unpack_size.
    assert dec.flush().data == b""


def test_ppmd_decoder_skips_native_calls_after_eof() -> None:
    """After native EOF, feed/flush must not call decode again (pyppmd 1.3.1 abort)."""
    payload = b"hello world"
    packed = _encode_ppmd7(payload)
    dec = PpmdDecoder(order=_ORDER, mem_size=_MEM, variant=7, unpack_size=len(payload))
    out = dec.feed(packed).data
    out += dec.flush().data
    assert out == payload
    assert dec.finished
    assert dec._decomp.eof
    # Further feed/flush must be no-ops (would be crashy if forwarded as decode -1).
    assert dec.feed(b"\0\0\0").data == b""
    assert dec.flush().data == b""
    assert dec.feed(b"").data == b""


def test_archivey_ppmd8_via_open_codec_stream() -> None:
    """``open_codec_stream(Codec.PPMD)`` ZIP/PPMd8 params — end-marked, read-to-EOF."""
    packed = _encode_ppmd8(_CONTENT)
    with open_codec_stream(
        Codec.PPMD,
        io.BytesIO(packed),
        params=CodecParams(ppmd_order=_ORDER, ppmd_mem_size=_MEM),
    ) as stream:
        assert stream.read() == _CONTENT


def test_archivey_ppmd8_repeated_construct_destroy() -> None:
    """PPMd8 create/destroy cycles (end-marked; safer than PPMd7 on Windows)."""
    for _ in range(25):
        packed = _encode_ppmd8(_CONTENT)
        with open_codec_stream(
            Codec.PPMD,
            io.BytesIO(packed),
            params=CodecParams(ppmd_order=_ORDER, ppmd_mem_size=_MEM),
        ) as stream:
            assert stream.read() == _CONTENT


@pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "In-process PPMd7 create/destroy has aborted Windows CI with "
        "STATUS_HEAP_CORRUPTION / access violation; covered by PPMd native stress"
    ),
)
def test_archivey_ppmd7_repeated_construct_destroy() -> None:
    """Create/destroy many PPMd7 streams in one process (Linux/macOS only)."""
    for _ in range(25):
        packed = _encode_ppmd7(_CONTENT)
        with PpmdDecompressorStream(
            io.BytesIO(packed),
            order=_ORDER,
            mem_size=_MEM,
            variant=7,
            unpack_size=len(_CONTENT),
        ) as stream:
            assert read_exact(stream, len(_CONTENT)) == _CONTENT
