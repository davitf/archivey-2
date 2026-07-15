"""Reproductions for Brief 3 (seekable decoder layer) findings.

Run from the repo root:

    uv run python review/next/03-stream-decoder-findings/repro.py

Findings 1, 3 and 4 need only the stdlib (they reproduce in every dependency
config, including ``[core-only]``). Finding 2 needs the ``[seekable]`` extra
(``rapidgzip``); it is skipped with a note when rapidgzip is absent. Finding 3's
fixture uses ``ncompress`` (a test-only LZW *compressor*) when available; if it is
absent the finding is demonstrated with a minimal in-tree decoder stand-in that
mirrors ``UnixCompressDecoder``'s deferred-truncation contract.
"""

from __future__ import annotations

import io
import lzma
import os
import sys

sys.path.insert(0, "src")

from archivey.exceptions import TruncatedError  # noqa: E402
from archivey.internal.streams.decompressor_stream import (  # noqa: E402
    BaseDecoder,
    DecodeOut,
    DecompressorStream,
)
from archivey.internal.streams.unix_compress import (  # noqa: E402
    UnixCompressDecompressorStream,
    _parse_header,
)
from archivey.internal.streams.xz import XzDecompressorStream  # noqa: E402


def _hr(title: str) -> None:
    print("\n" + "=" * 72 + f"\n{title}\n" + "=" * 72)


def finding1_xz_collision_assert() -> None:
    _hr("F1 (HIGH): multi-stream .xz seek-point collision -> AssertionError")
    part0, part1 = b"A" * 5000, b"B" * 5000
    data = b"".join(lzma.compress(p, format=lzma.FORMAT_XZ) for p in (part0, part1))
    # The common "size then read" access pattern on ONE stream instance:
    stream = XzDecompressorStream(io.BytesIO(data), seekable=True)
    stream.seek(0, io.SEEK_END)  # build_index emits first-block points (state=block)
    stream.seek(0)
    try:
        stream.read()  # forward pass emits stream-start points (state=None) -> collision
        print("  no crash (running under python -O? the assert is compiled out)")
    except AssertionError as exc:
        print(f"  AssertionError raised on a VALID 2-stream .xz: {str(exc)[:70]}...")
    finally:
        stream.close()


def finding2_accelerator_truncation() -> None:
    _hr("F2 (HIGH): rapidgzip swallows truncation that stdlib raises (deflate)")
    try:
        import rapidgzip  # noqa: F401
    except ImportError:
        print("  SKIP: rapidgzip not installed ([seekable] extra); stdlib path is safe.")
        return
    import tempfile
    import zlib

    from archivey.config import AcceleratorMode
    from archivey.internal.config import StreamConfig
    from archivey.internal.streams.codecs import Codec, open_codec_stream

    payload = os.urandom(2_000_000)  # >1 MiB so the AUTO gate picks rapidgzip
    co = zlib.compressobj(9, zlib.DEFLATED, -15)
    deflate = co.compress(payload) + co.flush()
    truncated = deflate[: len(deflate) // 2]

    on = StreamConfig(
        use_rapidgzip=AcceleratorMode.ON, seekable=True,
        compressed_input_size=len(deflate),
    )
    off = StreamConfig(
        use_rapidgzip=AcceleratorMode.OFF, seekable=True,
        compressed_input_size=len(deflate),
    )
    d = tempfile.mkdtemp()
    p = os.path.join(d, "x.deflate")
    with open(p, "wb") as f:
        f.write(truncated)

    try:
        with open_codec_stream(Codec.DEFLATE, p, config=on) as s:
            out = s.read()
        print(f"  rapidgzip: returned {len(out)} bytes, NO error (truncation SWALLOWED)")
    except TruncatedError:
        print("  rapidgzip: TruncatedError raised")

    try:
        with open_codec_stream(Codec.DEFLATE, io.BytesIO(truncated), config=off) as s:
            s.read()
        print("  stdlib:    NO error (unexpected)")
    except TruncatedError:
        print("  stdlib:    TruncatedError raised (this is the correct behaviour)")


class _TruncGuineaDecoder(BaseDecoder):
    """Stand-in mirroring UnixCompressDecoder: finished at flush, deferred truncation."""

    def __init__(self) -> None:
        self._done = False

    def recreate(self, point, inner):  # noqa: ANN001
        return _TruncGuineaDecoder()

    def feed(self, chunk: bytes) -> DecodeOut:
        return DecodeOut(chunk)

    def flush(self) -> DecodeOut:
        self._done = True
        self._pending_error = TruncatedError("deferred truncation")
        return DecodeOut(b"")

    @property
    def finished(self) -> bool:
        return self._done


def finding3_readall_swallows_pending_error() -> None:
    _hr("F3 (MED): read(-1)/readall never checks pending_error (truncated .Z)")

    # Real .Z fixture via ncompress when available; else the stand-in decoder.
    real_z: bytes | None = None
    try:
        import ncompress  # noqa: F401

        payload = bytes(i % 251 for i in range(4000))
        buf = io.BytesIO()
        ncompress.compress(io.BytesIO(payload), buf)
        real_z = buf.getvalue()
    except Exception:  # noqa: BLE001
        pass

    if real_z is not None:
        truncated = real_z[:-6]  # a cut that leaves nonzero leftover bits
        with UnixCompressDecompressorStream(io.BytesIO(truncated), seekable=False) as s:
            out = s.read()  # f.read() idiom
            print(f"  real .Z read(-1): {len(out)} bytes, NO error (SWALLOWED)")
        with UnixCompressDecompressorStream(io.BytesIO(truncated), seekable=False) as s:
            try:
                buf2 = bytearray()
                while True:
                    c = s.read(256)
                    if not c:
                        break
                    buf2 += c
                print(f"  real .Z chunked: {len(buf2)} bytes, NO error")
            except TruncatedError:
                print("  real .Z chunked: TruncatedError raised (inconsistent with above)")
    else:
        print("  (ncompress absent — using in-tree stand-in decoder)")

    data = b"hello world payload"

    def mk() -> DecompressorStream:
        return DecompressorStream(
            io.BytesIO(data), make_decoder=lambda p, i: _TruncGuineaDecoder(),
            seekable=False,
        )

    with mk() as s:
        out = s.read()  # readall path
        print(f"  stand-in read(-1): {out!r} -> NO error (pending TruncatedError SWALLOWED)")
    with mk() as s:
        try:
            while s.read(4):
                pass
            print("  stand-in chunked: NO error")
        except TruncatedError:
            print("  stand-in chunked: TruncatedError raised (correct)")


def finding4_lzw_maxbits_unbounded() -> None:
    _hr("F4 (LOW-MED): .Z maxbits accepted up to 31 (spec caps at 16)")
    for mb in (16, 17, 24, 31):
        flag = 0x80 | mb
        mw, _ = _parse_header(bytes([0x1F, 0x9D, flag]))
        note = "" if mb <= 16 else "  <-- out of spec, accepted"
        print(f"  maxbits={mb:>2}: accepted max_width={mw}{note}")


if __name__ == "__main__":
    # F1 aborts the process under default python (AssertionError propagates), so run it
    # in a child-safe order: everything else first, F1 last.
    finding2_accelerator_truncation()
    finding3_readall_swallows_pending_error()
    finding4_lzw_maxbits_unbounded()
    finding1_xz_collision_assert()
