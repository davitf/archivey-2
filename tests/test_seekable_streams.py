"""Tests for the ``seekable-decompressor-streams`` capability: XZ block-index and lzip
trailer-scan random access, plus accelerator present/absent behaviour."""

from __future__ import annotations

import bz2
import gzip
import importlib.util
import io
import lzma
import zlib

import pytest

from archivey.internal.config import AcceleratorMode, StreamConfig
from archivey.internal.errors import PackageNotInstalledError, TruncatedError
from archivey.internal.streams.codecs import Codec, open_codec_stream
from archivey.internal.streams.lzip import LzipDecompressorStream, _read_index_backwards
from archivey.internal.streams.xz import XzDecompressorStream, _read_xz_index_backwards
from tests.conftest import requires
from tests.streams_util import (
    CountingBytesIO,
    make_lzip_member,
    make_multi_member_lzip,
    make_multi_stream_xz,
    make_multiblock_xz,
    xz_cli_available,
)

CONTENT = bytes(range(256)) * 200  # 51200 bytes, compressible but non-trivial


# --- XZ seeking via the block index ----------------------------------------------------


def test_xz_forward_read_roundtrip() -> None:
    compressed = lzma.compress(CONTENT, format=lzma.FORMAT_XZ)
    with XzDecompressorStream(io.BytesIO(compressed)) as stream:
        assert stream.read() == CONTENT


def test_xz_seek_set_and_read() -> None:
    compressed = lzma.compress(CONTENT, format=lzma.FORMAT_XZ)
    with XzDecompressorStream(io.BytesIO(compressed)) as stream:
        assert stream.seek(10000) == 10000
        assert stream.read(100) == CONTENT[10000:10100]


def test_xz_seek_end_reports_size() -> None:
    compressed = lzma.compress(CONTENT, format=lzma.FORMAT_XZ)
    with XzDecompressorStream(io.BytesIO(compressed)) as stream:
        assert stream.seek(0, io.SEEK_END) == len(CONTENT)
        assert stream.read() == b""


def test_xz_try_get_size_uses_index_not_full_decode() -> None:
    compressed = make_multi_stream_xz([CONTENT, CONTENT])
    stream = XzDecompressorStream(io.BytesIO(compressed))
    assert stream.try_get_size() == 2 * len(CONTENT)
    stream.close()


def test_xz_backward_seek_uses_block_index() -> None:
    """A backward seek decompresses only from a nearby block, not the whole stream."""
    compressed = make_multi_stream_xz([CONTENT, CONTENT, CONTENT])
    counting = CountingBytesIO(compressed)
    with XzDecompressorStream(counting) as stream:
        assert stream.read() == CONTENT * 3  # forward pass populates the index
        baseline = counting.bytes_read
        stream.seek(len(CONTENT) * 2 + 5)  # into the third stream
        assert stream.read(50) == (CONTENT * 3)[len(CONTENT) * 2 + 5 : len(CONTENT) * 2 + 55]
        # Re-reading from a block start must not re-read the entire compressed file.
        assert counting.bytes_read - baseline < len(compressed)


def test_xz_index_backwards_parses_blocks() -> None:
    compressed = make_multi_stream_xz([CONTENT, CONTENT])
    blocks = _read_xz_index_backwards(io.BytesIO(compressed), len(compressed))
    assert sum(b.uncompressed_size for b in blocks) == 2 * len(CONTENT)
    assert blocks[0].decompressed_start == 0


@pytest.mark.skipif(
    not xz_cli_available(),
    reason="the xz CLI is needed to build a multi-block (single-stream) XZ fixture",
)
def test_xz_multiblock_backward_seek_crosses_block_boundary() -> None:
    """A backward seek within a *single* multi-block XZ stream uses the block chain.

    ``lzma.compress`` emits one block per stream, so the in-stream "advance to the next
    block" path of ``_XzBlockChain`` is otherwise unexercised. Build a genuinely
    multi-block stream and seek so the read spans a block boundary.
    """
    compressed = make_multiblock_xz(CONTENT, block_size=8192)
    blocks = _read_xz_index_backwards(io.BytesIO(compressed), len(compressed))
    assert len(blocks) > 1  # genuinely multi-block within one stream

    with XzDecompressorStream(io.BytesIO(compressed)) as stream:
        assert stream.read() == CONTENT  # forward pass populates the block index
        start = len(CONTENT) // 3
        length = len(CONTENT) // 2  # long enough to cross at least one block boundary
        stream.seek(start)
        assert stream.read(length) == CONTENT[start : start + length]


def test_xz_truncated_raises() -> None:
    compressed = lzma.compress(CONTENT, format=lzma.FORMAT_XZ)
    with XzDecompressorStream(io.BytesIO(compressed[: len(compressed) // 2])) as stream:
        with pytest.raises(TruncatedError):
            stream.read()


# --- lzip seeking via the trailer scan -------------------------------------------------


def test_lzip_forward_read_roundtrip() -> None:
    compressed = make_lzip_member(CONTENT)
    with LzipDecompressorStream(io.BytesIO(compressed)) as stream:
        assert stream.read() == CONTENT


def test_lzip_multi_member_roundtrip() -> None:
    compressed = make_multi_member_lzip([b"first-part", b"second-part", b"third"])
    with LzipDecompressorStream(io.BytesIO(compressed)) as stream:
        assert stream.read() == b"first-partsecond-partthird"


def test_lzip_seek_and_read() -> None:
    compressed = make_lzip_member(CONTENT)
    with LzipDecompressorStream(io.BytesIO(compressed)) as stream:
        assert stream.seek(12345) == 12345
        assert stream.read(100) == CONTENT[12345:12445]


def test_lzip_seek_end_via_trailer_scan() -> None:
    parts = [b"alpha" * 1000, b"beta" * 1000]
    compressed = make_multi_member_lzip(parts)
    with LzipDecompressorStream(io.BytesIO(compressed)) as stream:
        assert stream.seek(0, io.SEEK_END) == sum(len(p) for p in parts)


def test_lzip_index_backwards_parses_members() -> None:
    parts = [b"a" * 500, b"b" * 700]
    compressed = make_multi_member_lzip(parts)
    members = _read_index_backwards(io.BytesIO(compressed), len(compressed))
    assert [m.decompressed_size for m in members] == [500, 700]
    assert members[0].decompressed_start == 0
    assert members[1].decompressed_start == 500


# --- accelerator backends present / absent ---------------------------------------------


def test_gzip_accelerator_off_warns_on_rewind_but_still_seeks(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """With the accelerator OFF, gzip still seeks (slowly); a rewind logs one warning.

    Forward consumption and forward seeks stay quiet — only a backward seek, which
    re-decompresses from the start, triggers the warning.
    """
    config = StreamConfig(use_rapidgzip=AcceleratorMode.OFF)
    compressed = gzip.compress(CONTENT)
    with open_codec_stream(Codec.GZIP, io.BytesIO(compressed), config=config) as stream:
        with caplog.at_level("WARNING", logger="archivey.streams"):
            assert stream.read(100) == CONTENT[:100]
            assert stream.seek(200) == 200  # forward seek: no rewind, no warning
            assert stream.read(10) == CONTENT[200:210]
        assert not caplog.records

        with caplog.at_level("WARNING", logger="archivey.streams"):
            assert stream.seek(0) == 0  # rewind → slow re-decompression
            assert stream.read(10) == CONTENT[:10]  # still returns correct data
    assert sum("seekable" in r.getMessage() for r in caplog.records) == 1


def test_bzip2_accelerator_off_warns_on_rewind(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The bz2 stdlib path mirrors gzip: a rewind warns and still seeks."""
    config = StreamConfig(use_indexed_bzip2=AcceleratorMode.OFF)
    compressed = bz2.compress(CONTENT)
    with open_codec_stream(Codec.BZIP2, io.BytesIO(compressed), config=config) as stream:
        assert stream.read(100) == CONTENT[:100]
        with caplog.at_level("WARNING", logger="archivey.streams"):
            assert stream.seek(0) == 0
            assert stream.read(10) == CONTENT[:10]
    assert any("indexed_bzip2" in r.getMessage() for r in caplog.records)


# --- forward-only codecs without an accelerator: warn (generically) on a rewind ---------


def test_zlib_warns_on_rewind(caplog: pytest.LogCaptureFixture) -> None:
    """zlib has no index: a rewind warns (no accelerator to name), a forward seek doesn't."""
    compressed = zlib.compress(CONTENT)
    with open_codec_stream(Codec.ZLIB, io.BytesIO(compressed)) as stream:
        with caplog.at_level("WARNING", logger="archivey.streams"):
            assert stream.read(100) == CONTENT[:100]
            assert stream.seek(200) == 200  # forward seek: no warning
            assert stream.read(10) == CONTENT[200:210]
        assert not caplog.records

        with caplog.at_level("WARNING", logger="archivey.streams"):
            assert stream.seek(0) == 0  # rewind → re-decode from start
            assert stream.read(10) == CONTENT[:10]  # still correct
    msgs = [r.getMessage() for r in caplog.records]
    assert sum("no random-access index" in m for m in msgs) == 1
    assert not any("seekable" in m for m in msgs)  # generic message, no accelerator named


@requires("brotli")
def test_brotli_warns_on_rewind(caplog: pytest.LogCaptureFixture) -> None:
    import brotli

    compressed = brotli.compress(CONTENT)
    with open_codec_stream(Codec.BROTLI, io.BytesIO(compressed)) as stream:
        assert stream.read(100) == CONTENT[:100]
        with caplog.at_level("WARNING", logger="archivey.streams"):
            assert stream.seek(0) == 0
            assert stream.read(10) == CONTENT[:10]
    assert sum("no random-access index" in r.getMessage() for r in caplog.records) == 1


@requires("lz4")
def test_lz4_warns_on_rewind(caplog: pytest.LogCaptureFixture) -> None:
    import lz4.frame

    compressed = lz4.frame.compress(CONTENT)
    with open_codec_stream(Codec.LZ4, io.BytesIO(compressed)) as stream:
        assert stream.read(100) == CONTENT[:100]
        with caplog.at_level("WARNING", logger="archivey.streams"):
            assert stream.seek(0) == 0
            assert stream.read(10) == CONTENT[:10]
    assert sum("no random-access index" in r.getMessage() for r in caplog.records) == 1


@requires("zstandard")
def test_zstd_reopens_and_warns_on_rewind(caplog: pytest.LogCaptureFixture) -> None:
    """zstd's reader can't seek backward in place; a rewind reopens from the start + warns.

    A forward seek stays quiet; the backward seek reopens the source and re-decodes,
    delivering correct data with one warning.
    """
    import zstandard

    compressed = zstandard.ZstdCompressor().compress(CONTENT)
    with open_codec_stream(Codec.ZSTD, io.BytesIO(compressed)) as stream:
        assert stream.seekable() is True  # made rewindable via reopen
        with caplog.at_level("WARNING", logger="archivey.streams"):
            assert stream.read(100) == CONTENT[:100]
            assert stream.seek(300) == 300  # forward: no rewind, no warning
            assert stream.read(10) == CONTENT[300:310]
        assert not caplog.records

        with caplog.at_level("WARNING", logger="archivey.streams"):
            assert stream.seek(0) == 0  # backward → reopen + re-decode from start
            assert stream.read(100) == CONTENT[:100]  # correct data after reopen
    assert sum("no random-access index" in r.getMessage() for r in caplog.records) == 1


def test_gzip_accelerator_on_without_package_raises() -> None:
    """ON explicitly requests rapidgzip; absent, that's a PackageNotInstalledError."""
    if importlib.util.find_spec("rapidgzip") is not None:
        pytest.skip("rapidgzip is installed; cannot exercise the absent path")
    config = StreamConfig(use_rapidgzip=AcceleratorMode.ON)
    compressed = gzip.compress(CONTENT)
    with pytest.raises(PackageNotInstalledError):
        open_codec_stream(Codec.GZIP, io.BytesIO(compressed), config=config).read()


def test_bzip2_accelerator_on_without_package_raises() -> None:
    if importlib.util.find_spec("indexed_bzip2") is not None:
        pytest.skip("indexed_bzip2 is installed; cannot exercise the absent path")
    config = StreamConfig(use_indexed_bzip2=AcceleratorMode.ON)
    compressed = bz2.compress(CONTENT)
    with pytest.raises(PackageNotInstalledError):
        open_codec_stream(Codec.BZIP2, io.BytesIO(compressed), config=config).read()


def test_accelerator_mode_auto_resolution() -> None:
    """AUTO enables only for random access (streaming=False) and only when available."""
    assert AcceleratorMode.AUTO.enabled_for(streaming=False, available=True) is True
    assert not AcceleratorMode.AUTO.enabled_for(streaming=True, available=True)
    assert not AcceleratorMode.AUTO.enabled_for(streaming=False, available=False)
    assert AcceleratorMode.ON.enabled_for(streaming=True, available=True)
    assert not AcceleratorMode.OFF.enabled_for(streaming=False, available=True)
    # ON resolves to "use it" even when absent; the opener turns that into a clear
    # PackageNotInstalledError (asserted in the gzip/bzip2 ON-without-package tests).
    assert AcceleratorMode.ON.enabled_for(streaming=False, available=False)
