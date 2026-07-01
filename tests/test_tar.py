"""TAR backend tests — random-access read, forward-only streaming on non-seekable
sources, PAX/GNU/ustar member mapping, cost, corrupt/truncated handling, and
``strict_eof`` end-of-archive verification."""

from __future__ import annotations

import io
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import pytest

from archivey import (
    ArchiveFormat,
    CompressionAlgorithm,
    CompressionMethod,
    MemberType,
    UnsupportedOperationError,
    open_archive,
)
from archivey.cost import AccessCost, ListingCost, StreamCapability
from archivey.exceptions import (
    CorruptionError,
    StreamNotSeekableError,
    TruncatedError,
)
from tests.conftest import requires_zstd, zstd_backend
from tests.streams_util import NonSeekableBytesIO

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _build_tar(mode: str = "w") -> bytes:
    """A small tar with a file, a nested file, a directory, and a symlink."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode=mode) as t:
        for name, data, mtime in [
            ("hello.txt", b"hello world", 1_600_000_000),
            ("dir/nested.txt", b"nested content", 1_600_000_100),
        ]:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o644
            info.mtime = mtime
            info.uid, info.gid = 1000, 1000
            info.uname, info.gname = "alice", "staff"
            t.addfile(info, io.BytesIO(data))
        d = tarfile.TarInfo("dir")
        d.type = tarfile.DIRTYPE
        d.mode = 0o755
        t.addfile(d)
        link = tarfile.TarInfo("link.txt")
        link.type = tarfile.SYMTYPE
        link.linkname = "hello.txt"
        t.addfile(link)
    return buf.getvalue()


def _tar_missing_eof_block() -> bytes:
    """Valid member data but only one of the two required EOF null blocks."""
    full = _build_tar()
    with tarfile.open(fileobj=io.BytesIO(full), mode="r:") as t:
        members = t.getmembers()
        last = members[-1]
        blocks = (last.size + 511) & ~511
        eof_start = last.offset_data + blocks
    return full[: eof_start + 512]


@pytest.fixture
def plain_tar(tmp_path: Path) -> Path:
    path = tmp_path / "simple.tar"
    path.write_bytes(_build_tar())
    return path


# ---------------------------------------------------------------------------
# Cost / format properties (access-mode-and-cost for TAR)
# ---------------------------------------------------------------------------


def test_plain_tar_cost(plain_tar: Path) -> None:
    with open_archive(plain_tar) as ar:
        assert ar.format == ArchiveFormat.TAR
        assert ar.cost.listing_cost == ListingCost.REQUIRES_SCANNING
        assert ar.cost.access_cost == AccessCost.DIRECT
        assert ar.cost.stream_capability == StreamCapability.SEEKABLE
        assert ar.info.is_solid is False
        assert ar.info.member_count is None  # no central directory: count needs a scan


@pytest.mark.parametrize(
    "mode,fmt",
    [
        ("w:gz", ArchiveFormat.TAR_GZ),
        ("w:bz2", ArchiveFormat.TAR_BZ2),
        ("w:xz", ArchiveFormat.TAR_XZ),
    ],
)
def test_compressed_tar_cost_and_read(mode: str, fmt: ArchiveFormat, tmp_path: Path) -> None:
    path = tmp_path / f"a.{mode.split(':')[1]}"
    path.write_bytes(_build_tar(mode))
    with open_archive(path) as ar:
        assert ar.format == fmt
        assert ar.cost.listing_cost == ListingCost.REQUIRES_DECOMPRESSION
        assert ar.cost.access_cost == AccessCost.SOLID
        assert ar.info.is_solid is True
        assert ar.read("hello.txt") == b"hello world"
        assert ar.read("dir/nested.txt") == b"nested content"


# ---------------------------------------------------------------------------
# Random-access read + listing
# ---------------------------------------------------------------------------


def test_members_listed(plain_tar: Path) -> None:
    with open_archive(plain_tar) as ar:
        names = {m.name for m in ar.members()}
        assert names == {"hello.txt", "dir/nested.txt", "dir/", "link.txt"}


def test_random_access_read_by_name(plain_tar: Path) -> None:
    with open_archive(plain_tar) as ar:
        assert ar.read("dir/nested.txt") == b"nested content"
        assert ar.read("hello.txt") == b"hello world"  # out of order still works
        assert ar.read("hello.txt") == b"hello world"  # re-read


def test_member_list_not_available_without_scan(plain_tar: Path) -> None:
    # TAR has no upfront index: get_members_if_available is None until a scan materializes it.
    with open_archive(plain_tar) as ar:
        assert ar.get_members_if_available() is None
        ar.members()  # forces the scan
        assert ar.get_members_if_available() is not None


def test_stream_members(plain_tar: Path) -> None:
    with open_archive(plain_tar) as ar:
        collected = {}
        for member, stream in ar.stream_members():
            collected[member.name] = stream.read() if stream is not None else None
        assert collected["hello.txt"] == b"hello world"
        assert collected["dir/"] is None  # directory has no data stream


# ---------------------------------------------------------------------------
# Member metadata mapping (ustar / GNU / PAX)
# ---------------------------------------------------------------------------


def test_member_metadata(plain_tar: Path) -> None:
    with open_archive(plain_tar) as ar:
        by_name = {m.name: m for m in ar.members()}
        f = by_name["hello.txt"]
        assert f.type == MemberType.FILE
        assert f.size == len(b"hello world")
        assert f.mode == 0o644
        assert f.uid == 1000 and f.gid == 1000
        assert f.uname == "alice" and f.gname == "staff"
        assert f.modified == datetime.fromtimestamp(1_600_000_000, tz=timezone.utc)
        assert f.modified.tzinfo is not None  # tz-aware UTC
        # tar stores members uncompressed; no encryption.
        assert f.compression == (CompressionMethod(algo=CompressionAlgorithm.STORED),)
        assert f.is_encrypted is False


def test_directory_and_symlink_types(plain_tar: Path) -> None:
    with open_archive(plain_tar) as ar:
        by_name = {m.name: m for m in ar.members()}
        assert by_name["dir/"].type == MemberType.DIRECTORY
        link = by_name["link.txt"]
        assert link.type == MemberType.SYMLINK
        assert link.link_target == "hello.txt"


def test_symlink_followed_on_read(plain_tar: Path) -> None:
    with open_archive(plain_tar) as ar:
        # Opening the symlink follows it to its target's data.
        assert ar.read("link.txt") == b"hello world"


def test_hardlink_type_mapping(tmp_path: Path) -> None:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as t:
        data = b"original"
        info = tarfile.TarInfo("orig.txt")
        info.size = len(data)
        t.addfile(info, io.BytesIO(data))
        hl = tarfile.TarInfo("hard.txt")
        hl.type = tarfile.LNKTYPE
        hl.linkname = "orig.txt"
        t.addfile(hl)
    path = tmp_path / "hl.tar"
    path.write_bytes(buf.getvalue())
    with open_archive(path) as ar:
        hard = {m.name: m for m in ar.members()}["hard.txt"]
        assert hard.type == MemberType.HARDLINK
        assert hard.link_target == "orig.txt"
        assert ar.read("hard.txt") == b"original"  # follows to the linked data


def test_pax_mtime_override(tmp_path: Path) -> None:
    # A PAX header carries a sub-second mtime; tarfile folds it into TarInfo.mtime, which
    # the backend surfaces (overriding the whole-second ustar field).
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as t:
        info = tarfile.TarInfo("p.txt")
        data = b"x"
        info.size = len(data)
        info.pax_headers["mtime"] = "1600000000.123456"
        t.addfile(info, io.BytesIO(data))
    path = tmp_path / "pax.tar"
    path.write_bytes(buf.getvalue())
    with open_archive(path) as ar:
        m = ar["p.txt"]
        assert m.modified is not None
        assert abs(m.modified.timestamp() - 1_600_000_000.123456) < 1e-3


def test_raw_name_preserved(tmp_path: Path) -> None:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as t:
        info = tarfile.TarInfo("café.txt")
        info.size = 1
        t.addfile(info, io.BytesIO(b"x"))
    path = tmp_path / "u.tar"
    path.write_bytes(buf.getvalue())
    with open_archive(path) as ar:
        m = ar["café.txt"]
        assert m.name == "café.txt"  # decoded name round-trips
        assert m.raw_name == "café.txt".encode("utf-8")  # verbatim stored bytes


def test_pax_atime_ctime(tmp_path: Path) -> None:
    # PAX access/creation times live only in pax_headers (tarfile does not fold them into
    # TarInfo like mtime); the backend surfaces them as accessed/created.
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as t:
        info = tarfile.TarInfo("p.txt")
        info.size = 1
        info.pax_headers["atime"] = "1600000100.5"
        info.pax_headers["ctime"] = "1600000200.25"
        t.addfile(info, io.BytesIO(b"x"))
    path = tmp_path / "pax_times.tar"
    path.write_bytes(buf.getvalue())
    with open_archive(path) as ar:
        m = ar["p.txt"]
        assert m.accessed is not None
        assert abs(m.accessed.timestamp() - 1_600_000_100.5) < 1e-3
        assert m.created is not None
        assert abs(m.created.timestamp() - 1_600_000_200.25) < 1e-3


# ---------------------------------------------------------------------------
# Compressed-tar combinations beyond gz/bz2/xz (codec-layer composition)
# ---------------------------------------------------------------------------


@requires_zstd()
def test_tar_zst_via_codec_layer() -> None:
    zstd = zstd_backend()
    plain = _build_tar()
    data = zstd.compress(plain)
    with open_archive(io.BytesIO(data)) as ar:
        assert ar.format == ArchiveFormat.TAR_ZST
        assert ar.read("hello.txt") == b"hello world"


# ---------------------------------------------------------------------------
# Non-seekable source: random-access fails fast; streaming succeeds
# ---------------------------------------------------------------------------


def test_non_seekable_tar_fails_fast() -> None:
    with pytest.raises(StreamNotSeekableError):
        open_archive(NonSeekableBytesIO(_build_tar()), format=ArchiveFormat.TAR)


def test_non_seekable_tar_fails_fast_via_detection() -> None:
    with pytest.raises(StreamNotSeekableError):
        open_archive(NonSeekableBytesIO(_build_tar()))


def test_non_seekable_tar_streaming_opens_without_scanning() -> None:
    with open_archive(
        NonSeekableBytesIO(_build_tar()), format=ArchiveFormat.TAR, streaming=True
    ) as ar:
        assert ar.cost.stream_capability == StreamCapability.FORWARD_ONLY


def test_non_seekable_plain_tar_stream_members() -> None:
    with open_archive(
        NonSeekableBytesIO(_build_tar()), format=ArchiveFormat.TAR, streaming=True
    ) as ar:
        collected = {}
        for member, stream in ar.stream_members():
            collected[member.name] = stream.read() if stream is not None else None
        assert collected["hello.txt"] == b"hello world"
        assert collected["dir/nested.txt"] == b"nested content"
        assert collected["dir/"] is None


def test_non_seekable_plain_tar_iter() -> None:
    with open_archive(
        NonSeekableBytesIO(_build_tar()), format=ArchiveFormat.TAR, streaming=True
    ) as ar:
        names = [m.name for m in ar]
        assert "hello.txt" in names
        assert "dir/nested.txt" in names


def test_streaming_tar_disables_random_access(plain_tar: Path) -> None:
    with open_archive(plain_tar, streaming=True) as ar:
        with pytest.raises(UnsupportedOperationError):
            ar.members()
        with pytest.raises(UnsupportedOperationError):
            ar.read("hello.txt")


def test_streaming_tar_does_not_call_getmembers() -> None:
    data = _build_tar()
    with open_archive(
        NonSeekableBytesIO(data), format=ArchiveFormat.TAR, streaming=True
    ) as ar:
        with mock.patch.object(
            tarfile.TarFile, "getmembers", side_effect=AssertionError("getmembers called")
        ):
            list(ar.stream_members())


def test_non_seekable_tar_gz_streaming(tmp_path: Path) -> None:
    path = tmp_path / "a.tar.gz"
    path.write_bytes(_build_tar("w:gz"))
    data = path.read_bytes()
    with open_archive(NonSeekableBytesIO(data), streaming=True) as ar:
        assert ar.format == ArchiveFormat.TAR_GZ
        assert ar.cost.stream_capability == StreamCapability.FORWARD_ONLY
        assert ar.cost.listing_cost == ListingCost.REQUIRES_DECOMPRESSION
        assert ar.cost.access_cost == AccessCost.SOLID
        collected = {}
        for member, stream in ar.stream_members():
            if stream is not None:
                collected[member.name] = stream.read()
        assert collected["hello.txt"] == b"hello world"


def test_non_seekable_tar_bz2_streaming_smoke() -> None:
    data = _build_tar("w:bz2")
    with open_archive(
        NonSeekableBytesIO(data), format=ArchiveFormat.TAR_BZ2, streaming=True
    ) as ar:
        names = [m.name for m, _ in ar.stream_members()]
        assert "hello.txt" in names


def test_compressed_source_size_on_path(tmp_path: Path) -> None:
    path = tmp_path / "a.tar.gz"
    path.write_bytes(_build_tar("w:gz"))
    with open_archive(path) as ar:
        assert ar.compressed_source_size == path.stat().st_size


def test_compressed_source_size_none_for_plain_and_stream(plain_tar: Path) -> None:
    with open_archive(plain_tar) as ar:
        assert ar.compressed_source_size is None
    with open_archive(NonSeekableBytesIO(_build_tar("w:gz")), streaming=True) as ar:
        assert ar.compressed_source_size is None


# ---------------------------------------------------------------------------
# strict_eof / end-of-archive truncation detection
# ---------------------------------------------------------------------------


def test_valid_tar_eof_silent(plain_tar: Path) -> None:
    with open_archive(plain_tar) as ar:
        with mock.patch("archivey.internal.backends.tar_reader.backends_logger") as log:
            ar.members()
            log.warning.assert_not_called()


def test_missing_eof_blocks_warns_by_default() -> None:
    data = _tar_missing_eof_block()
    with open_archive(io.BytesIO(data), format=ArchiveFormat.TAR) as ar:
        with mock.patch("archivey.internal.backends.tar_reader.backends_logger") as log:
            ar.members()
            log.warning.assert_called_once()
            assert "truncated" in log.warning.call_args[0][0].lower()


def test_missing_eof_blocks_strict_eof_raises() -> None:
    data = _tar_missing_eof_block()
    with pytest.raises(TruncatedError):
        with open_archive(
            io.BytesIO(data), format=ArchiveFormat.TAR, strict_eof=True
        ) as ar:
            ar.members()


def test_missing_eof_blocks_streaming_warns() -> None:
    data = _tar_missing_eof_block()
    with open_archive(
        NonSeekableBytesIO(data), format=ArchiveFormat.TAR, streaming=True
    ) as ar:
        with mock.patch("archivey.internal.backends.tar_reader.backends_logger") as log:
            list(ar.stream_members())
            log.warning.assert_called_once()


def test_missing_eof_blocks_streaming_strict_raises() -> None:
    data = _tar_missing_eof_block()
    with pytest.raises(TruncatedError):
        with open_archive(
            NonSeekableBytesIO(data),
            format=ArchiveFormat.TAR,
            streaming=True,
            strict_eof=True,
        ) as ar:
            list(ar.stream_members())


# ---------------------------------------------------------------------------
# Corrupt / truncated input (per-format slice of testing-contract).
# ---------------------------------------------------------------------------


def test_truncated_tar_raises() -> None:
    full = _build_tar()
    # Cut into the body so the header scan hits "unexpected end of data" (tarfile pads the
    # whole archive to a 10 KiB record, so cut well inside the real member region).
    truncated = full[:800]
    with pytest.raises(TruncatedError) as excinfo:
        with open_archive(io.BytesIO(truncated), format=ArchiveFormat.TAR) as ar:
            ar.members()
    assert isinstance(excinfo.value.__cause__, tarfile.ReadError)


def test_corrupt_tar_header_raises() -> None:
    raw = bytearray(_build_tar())
    # Corrupt the checksum field (offset 148, 8 bytes) of the first header.
    raw[148:156] = b"\xff\xff\xff\xff\xff\xff\xff\xff"
    with pytest.raises(CorruptionError) as excinfo:
        with open_archive(io.BytesIO(bytes(raw)), format=ArchiveFormat.TAR) as ar:
            ar.members()
    assert isinstance(excinfo.value.__cause__, tarfile.ReadError)


def test_filesystem_oserror_propagates_unwrapped(tmp_path: Path) -> None:
    # A genuine OSError (missing file) is not archive corruption: it must propagate
    # unchanged, not be reclassified as CorruptionError (error-handling spec).
    missing = tmp_path / "does-not-exist.tar"
    with pytest.raises(FileNotFoundError):
        open_archive(missing, format=ArchiveFormat.TAR)


def test_corrupt_compressed_tar_surfaces_codec_corruption(tmp_path: Path) -> None:
    # A gzip-wrapped tar whose deflate body is mangled: the corruption surfaces through the
    # codec layer as a CorruptionError while scanning/reading (not a raw zlib.error).
    path = tmp_path / "bad.tar.gz"
    raw = bytearray(_build_tar("w:gz"))
    raw[len(raw) // 2] ^= 0xFF  # flip a byte inside the deflate stream
    path.write_bytes(bytes(raw))
    with pytest.raises(CorruptionError):
        with open_archive(path) as ar:
            for _member, stream in ar.stream_members():
                if stream is not None:
                    stream.read()
