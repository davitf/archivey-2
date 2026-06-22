"""Single-file compressor backend tests — Stage 2.

Covers name inference, the one-member shape, the gzip stored-name surface, per-format
size rules, DIRECT/INDEXED cost, the unix-compress non-seekable rule, and the
password-rejection rule. ZST/LZ4 standalone are first-class here; only their
seekable-decompressor refinements remain for Phase 8.
"""

from __future__ import annotations

import bz2
import gzip
import io
import lzma
import zlib
from pathlib import Path

import pytest

from archivey import ArchiveFormat, MemberType, open_archive
from archivey.internal.cost import AccessCost, ListingCost
from archivey.internal.errors import StreamNotSeekableError, UnsupportedOperationError
from tests.conftest import requires
from tests.streams_util import NonSeekableBytesIO, make_lzip_member, make_unix_compress


def _gzip_bytes(payload: bytes, *, filename: str | None = None, mtime: int = 0) -> bytes:
    buf = io.BytesIO()
    gz = gzip.GzipFile(filename=filename or "", mode="wb", fileobj=buf, mtime=mtime)
    gz.write(payload)
    gz.close()
    return buf.getvalue()


# ---------------------------------------------------------------------------
# One backend, multiple formats; one-member shape
# ---------------------------------------------------------------------------


def test_one_backend_serves_multiple_formats() -> None:
    cases = {
        ArchiveFormat.GZ: gzip.compress(b"gzipped"),
        ArchiveFormat.BZ2: bz2.compress(b"bzipped"),
        ArchiveFormat.XZ: lzma.compress(b"xzipped"),
    }
    for expected_format, data in cases.items():
        with open_archive(io.BytesIO(data)) as ar:
            assert ar.format == expected_format
            members = ar.members()
            assert len(members) == 1
            assert members[0].type == MemberType.FILE


def test_exactly_one_member_no_directories() -> None:
    with open_archive(io.BytesIO(gzip.compress(b"x"))) as ar:
        members = list(ar)
        assert len(members) == 1
        assert members[0].is_file


def test_read_roundtrip() -> None:
    with open_archive(io.BytesIO(bz2.compress(b"hello bzip"))) as ar:
        assert ar.read(ar.members()[0]) == b"hello bzip"


# ---------------------------------------------------------------------------
# Member name inference
# ---------------------------------------------------------------------------


def test_name_strips_known_compression_extension(tmp_path: Path) -> None:
    path = tmp_path / "data.txt.gz"
    with gzip.open(path, "wb") as f:
        f.write(b"content")
    with open_archive(path) as ar:
        assert ar.members()[0].name == "data.txt"


def test_name_appends_uncompressed_for_unknown_extension(tmp_path: Path) -> None:
    # Identified by content (gzip magic), but ".bin" is not a compression extension, so the
    # extension is preserved and ".uncompressed" appended rather than discarding info.
    path = tmp_path / "mystery.bin"
    path.write_bytes(gzip.compress(b"content"))
    with open_archive(path) as ar:
        assert ar.members()[0].name == "mystery.bin.uncompressed"


def test_name_defaults_to_data_for_anonymous_stream() -> None:
    with open_archive(io.BytesIO(gzip.compress(b"x"))) as ar:
        assert ar.members()[0].name == "data"


# ---------------------------------------------------------------------------
# gzip stored filename + mtime
# ---------------------------------------------------------------------------


def test_gzip_stored_filename_surfaced(tmp_path: Path) -> None:
    path = tmp_path / "archive.gz"
    path.write_bytes(_gzip_bytes(b"payload", filename="report.csv"))
    with open_archive(path) as ar:
        member = ar.members()[0]
        # name comes from the source filename; the stored FNAME lives in extra + raw_name.
        assert member.name == "archive"
        assert member.extra["gzip.original_filename"] == "report.csv"
        assert member.raw_name == b"report.csv"


def test_gzip_without_stored_filename() -> None:
    # gzip.compress writes no FNAME -> no extra key, raw_name stays None.
    with open_archive(io.BytesIO(gzip.compress(b"x"))) as ar:
        member = ar.members()[0]
        assert "gzip.original_filename" not in member.extra
        assert member.raw_name is None


def test_gzip_mtime_surfaced() -> None:
    data = _gzip_bytes(b"payload", mtime=1_600_000_000)
    with open_archive(io.BytesIO(data)) as ar:
        member = ar.members()[0]
        assert member.modified is not None
        assert member.modified.tzinfo is not None
        assert int(member.modified.timestamp()) == 1_600_000_000


# ---------------------------------------------------------------------------
# Per-format size rules
# ---------------------------------------------------------------------------


def test_gz_size_is_always_none() -> None:
    with open_archive(io.BytesIO(gzip.compress(b"x" * 1000))) as ar:
        assert ar.members()[0].size is None


def test_bz2_size_none_before_full_read() -> None:
    with open_archive(io.BytesIO(bz2.compress(b"x" * 1000))) as ar:
        assert ar.members()[0].size is None


def test_zlib_size_none() -> None:
    with open_archive(io.BytesIO(zlib.compress(b"x" * 1000))) as ar:
        assert ar.members()[0].size is None


def test_xz_size_from_header(tmp_path: Path) -> None:
    path = tmp_path / "data.xz"
    with lzma.open(path, "wb") as f:
        f.write(b"x" * 1234)
    with open_archive(path) as ar:
        assert ar.members()[0].size == 1234


def test_lzip_size_from_trailer(tmp_path: Path) -> None:
    path = tmp_path / "data.lz"
    path.write_bytes(make_lzip_member(b"y" * 777))
    with open_archive(path) as ar:
        assert ar.members()[0].size == 777


# ---------------------------------------------------------------------------
# Cost
# ---------------------------------------------------------------------------


def test_cost_is_indexed_and_direct() -> None:
    with open_archive(io.BytesIO(gzip.compress(b"x"))) as ar:
        cost = ar.cost
        assert cost.listing_cost == ListingCost.INDEXED
        assert cost.access_cost == AccessCost.DIRECT


def test_archive_info() -> None:
    with open_archive(io.BytesIO(gzip.compress(b"x"))) as ar:
        info = ar.info
        assert info.member_count == 1
        assert info.is_solid is False
        assert info.is_encrypted is False


# ---------------------------------------------------------------------------
# Non-seekable behavior
# ---------------------------------------------------------------------------


def test_non_seekable_gzip_reads_fine() -> None:
    # Single-file formats (except .Z) read from a non-seekable source.
    data = gzip.compress(b"streamed payload")
    with open_archive(NonSeekableBytesIO(data)) as ar:
        assert ar.read(ar.members()[0]) == b"streamed payload"


@requires("uncompresspy", "ncompress")
def test_unix_compress_non_seekable_raises() -> None:
    data = make_unix_compress(b"lzw payload")
    with pytest.raises(StreamNotSeekableError):
        open_archive(NonSeekableBytesIO(data), format=ArchiveFormat.Z)


@requires("uncompresspy", "ncompress")
def test_unix_compress_seekable_reads(tmp_path: Path) -> None:
    path = tmp_path / "data.Z"
    path.write_bytes(make_unix_compress(b"lzw payload"))
    with open_archive(path) as ar:
        assert ar.format == ArchiveFormat.Z
        assert ar.read(ar.members()[0]) == b"lzw payload"


# ---------------------------------------------------------------------------
# Password rejection
# ---------------------------------------------------------------------------


def test_password_raises() -> None:
    with pytest.raises(UnsupportedOperationError):
        open_archive(io.BytesIO(gzip.compress(b"x")), password=b"secret")


# ---------------------------------------------------------------------------
# Brotli (magic-less, detected by content probe)
# ---------------------------------------------------------------------------


@requires("brotli")
def test_brotli_roundtrip() -> None:
    import brotli

    data = brotli.compress(b"brotli payload")
    with open_archive(io.BytesIO(data)) as ar:
        assert ar.format == ArchiveFormat.BROTLI
        assert ar.read(ar.members()[0]) == b"brotli payload"


# ---------------------------------------------------------------------------
# zstd / lz4 standalone (now first-class single-file formats)
# ---------------------------------------------------------------------------


@requires("zstandard")
def test_zstd_roundtrip(tmp_path: Path) -> None:
    import zstandard

    data = zstandard.ZstdCompressor().compress(b"zstd payload")
    with open_archive(io.BytesIO(data)) as ar:
        assert ar.format == ArchiveFormat.ZST
        assert ar.members()[0].name == "data"
        assert ar.read(ar.members()[0]) == b"zstd payload"

    path = tmp_path / "file.bin.zst"
    path.write_bytes(data)
    with open_archive(path) as ar:
        assert ar.members()[0].name == "file.bin"


@requires("lz4")
def test_lz4_roundtrip() -> None:
    import lz4.frame

    data = lz4.frame.compress(b"lz4 payload")
    with open_archive(io.BytesIO(data)) as ar:
        assert ar.format == ArchiveFormat.LZ4
        assert ar.read(ar.members()[0]) == b"lz4 payload"


# ---------------------------------------------------------------------------
# The backend uses the resolved format it is given (no re-inspection)
# ---------------------------------------------------------------------------


def test_explicit_format_bypasses_detection() -> None:
    # Forcing format=GZ routes straight to the gzip codec without re-detecting the source.
    data = gzip.compress(b"forced gzip")
    with open_archive(io.BytesIO(data), format=ArchiveFormat.GZ) as ar:
        assert ar.format == ArchiveFormat.GZ
        assert ar.read(ar.members()[0]) == b"forced gzip"
