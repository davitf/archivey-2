"""ZIP backend tests — Stage 1 (member mapping, cost, O(1) lookup, non-seekable
fail-fast, multi-volume rejection) and the ZIP slice of access-mode-and-cost."""

from __future__ import annotations

import io
import struct
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from archivey import (
    ArchiveFormat,
    CompressionAlgorithm,
    MemberType,
    open_archive,
)
from archivey.internal.cost import AccessCost, ListingCost, StreamCapability
from archivey.internal.errors import (
    CorruptionError,
    StreamNotSeekableError,
    UnsupportedFeatureError,
)
from tests.streams_util import NonSeekableBytesIO

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def simple_zip(tmp_path: Path) -> Path:
    path = tmp_path / "simple.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("hello.txt", b"hello world")
        z.writestr("dir/nested.txt", b"nested content")
    return path


def _zip_with_unix_mode(path: Path, name: str, data: bytes, mode: int) -> None:
    info = zipfile.ZipInfo(name)
    info.create_system = 3  # Unix
    info.external_attr = mode << 16
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(info, data)


# ---------------------------------------------------------------------------
# Cost / format properties (also covers access-mode-and-cost for ZIP)
# ---------------------------------------------------------------------------


def test_cost_receipt(simple_zip: Path) -> None:
    with open_archive(simple_zip) as ar:
        cost = ar.cost
        assert cost.listing_cost == ListingCost.INDEXED
        assert cost.access_cost == AccessCost.DIRECT
        assert cost.stream_capability == StreamCapability.SEEKABLE


def test_archive_info(simple_zip: Path) -> None:
    with open_archive(simple_zip) as ar:
        info = ar.info
        assert info.format == ArchiveFormat.ZIP
        assert info.is_solid is False
        assert info.is_encrypted is False
        assert info.is_multivolume is False
        assert info.member_count == 2


def test_format_detected_as_zip(simple_zip: Path) -> None:
    with open_archive(simple_zip) as ar:
        assert ar.format == ArchiveFormat.ZIP


# ---------------------------------------------------------------------------
# Indexed listing + random access (access-mode-and-cost, default streaming=False)
# ---------------------------------------------------------------------------


def test_members_listed(simple_zip: Path) -> None:
    with open_archive(simple_zip) as ar:
        names = {m.name for m in ar.members()}
        assert names == {"hello.txt", "dir/nested.txt"}


def test_member_list_available_without_scan(simple_zip: Path) -> None:
    with open_archive(simple_zip) as ar:
        # The central directory is an upfront index, so the list is available with no scan.
        members = ar.get_members_if_available()
        assert members is not None
        assert {m.name for m in members} == {"hello.txt", "dir/nested.txt"}


def test_random_access_read_by_name(simple_zip: Path) -> None:
    with open_archive(simple_zip) as ar:
        assert ar.read("hello.txt") == b"hello world"
        assert ar.read("dir/nested.txt") == b"nested content"
        # Re-reading an earlier member out of order still works (random access).
        assert ar.read("hello.txt") == b"hello world"


def test_central_directory_lookup_no_io(simple_zip: Path) -> None:
    # reader["name"] is served from the in-memory name map with no extra archive reads.
    with open_archive(simple_zip) as ar:
        ar.members()  # materialize
        archive = ar._archive  # type: ignore[attr-defined]
        calls = {"open": 0}
        original_open = archive.open

        def counting_open(*args, **kwargs):  # type: ignore[no-untyped-def]
            calls["open"] += 1
            return original_open(*args, **kwargs)

        archive.open = counting_open  # type: ignore[method-assign]
        member = ar["hello.txt"]
        assert member.name == "hello.txt"
        assert calls["open"] == 0  # lookup touched no archive data


# ---------------------------------------------------------------------------
# Member metadata mapping
# ---------------------------------------------------------------------------


def test_unix_mode_from_external_attr(tmp_path: Path) -> None:
    path = tmp_path / "moded.zip"
    _zip_with_unix_mode(path, "script.sh", b"#!/bin/sh\n", 0o755)
    with open_archive(path) as ar:
        member = ar["script.sh"]
        assert member.mode == 0o755


def test_none_mode_when_external_attr_zero() -> None:
    # zipfile.writestr always stamps a 0o600 external_attr, so zero it in the central
    # directory (4-byte field at offset 38 of the PK\x01\x02 record) to exercise the
    # "external_attr == 0 -> mode None" rule.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("plain.txt", b"x")
    raw = bytearray(buf.getvalue())
    cd = raw.index(b"PK\x01\x02")
    raw[cd + 38 : cd + 42] = b"\x00\x00\x00\x00"
    with open_archive(io.BytesIO(bytes(raw))) as ar:
        assert ar["plain.txt"].mode is None


def test_directory_member_type(simple_zip: Path) -> None:
    with open_archive(simple_zip) as ar:
        by_name = {m.name: m for m in ar.members()}
        # zipfile stores explicit directory entries with a trailing slash.
        assert by_name["hello.txt"].type == MemberType.FILE


def test_explicit_directory_entry(tmp_path: Path) -> None:
    path = tmp_path / "withdir.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("adir/", b"")
        z.writestr("adir/file.txt", b"data")
    with open_archive(path) as ar:
        by_name = {m.name: m for m in ar.members()}
        assert by_name["adir/"].type == MemberType.DIRECTORY


def test_symlink_member(tmp_path: Path) -> None:
    import stat as stat_module

    path = tmp_path / "link.zip"
    info = zipfile.ZipInfo("link")
    info.create_system = 3
    info.external_attr = (stat_module.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(info, b"target.txt")
    with open_archive(path) as ar:
        member = ar["link"]
        assert member.type == MemberType.SYMLINK
        assert member.link_target == "target.txt"


def test_compression_method_mapping(tmp_path: Path) -> None:
    path = tmp_path / "methods.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("stored.txt", b"a" * 100, compress_type=zipfile.ZIP_STORED)
        z.writestr("deflated.txt", b"a" * 100, compress_type=zipfile.ZIP_DEFLATED)
    with open_archive(path) as ar:
        by_name = {m.name: m for m in ar.members()}
        assert by_name["stored.txt"].compression[0].algo == CompressionAlgorithm.STORED
        assert by_name["deflated.txt"].compression[0].algo == CompressionAlgorithm.DEFLATE


def test_encrypted_flag() -> None:
    # stdlib zipfile cannot write encrypted entries, so set the general-purpose
    # encryption bit (0x1) directly in the central-directory record's flag field (at
    # offset 8 of the PK\x01\x02 record) to exercise the is_encrypted mapping.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("e.txt", b"data")
    raw = bytearray(buf.getvalue())
    cd = raw.index(b"PK\x01\x02")
    flags = int.from_bytes(raw[cd + 8 : cd + 10], "little") | 0x1
    raw[cd + 8 : cd + 10] = flags.to_bytes(2, "little")
    with open_archive(io.BytesIO(bytes(raw))) as ar:
        assert ar["e.txt"].is_encrypted is True


def test_size_fields(simple_zip: Path) -> None:
    with open_archive(simple_zip) as ar:
        member = ar["hello.txt"]
        assert member.size == len(b"hello world")
        assert member.compressed_size is not None


def test_raw_name_preserved(tmp_path: Path) -> None:
    path = tmp_path / "utf8.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("café.txt", b"x")  # forces the UTF-8 flag
    with open_archive(path) as ar:
        member = ar["café.txt"]
        assert member.raw_name == "café.txt".encode("utf-8")


def test_extended_timestamp_precedence(tmp_path: Path) -> None:
    # An Extended Timestamp extra field (0x5455) carries a real Unix time and overrides the
    # 2-second-granularity DOS date_time, yielding a tz-aware UTC datetime.
    unix_time = 1_600_000_000  # 2020-09-13T12:26:40Z
    extra = struct.pack("<HHB I", 0x5455, 5, 0x01, unix_time)
    path = tmp_path / "ts.zip"
    info = zipfile.ZipInfo("t.txt", date_time=(1990, 1, 1, 0, 0, 0))
    info.extra = extra
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(info, b"data")
    with open_archive(path) as ar:
        member = ar["t.txt"]
        assert member.modified is not None
        assert member.modified.tzinfo is not None
        assert member.modified == datetime.fromtimestamp(unix_time, tz=timezone.utc)


# ---------------------------------------------------------------------------
# Non-seekable source fails fast
# ---------------------------------------------------------------------------


def test_non_seekable_zip_fails_fast(simple_zip: Path) -> None:
    data = simple_zip.read_bytes()
    with pytest.raises(StreamNotSeekableError):
        open_archive(NonSeekableBytesIO(data), format=ArchiveFormat.ZIP)


def test_non_seekable_zip_fails_fast_via_detection(simple_zip: Path) -> None:
    # Even without an explicit format, a non-seekable ZIP is rejected at open time (the
    # opener wraps it in a PeekableStream for detection, then enforces REQUIRES_SEEK).
    data = simple_zip.read_bytes()
    with pytest.raises(StreamNotSeekableError):
        open_archive(NonSeekableBytesIO(data))


# ---------------------------------------------------------------------------
# Multi-volume rejection
# ---------------------------------------------------------------------------


def test_split_segment_name_rejected(tmp_path: Path) -> None:
    # A .z01 segment of a split set is rejected by name with a "rejoin first" hint.
    segment = tmp_path / "archive.z01"
    segment.write_bytes(b"\x50\x4b\x03\x04" + b"\x00" * 64)
    with pytest.raises(UnsupportedFeatureError) as excinfo:
        open_archive(segment, format=ArchiveFormat.ZIP)
    assert "rejoin" in str(excinfo.value).lower()


# ---------------------------------------------------------------------------
# Reading via open() and stream_members()
# ---------------------------------------------------------------------------


def test_open_returns_stream(simple_zip: Path) -> None:
    with open_archive(simple_zip) as ar:
        with ar.open("hello.txt") as f:
            assert f.read() == b"hello world"


def test_stream_members(simple_zip: Path) -> None:
    with open_archive(simple_zip) as ar:
        collected = {}
        for member, stream in ar.stream_members():
            collected[member.name] = stream.read() if stream is not None else None
        assert collected["hello.txt"] == b"hello world"
        assert collected["dir/nested.txt"] == b"nested content"


def test_read_roundtrip_from_stream_source(simple_zip: Path) -> None:
    # Opening from an already-seekable in-memory stream (no path) works too.
    data = simple_zip.read_bytes()
    with open_archive(io.BytesIO(data)) as ar:
        assert ar.read("hello.txt") == b"hello world"


# ---------------------------------------------------------------------------
# Corrupt / truncated input -> CorruptionError (with the original cause attached).
# (Per-format slice of testing-contract's adversarial-corpus requirement, pulled
# forward to lock down the backend's exception translation as it lands.)
# ---------------------------------------------------------------------------


def test_truncated_zip_raises_corruption() -> None:
    # A ZIP whose central directory / EOCD has been cut off: the local-file-header magic
    # still makes detection pick ZIP, but stdlib zipfile cannot parse it.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("hello.txt", b"hello world" * 100)
    truncated = buf.getvalue()[: len(buf.getvalue()) // 2]

    with pytest.raises(CorruptionError) as excinfo:
        open_archive(io.BytesIO(truncated))
    assert isinstance(excinfo.value.__cause__, zipfile.BadZipFile)


def test_corrupt_member_data_raises_corruption_on_read() -> None:
    # A structurally valid ZIP whose stored member payload has been altered: listing
    # succeeds, but reading the member trips the CRC check -> CorruptionError.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        z.writestr("data.txt", b"A" * 200)
    raw = bytearray(buf.getvalue())
    # STORED payload begins after the 30-byte local header + 8-byte name ("data.txt").
    raw[50] ^= 0xFF

    with open_archive(io.BytesIO(bytes(raw))) as ar:
        assert ar.members()[0].name == "data.txt"  # listing is unaffected
        with pytest.raises(CorruptionError):
            ar.read("data.txt")
