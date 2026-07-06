"""TAR backend tests — random-access read, forward-only streaming on non-seekable
sources, PAX/GNU/ustar member mapping, cost, corrupt/truncated handling, and
``strict_eof`` end-of-archive verification."""

from __future__ import annotations

import io
import logging
import tarfile
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO
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
    ReadError,
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


def _tar_minimal_eof() -> bytes:
    """A fully valid archive terminated by exactly the two required EOF null blocks.

    ``tarfile`` pads its own output to a 10240-byte record boundary, so a tar it wrote
    always has plenty of trailing zeros. This helper strips that padding down to the
    bare POSIX minimum (two 512-byte null blocks, no record padding) — what e.g.
    ``tar -b1`` or a streaming producer emits — to exercise the EOF check's boundary.
    """
    full = _build_tar()
    with tarfile.open(fileobj=io.BytesIO(full), mode="r:") as t:
        members = t.getmembers()
        last = members[-1]
        blocks = (last.size + 511) & ~511
        eof_start = last.offset_data + blocks
    return full[: eof_start + 512 * 2]


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
        m = ar.get("p.txt")
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
        m = ar.get("café.txt")
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
        m = ar.get("p.txt")
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
        collected: dict[str, bytes | None] = {}
        for member, stream in ar.stream_members():
            collected[member.name] = stream.read() if stream is not None else None
        assert set(collected) == {"hello.txt", "dir/nested.txt", "dir/", "link.txt"}
        assert collected["hello.txt"] == b"hello world"
        assert collected["dir/nested.txt"] == b"nested content"
        assert collected["dir/"] is None


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


def test_compressed_source_size_generalized(plain_tar: Path) -> None:
    # Generalized (for the archive-wide extraction ratio guard): known for any path
    # source — a plain tar simply yields a harmless ~1:1 ratio — and for seekable
    # streams via a SEEK_END probe; only a non-seekable stream is unknowable.
    with open_archive(plain_tar) as ar:
        assert ar.compressed_source_size == plain_tar.stat().st_size
    data = _build_tar("w:gz")
    with open_archive(io.BytesIO(data), format=ArchiveFormat.TAR_GZ) as ar:
        assert ar.compressed_source_size == len(data)
    with open_archive(NonSeekableBytesIO(data), streaming=True) as ar:
        assert ar.compressed_source_size is None


# ---------------------------------------------------------------------------
# strict_eof / end-of-archive truncation detection
# ---------------------------------------------------------------------------


def _eof_warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    """EOF-check warnings only — filtered to the backends logger so the unrelated
    ``archivey.normalization`` warning from the ``dir`` -> ``dir/`` fixture entry
    doesn't leak into the assertion."""
    return [
        r.getMessage()
        for r in caplog.records
        if r.name == "archivey.backends" and r.levelno == logging.WARNING
    ]


def test_valid_tar_eof_silent(
    plain_tar: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="archivey.backends"):
        with open_archive(plain_tar) as ar:
            ar.members()
    assert _eof_warnings(caplog) == []


def test_missing_eof_blocks_warns_by_default(
    caplog: pytest.LogCaptureFixture,
) -> None:
    data = _tar_missing_eof_block()
    with caplog.at_level(logging.WARNING, logger="archivey.backends"):
        with open_archive(io.BytesIO(data), format=ArchiveFormat.TAR) as ar:
            ar.members()
    warnings = _eof_warnings(caplog)
    assert len(warnings) == 1
    assert "truncated" in warnings[0].lower()


def test_missing_eof_blocks_strict_eof_raises() -> None:
    data = _tar_missing_eof_block()
    with pytest.raises(TruncatedError):
        with open_archive(
            io.BytesIO(data), format=ArchiveFormat.TAR, strict_eof=True
        ) as ar:
            ar.members()


def test_missing_eof_blocks_streaming_warns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    data = _tar_missing_eof_block()
    with caplog.at_level(logging.WARNING, logger="archivey.backends"):
        with open_archive(
            NonSeekableBytesIO(data), format=ArchiveFormat.TAR, streaming=True
        ) as ar:
            list(ar.stream_members())
    assert len(_eof_warnings(caplog)) == 1


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


def test_minimal_eof_trailer_silent(caplog: pytest.LogCaptureFixture) -> None:
    # A valid archive whose trailer is exactly the two required null blocks (no record
    # padding) must not be flagged: tarfile consumes the first block detecting EOF, so the
    # check must only require the second block, not two more. Random-access path.
    data = _tar_minimal_eof()
    with caplog.at_level(logging.WARNING, logger="archivey.backends"):
        with open_archive(io.BytesIO(data), format=ArchiveFormat.TAR) as ar:
            ar.members()
    assert _eof_warnings(caplog) == []


def test_minimal_eof_trailer_streaming_silent(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Same minimal-but-valid trailer over the forward-only streaming path.
    data = _tar_minimal_eof()
    with caplog.at_level(logging.WARNING, logger="archivey.backends"):
        with open_archive(
            NonSeekableBytesIO(data), format=ArchiveFormat.TAR, streaming=True
        ) as ar:
            list(ar.stream_members())
    assert _eof_warnings(caplog) == []


def test_minimal_eof_trailer_strict_does_not_raise() -> None:
    # strict_eof must accept the minimal valid trailer on both access modes.
    data = _tar_minimal_eof()
    with open_archive(
        io.BytesIO(data), format=ArchiveFormat.TAR, strict_eof=True
    ) as ar:
        assert [m.name for m in ar.members()]
    with open_archive(
        NonSeekableBytesIO(data),
        format=ArchiveFormat.TAR,
        streaming=True,
        strict_eof=True,
    ) as ar:
        assert [m for m, _ in ar.stream_members()]


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


# ---------------------------------------------------------------------------
# Password rejection and hostile metadata robustness
# ---------------------------------------------------------------------------


def test_password_rejected() -> None:
    # TAR carries no encryption; a password is API misuse, rejected like the other
    # unencrypted formats (single-file compressors, ISO, directory) rather than ignored.
    with pytest.raises(UnsupportedOperationError):
        open_archive(io.BytesIO(_build_tar()), format=ArchiveFormat.TAR, password="x")


def test_out_of_range_mtime_degrades_to_none() -> None:
    # A crafted PAX mtime beyond datetime's range must not sink the listing.
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as t:
        info = tarfile.TarInfo("weird.txt")
        info.size = 0
        info.mtime = 10**18
        t.addfile(info)
    with open_archive(io.BytesIO(buf.getvalue()), format=ArchiveFormat.TAR) as reader:
        (member,) = reader.members()
        assert member.modified is None


# ---------------------------------------------------------------------------
# Link-target name resolution (relative symlinks, hardlinks, streaming pass)
# ---------------------------------------------------------------------------


def _build_link_tar() -> bytes:
    """dir/file + a root-level decoy `file`, and links exercising target resolution."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as t:
        for name, data in [("file", b"ROOT"), ("dir/file", b"NESTED"), ("top.txt", b"TOP")]:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
        rel = tarfile.TarInfo("dir/rel_link")  # -> dir/file, not the root decoy
        rel.type = tarfile.SYMTYPE
        rel.linkname = "file"
        t.addfile(rel)
        up = tarfile.TarInfo("dir/up_link")  # ../top.txt -> top.txt
        up.type = tarfile.SYMTYPE
        up.linkname = "../top.txt"
        t.addfile(up)
        absolute = tarfile.TarInfo("dir/abs_link")  # absolute: outside the archive
        absolute.type = tarfile.SYMTYPE
        absolute.linkname = "/etc/passwd"
        t.addfile(absolute)
        hard = tarfile.TarInfo("dir/hard")  # hardlink targets are archive-relative
        hard.type = tarfile.LNKTYPE
        hard.linkname = "dir/file"
        t.addfile(hard)
    return buf.getvalue()


def test_relative_symlink_resolves_against_link_directory() -> None:
    with open_archive(io.BytesIO(_build_link_tar()), format=ArchiveFormat.TAR) as ar:
        member = ar.get("dir/rel_link")
        assert member.link_target == "file"  # raw stored target is untouched
        assert member.link_target_member is not None
        assert member.link_target_member.name == "dir/file"
        assert ar.read("dir/rel_link") == b"NESTED"  # not the root-level decoy


def test_dotdot_symlink_resolves_upward() -> None:
    with open_archive(io.BytesIO(_build_link_tar()), format=ArchiveFormat.TAR) as ar:
        assert ar.get("dir/up_link").link_target_member.name == "top.txt"
        assert ar.read("dir/up_link") == b"TOP"


def test_absolute_symlink_stays_unresolved() -> None:
    from archivey.exceptions import LinkTargetNotFoundError

    with open_archive(io.BytesIO(_build_link_tar()), format=ArchiveFormat.TAR) as ar:
        member = ar.get("dir/abs_link")
        assert member.link_target == "/etc/passwd"
        assert member.link_target_member is None
        with pytest.raises(LinkTargetNotFoundError):
            ar.open(member)


def test_hardlink_target_is_archive_relative() -> None:
    with open_archive(io.BytesIO(_build_link_tar()), format=ArchiveFormat.TAR) as ar:
        assert ar.get("dir/hard").link_target_member.name == "dir/file"
        assert ar.read("dir/hard") == b"NESTED"


def test_streaming_pass_resolves_backward_links() -> None:
    # Hardlinks always point at an earlier member (the TAR model), so a single
    # streaming pass resolves them progressively; relative symlinks to earlier
    # members resolve too.
    source = NonSeekableBytesIO(_build_link_tar())
    with open_archive(source, format=ArchiveFormat.TAR, streaming=True) as ar:
        resolved = {
            m.name: (m.link_target_member.name if m.link_target_member else None)
            for m, _stream in ar.stream_members()
            if m.is_link
        }
    assert resolved == {
        "dir/rel_link": "dir/file",
        "dir/up_link": "top.txt",
        "dir/abs_link": None,
        "dir/hard": "dir/file",
    }


def _link_tar_bytes(specs: list[tuple[str, str, bytes | str]]) -> bytes:
    """Build a tar from (kind, name, payload) specs.

    kind: ``file`` (payload=bytes), ``sym``/``hard`` (payload=linkname).
    """
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as t:
        for kind, name, payload in specs:
            info = tarfile.TarInfo(name)
            if kind == "file":
                info.size = len(payload)
                info.mode = 0o644
                t.addfile(info, io.BytesIO(payload))
            elif kind == "sym":
                info.type = tarfile.SYMTYPE
                info.linkname = payload
                t.addfile(info)
            elif kind == "hard":
                info.type = tarfile.LNKTYPE
                info.linkname = payload
                t.addfile(info)
    return buf.getvalue()


_DUP_HARDLINK_TAR = _link_tar_bytes(
    [
        ("file", "A.txt", b"content1"),
        ("hard", "L.txt", "A.txt"),
        ("file", "A.txt", b"content2"),
    ]
)


def test_hardlink_duplicate_name_positional_random_access() -> None:
    with open_archive(io.BytesIO(_DUP_HARDLINK_TAR), format=ArchiveFormat.TAR) as ar:
        link = ar.get("L.txt")
        assert link.link_target_member is not None
        assert link.link_target_member.name == "A.txt"
        assert ar.read(link.link_target_member) == b"content1"
        assert ar.read("L.txt") == b"content1"


def test_hardlink_duplicate_name_positional_streaming() -> None:
    source = NonSeekableBytesIO(_DUP_HARDLINK_TAR)
    with open_archive(source, format=ArchiveFormat.TAR, streaming=True) as ar:
        links = {
            m.name: m.link_target_member
            for m, _ in ar.stream_members()
            if m.type == MemberType.HARDLINK
        }
    assert links["L.txt"] is not None
    assert links["L.txt"].name == "A.txt"


def test_symlink_duplicate_name_last_wins_random_access() -> None:
    data = _link_tar_bytes(
        [
            ("file", "A.txt", b"content1"),
            ("sym", "S.txt", "A.txt"),
            ("file", "A.txt", b"content2"),
        ]
    )
    with open_archive(io.BytesIO(data), format=ArchiveFormat.TAR) as ar:
        link = ar.get("S.txt")
        assert link.link_target_member is not None
        assert link.link_target_member.name == "A.txt"
        assert ar.read(link.link_target_member) == b"content2"
        assert ar.read("S.txt") == b"content2"


# ---------------------------------------------------------------------------
# scan_members(), post-pass cache, one-pass-only streaming
# ---------------------------------------------------------------------------


def _build_forward_symlink_tar() -> bytes:
    """Symlink appears before its target in archive order."""
    return _link_tar_bytes(
        [
            ("sym", "forward_link", "target.txt"),
            ("file", "target.txt", b"TARGET"),
        ]
    )


def test_streaming_scan_members_resolves_forward_symlink() -> None:
    source = NonSeekableBytesIO(_build_forward_symlink_tar())
    with open_archive(source, format=ArchiveFormat.TAR, streaming=True) as ar:
        members = ar.scan_members()
    link = next(m for m in members if m.name == "forward_link")
    assert link.link_target_member is not None
    assert link.link_target_member.name == "target.txt"

    with open_archive(
        io.BytesIO(_build_forward_symlink_tar()), format=ArchiveFormat.TAR
    ) as ar:
        expected = ar.get("forward_link")
    assert link.link_target_member.name == expected.link_target_member.name


def test_streaming_iter_materializes_resolved_cache() -> None:
    source = NonSeekableBytesIO(_build_forward_symlink_tar())
    with open_archive(source, format=ArchiveFormat.TAR, streaming=True) as ar:
        collected: list = []
        for member in ar:
            if member.name == "forward_link":
                collected.append(member)
                assert member.link_target_member is None
        members = ar.get_members_if_available()
        assert members is not None
        link = next(m for m in members if m.name == "forward_link")
        assert link.link_target_member is not None
        assert link.link_target_member.name == "target.txt"
        assert collected[0].link_target_member.name == "target.txt"


def test_streaming_stream_members_materializes_resolved_cache() -> None:
    source = NonSeekableBytesIO(_build_forward_symlink_tar())
    with open_archive(source, format=ArchiveFormat.TAR, streaming=True) as ar:
        collected: list = []
        for member, _stream in ar.stream_members():
            if member.name == "forward_link":
                collected.append(member)
        members = ar.get_members_if_available()
        assert members is not None
        link = next(m for m in members if m.name == "forward_link")
        assert link.link_target_member is not None
        assert collected[0].link_target_member.name == "target.txt"


def test_scan_members_finishes_interrupted_pass(tmp_path: Path) -> None:
    source = NonSeekableBytesIO(_build_forward_symlink_tar())
    with open_archive(source, format=ArchiveFormat.TAR, streaming=True) as ar:
        for member in ar:
            if member.name == "forward_link":
                break
        members = ar.scan_members()
        link = next(m for m in members if m.name == "forward_link")
        assert link.link_target_member is not None
        assert link.link_target_member.name == "target.txt"
        with pytest.raises(UnsupportedOperationError):
            list(ar)
        with pytest.raises(UnsupportedOperationError):
            list(ar.stream_members())
        with pytest.raises(UnsupportedOperationError):
            ar.extract_all(tmp_path)


def test_abandoned_partial_pass_leaves_get_members_none() -> None:
    source = NonSeekableBytesIO(_build_forward_symlink_tar())
    with open_archive(source, format=ArchiveFormat.TAR, streaming=True) as ar:
        for member in ar:
            if member.name == "forward_link":
                break
        assert ar.get_members_if_available() is None


def test_streaming_second_pass_raises_tar_and_zip(
    plain_tar: Path, tmp_path: Path
) -> None:
    zip_path = tmp_path / "second.zip"
    import zipfile

    with zipfile.ZipFile(zip_path, "w") as z:
        z.writestr("hello.txt", b"hello world")
    with open_archive(plain_tar, streaming=True) as ar:
        list(ar)
        with pytest.raises(UnsupportedOperationError):
            list(ar)
        with pytest.raises(UnsupportedOperationError):
            list(ar.stream_members())
    with open_archive(zip_path, streaming=True) as ar:
        list(ar)
        with pytest.raises(UnsupportedOperationError):
            list(ar)


def test_scan_members_random_access_parity(plain_tar: Path, tmp_path: Path) -> None:
    import zipfile

    zip_path = tmp_path / "scan.zip"
    with zipfile.ZipFile(zip_path, "w") as z:
        z.writestr("hello.txt", b"hello world")
    simple_dir = tmp_path / "dirscan"
    simple_dir.mkdir()
    (simple_dir / "a.txt").write_bytes(b"x")
    for source in (plain_tar, zip_path, simple_dir):
        with open_archive(source) as ar:
            assert ar.scan_members() == ar.members()
            assert [m.name for m in ar] == [m.name for m in ar.members()]


def test_scan_members_before_pass_consumes_streaming_reader(
    plain_tar: Path, tmp_path: Path
) -> None:
    import zipfile

    zip_path = tmp_path / "consume.zip"
    with zipfile.ZipFile(zip_path, "w") as z:
        z.writestr("hello.txt", b"hello world")
    simple_dir = tmp_path / "dirconsume"
    simple_dir.mkdir()
    (simple_dir / "a.txt").write_bytes(b"x")
    for source in (plain_tar, zip_path, simple_dir):
        with open_archive(source, streaming=True) as ar:
            names = {m.name for m in ar.scan_members()}
            assert len(names) > 0
            with pytest.raises(UnsupportedOperationError):
                list(ar.stream_members())


def test_link_cycle_raises_read_error() -> None:
    data = _link_tar_bytes(
        [
            ("sym", "a", "b"),
            ("sym", "b", "a"),
        ]
    )
    with open_archive(io.BytesIO(data), format=ArchiveFormat.TAR) as ar:
        with pytest.raises(ReadError, match="cycle"):
            ar.read("a")


def test_chain_through_same_named_members_not_false_cycle() -> None:
    """Member-id cycle tracking must not false-positive on distinct same-named members."""
    from archivey.cost import AccessCost, ListingCost, StreamCapability
    from archivey.internal.base_reader import BaseArchiveReader
    from archivey.types import ArchiveInfo, ArchiveMember

    class _Reader(BaseArchiveReader):
        def __init__(self, members: list[ArchiveMember], payloads: dict[str, bytes]) -> None:
            super().__init__(ArchiveFormat.TAR, streaming=False, archive_name=None)
            self._payloads = payloads
            self._members_cache = members
            by_name_lists: dict[str, list[ArchiveMember]] = {}
            for m in members:
                BaseArchiveReader._index_member_name(by_name_lists, m)
            self._members_by_name_lists = by_name_lists

        def _iter_members(self):
            return iter(self._members_cache or [])

        def _open_member(self, member: ArchiveMember) -> BinaryIO:
            return io.BytesIO(self._payloads[member.name])

        def _get_archive_info(self) -> ArchiveInfo:
            return ArchiveInfo(
                format=ArchiveFormat.TAR,
                cost=AccessCost(
                    listing=ListingCost.FREE,
                    random_access=StreamCapability.SUPPORTED,
                ),
            )

        def _close_archive(self) -> None:
            return None

    # Hop-by-hop chain start → dup(sym) → tail → dup(file); two distinct "dup.txt" members.
    first = ArchiveMember(
        name="dup.txt", type=MemberType.SYMLINK, link_target="tail"
    )
    second = ArchiveMember(name="dup.txt", type=MemberType.FILE)
    tail = ArchiveMember(
        name="tail", type=MemberType.SYMLINK, link_target="dup.txt"
    )
    start = ArchiveMember(
        name="start", type=MemberType.SYMLINK, link_target="dup.txt"
    )
    for idx, m in enumerate((first, second, tail, start)):
        m._member_id = idx
        m._archive_id = "test"
    first.link_target_member = tail
    tail.link_target_member = second
    start.link_target_member = first
    reader = _Reader(
        [first, second, tail, start],
        {"dup.txt": b"payload", "tail": b"", "start": b""},
    )
    with reader._open_with_link_follow(start, set()) as stream:
        assert stream.read() == b"payload"
