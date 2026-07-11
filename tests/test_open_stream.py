"""Public ``open_stream``: forward-only by default, seekable on demand."""

from __future__ import annotations

import gzip
import io
import lzma
from pathlib import Path

import pytest

from archivey import (
    ArchiveFormat,
    ArchiveStream,
    ArchiveyUsageError,
    StreamFormat,
    UnsupportedFormatError,
    open_stream,
)

CONTENT = b"the quick brown fox jumps over the lazy dog\n" * 50


def test_open_stream_default_is_forward_only() -> None:
    compressed = gzip.compress(CONTENT)
    with open_stream(io.BytesIO(compressed)) as stream:
        assert isinstance(stream, ArchiveStream)
        assert stream.seekable() is False
        with pytest.raises(io.UnsupportedOperation):
            stream.seek(0)
        assert stream.tell() == 0
        assert stream.read() == CONTENT


def test_open_stream_seekable_true_allows_seek() -> None:
    compressed = gzip.compress(CONTENT)
    with open_stream(io.BytesIO(compressed), seekable=True) as stream:
        assert stream.seekable() is True
        assert stream.read(10) == CONTENT[:10]
        assert stream.seek(0) == 0
        assert stream.read(10) == CONTENT[:10]


def test_open_stream_explicit_format() -> None:
    compressed = gzip.compress(CONTENT)
    with open_stream(
        io.BytesIO(compressed), format=StreamFormat.GZIP
    ) as stream:
        assert stream.read() == CONTENT


def test_open_stream_archive_format_raw_stream() -> None:
    compressed = gzip.compress(CONTENT)
    with open_stream(io.BytesIO(compressed), format=ArchiveFormat.GZ) as stream:
        assert stream.read() == CONTENT


def test_open_stream_rejects_container_format() -> None:
    with pytest.raises(ArchiveyUsageError, match="container format"):
        open_stream(io.BytesIO(b"PK"), format=ArchiveFormat.ZIP)


def test_open_stream_rejects_detected_container(tmp_path: Path) -> None:
    import zipfile

    path = tmp_path / "a.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("a.txt", "hi")
    with pytest.raises(UnsupportedFormatError, match="not a single-file"):
        open_stream(path)


def test_open_stream_path_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "data.gz"
    path.write_bytes(gzip.compress(CONTENT))
    with open_stream(path) as stream:
        assert stream.read() == CONTENT


def test_open_stream_xz_default_builds_no_index() -> None:
    """Without seekable=True, XZ does not expose a cheap index-derived size."""
    compressed = lzma.compress(CONTENT, format=lzma.FORMAT_XZ)
    with open_stream(io.BytesIO(compressed)) as stream:
        assert stream.seekable() is False
        assert stream.size is None
        assert stream.read() == CONTENT


def test_open_stream_xz_seekable_exposes_size() -> None:
    compressed = lzma.compress(CONTENT, format=lzma.FORMAT_XZ)
    with open_stream(io.BytesIO(compressed), seekable=True) as stream:
        assert stream.seekable() is True
        assert stream.size == len(CONTENT)
        assert stream.seek(10) == 10
        assert stream.read(5) == CONTENT[10:15]
