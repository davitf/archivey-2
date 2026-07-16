"""Raw PPMd stream coverage — no 7z/ZIP container.

These tests exercise ``pyppmd`` and archivey's PPMd codec/stream adapters directly so
Windows/Linux native aborts can be chased on the *minimal* surface (see
``docs/internal/known-issues.md`` and ``scripts/ppmd_native_stress.py``).

Notes:
- In-process PPMd7 create/destroy loops are skipped on Windows (have aborted CI with
  ``STATUS_HEAP_CORRUPTION`` / access violation); that axis lives in the stress job.
- PPMd7 streams require ``unpack_size`` (7z always passes the folder size): the format
  has no end mark, and on pyppmd 1.3.x any decode request materially past the true
  payload corrupts the heap — the exact remaining size (py7zr-style) is the safe
  contract. Unsized PPMd8 is allowed (end-marked) and never passes ``-1`` (bounded
  drain loop).
"""

from __future__ import annotations

import io
import struct
import sys

import pytest

from archivey.exceptions import ArchiveyError, TruncatedError
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


def test_ppmd_decoder_skips_unbounded_after_eof() -> None:
    """After unpack_size is met, further feed/flush must not use decode(..., -1)."""
    payload = b"hello world"
    packed = _encode_ppmd7(payload)
    dec = PpmdDecoder(order=_ORDER, mem_size=_MEM, variant=7, unpack_size=len(payload))
    out = dec.feed(packed).data
    out += dec.flush().data
    assert out == payload
    assert dec.finished
    # max_length is 0 once produced >= unpack_size — no native after-eof -1.
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


# ---------------------------------------------------------------------------
# Adversarial-input regression tests (pyppmd 1.3.x native aborts)
#
# pyppmd 1.3.x heap corruption fires when the native decoder runs past the true
# end of stream (unbounded budget); see docs/internal/known-issues.md and
# docs/internal/pyppmd-upstream-report.md. These tests pin the shapes damaged or
# hostile archives can force — truncation, early close, garbage tails — which
# must fail cleanly (archivey exception or bounded output) and never abort the
# process. Subprocess-isolated soak versions of the same shapes live in
# ``scripts/pyppmd_crash_repro.py`` (modes ``underfed-sized`` / ``hostile-tail``).
# ---------------------------------------------------------------------------

_skip_win32_unfinished_teardown = pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "Tears down a mid-stream PPMd7 decoder in-process; unfinished-decoder "
        "teardown has aborted Windows CI — the PPMd native stress workflow "
        "covers this axis in isolated children"
    ),
)


@_skip_win32_unfinished_teardown
def test_archivey_ppmd7_truncated_input_raises_truncated_error() -> None:
    """Truncated member: flush must not fabricate the missing output.

    ``PpmdDecoder.flush`` injects at most the one documented extra NUL; a stream
    cut mid-payload must surface as ``TruncatedError``, not silently complete.
    """
    packed = _encode_ppmd7(_CONTENT)
    with PpmdDecompressorStream(
        io.BytesIO(packed[: len(packed) // 2]),
        order=_ORDER,
        mem_size=_MEM,
        variant=7,
        unpack_size=len(_CONTENT),
    ) as stream:
        with pytest.raises(TruncatedError):
            stream.read()


@_skip_win32_unfinished_teardown
def test_ppmd_decoder_truncated_flush_reports_unfinished() -> None:
    """Decoder-level truncation: single-NUL flush leaves ``finished`` False."""
    packed = _encode_ppmd7(_CONTENT)
    dec = PpmdDecoder(order=_ORDER, mem_size=_MEM, variant=7, unpack_size=len(_CONTENT))
    out = dec.feed(packed[: len(packed) // 2]).data
    out += dec.flush().data
    assert len(out) < len(_CONTENT)
    assert not dec.finished


@_skip_win32_unfinished_teardown
def test_archivey_ppmd7_early_close_partial_read() -> None:
    """Closing mid-member (native decoder mid-stream) must not abort or raise."""
    packed = _encode_ppmd7(_CONTENT)
    for _ in range(5):
        stream = PpmdDecompressorStream(
            io.BytesIO(packed),
            order=_ORDER,
            mem_size=_MEM,
            variant=7,
            unpack_size=len(_CONTENT),
        )
        assert read_exact(stream, 16) == _CONTENT[:16]
        stream.close()


@_skip_win32_unfinished_teardown
def test_archivey_ppmd7_hostile_tail_stays_bounded() -> None:
    """Inflated declared size + garbage tail: decode stays bounded, fails cleanly.

    Models a hostile 7z header whose folder ``unpack_size`` exceeds the true
    payload while the packed stream carries trailing garbage. Bytes past the
    true payload are undefined (garbage symbols), but output must never exceed
    the declared size, the true payload prefix must be intact, and any failure
    must surface as an archivey error — never a native abort.
    """
    payload = b"alpha\n" * 100
    packed = _encode_ppmd7(payload) + bytes(range(64))
    claimed = len(payload) + 64
    props = struct.pack("<BL", _ORDER, _MEM)
    with open_codec_stream(
        Codec.PPMD,
        io.BytesIO(packed),
        params=CodecParams(properties=props, unpack_size=claimed),
    ) as stream:
        try:
            data = stream.read()
        except ArchiveyError:
            return
        assert len(data) <= claimed
        assert data[: len(payload)] == payload


class _MaxLengthSpy:
    """Wraps a pyppmd decoder, recording every ``max_length`` passed to ``decode``."""

    def __init__(self, inner: object) -> None:
        self._inner = inner
        self.lengths: list[int] = []

    def decode(self, data: bytes, length: int) -> bytes:
        self.lengths.append(length)
        return self._inner.decode(data, length)  # type: ignore[attr-defined]

    @property
    def eof(self) -> bool:
        return self._inner.eof  # type: ignore[attr-defined]

    @property
    def needs_input(self) -> bool:
        return self._inner.needs_input  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("variant", "sized"),
    [(7, True), (8, True), (8, False)],
    ids=["ppmd7-sized", "ppmd8-sized", "ppmd8-unsized"],
)
def test_ppmd_decoder_never_passes_unbounded_max_length(
    variant: int, sized: bool
) -> None:
    """No decode call may reach pyppmd with ``max_length=-1``.

    ``-1`` grants the pyppmd 1.3.x native worker an effectively unlimited symbol
    budget — the heap-corruption path. Sized decodes pass the remaining
    ``unpack_size``; unsized PPMd8 must use the bounded drain loop instead.
    """
    packed = _encode_ppmd7(_CONTENT) if variant == 7 else _encode_ppmd8(_CONTENT)
    dec = PpmdDecoder(
        order=_ORDER,
        mem_size=_MEM,
        variant=variant,
        unpack_size=len(_CONTENT) if sized else None,
    )
    spy = _MaxLengthSpy(dec._decomp)
    dec._decomp = spy
    out = dec.feed(packed).data
    out += dec.flush().data
    assert out[: len(_CONTENT)] == _CONTENT
    assert spy.lengths, "expected at least one native decode call"
    assert all(length >= 0 for length in spy.lengths), spy.lengths


def test_archivey_ppmd7_requires_unpack_size() -> None:
    """Unsized PPMd7 is rejected outright — there is no safe decode request for it.

    PPMd7 has no end mark, and on pyppmd 1.3.x any decode request materially past
    the true remaining output corrupts the heap (measured: requests ≥64 KiB beyond
    the payload crash even without ``-1``). With no declared size there is no
    correct output boundary either, so construction fails fast instead.
    """
    with pytest.raises(ValueError, match="unpack_size"):
        PpmdDecompressorStream(
            io.BytesIO(b""),
            order=_ORDER,
            mem_size=_MEM,
            variant=7,
        )
