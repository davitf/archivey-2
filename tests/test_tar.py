"""TAR backend tests — Stage 3 (random-access read of plain + compressed tars,
PAX/GNU/ustar member mapping, cost, corrupt/truncated handling) and the TAR slice of
access-mode-and-cost. Streaming / ``stream_members`` over a non-seekable tar is Phase 4."""

from __future__ import annotations

import io
import tarfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from archivey import (
    ArchiveFormat,
    MemberType,
    open_archive,
)
from archivey.internal.cost import AccessCost, ListingCost, StreamCapability
from archivey.internal.errors import (
    CorruptionError,
    StreamNotSeekableError,
    TruncatedError,
)
from tests.conftest import requires
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
        assert ar["café.txt"].raw_name == "café.txt".encode("utf-8")


# ---------------------------------------------------------------------------
# Compressed-tar combinations beyond gz/bz2/xz (codec-layer composition)
# ---------------------------------------------------------------------------


@requires("zstandard")
def test_tar_zst_via_codec_layer() -> None:
    import zstandard

    plain = _build_tar()
    data = zstandard.ZstdCompressor().compress(plain)
    with open_archive(io.BytesIO(data)) as ar:
        assert ar.format == ArchiveFormat.TAR_ZST
        assert ar.read("hello.txt") == b"hello world"


# ---------------------------------------------------------------------------
# Non-seekable source fails fast (random-access read needs seek; streaming is Phase 4)
# ---------------------------------------------------------------------------


def test_non_seekable_tar_fails_fast() -> None:
    with pytest.raises(StreamNotSeekableError):
        open_archive(NonSeekableBytesIO(_build_tar()), format=ArchiveFormat.TAR)


def test_non_seekable_tar_fails_fast_via_detection() -> None:
    with pytest.raises(StreamNotSeekableError):
        open_archive(NonSeekableBytesIO(_build_tar()))


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
