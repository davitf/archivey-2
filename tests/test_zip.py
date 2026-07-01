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
from archivey.cost import AccessCost, ListingCost, StreamCapability
from archivey.exceptions import (
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


def test_unknown_extra_field_before_timestamp(tmp_path: Path) -> None:
    # An unknown extra field (0x1234) preceding the Extended Timestamp (0x5455): the
    # extra-field walk must skip the unknown field by its declared length and still reach
    # and parse the timestamp (regression for the field-walking loop).
    unix_time = 1_600_000_000
    extra = struct.pack("<HH4s", 0x1234, 4, b"abcd") + struct.pack(
        "<HHBI", 0x5455, 5, 0x01, unix_time
    )
    path = tmp_path / "extra.zip"
    info = zipfile.ZipInfo("file.txt", date_time=(1990, 1, 1, 0, 0, 0))
    info.extra = extra
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(info, b"data")
    with open_archive(path) as ar:
        assert ar["file.txt"].modified == datetime.fromtimestamp(unix_time, tz=timezone.utc)


def test_extended_timestamp_fills_mtime_atime_ctime(tmp_path: Path) -> None:
    # An Extended Timestamp (0x5455) with flags 0x07 carries modification, access and
    # creation times (in that order); all three should populate the member.
    mtime, atime, ctime = 1_600_000_000, 1_600_000_100, 1_600_000_200
    extra = struct.pack("<HHB iii", 0x5455, 13, 0x07, mtime, atime, ctime)
    path = tmp_path / "ts3.zip"
    info = zipfile.ZipInfo("t.txt")
    info.extra = extra
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(info, b"data")
    with open_archive(path) as ar:
        member = ar["t.txt"]
        assert member.modified == datetime.fromtimestamp(mtime, tz=timezone.utc)
        assert member.accessed == datetime.fromtimestamp(atime, tz=timezone.utc)
        assert member.created == datetime.fromtimestamp(ctime, tz=timezone.utc)


def test_duplicate_member_names_read_independently(tmp_path: Path) -> None:
    # Two members stored under the same name: each must read its own data. The reader keys
    # off the member's own ZipInfo handle (member._raw), not a name map, so there is no
    # collision.
    path = tmp_path / "dup.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("dup.txt", b"first")
        z.writestr("dup.txt", b"second")
    with open_archive(path) as ar:
        members = ar.members()
        assert len(members) == 2
        assert ar.read(members[0]) == b"first"
        assert ar.read(members[1]) == b"second"


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
    # A .z01 segment of a split set is rejected by name as an unsupported multi-volume ZIP.
    segment = tmp_path / "archive.z01"
    segment.write_bytes(b"\x50\x4b\x03\x04" + b"\x00" * 64)
    with pytest.raises(UnsupportedFeatureError) as excinfo:
        open_archive(segment, format=ArchiveFormat.ZIP)
    assert "multi-volume" in str(excinfo.value).lower()


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


# ---------------------------------------------------------------------------
# Encrypted symlink targets and explicit metadata encoding
# ---------------------------------------------------------------------------


def _flag_first_entry_encrypted(data: bytes) -> bytes:
    """Set bit 0 (encryption) of the general-purpose flags in both headers."""
    raw = bytearray(data)
    raw[raw.find(b"PK\x03\x04") + 6] |= 1
    raw[raw.find(b"PK\x01\x02") + 8] |= 1
    return bytes(raw)


def test_encrypted_symlink_listing_without_password() -> None:
    # A symlink's target is its (encrypted) file data; listing must still succeed with
    # link_target unset — not leak zipfile's raw RuntimeError("password required").
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        info = zipfile.ZipInfo("link")
        info.create_system = 3  # Unix
        info.external_attr = 0o120777 << 16  # symlink mode
        z.writestr(info, b"target.txt")
    data = _flag_first_entry_encrypted(buf.getvalue())

    with open_archive(io.BytesIO(data), format=ArchiveFormat.ZIP) as reader:
        (member,) = reader.members()
        assert member.type is MemberType.SYMLINK
        assert member.is_encrypted
        assert member.link_target is None


def _zip_with_non_utf8_name(name_byte: bytes) -> bytes:
    """A ZIP whose single member name contains ``name_byte``, stored without the UTF-8 flag."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("X.txt", b"data")  # ASCII name -> no UTF-8 flag
    # Same length, so all header offsets stay valid; replaces the name in both the local
    # header and the central directory.
    return buf.getvalue().replace(b"X.txt", name_byte + b".txt")


def test_explicit_encoding_overrides_cp437_default() -> None:
    data = _zip_with_non_utf8_name(b"\xe9")

    # Default: zipfile's cp437 fallback (0xE9 -> Greek Theta).
    with open_archive(io.BytesIO(data), format=ArchiveFormat.ZIP) as reader:
        (member,) = reader.members()
        assert member.name == "Θ.txt"

    # Explicit caller encoding wins, and raw_name still round-trips the stored bytes.
    with open_archive(
        io.BytesIO(data), format=ArchiveFormat.ZIP, encoding="latin-1"
    ) as reader:
        (member,) = reader.members()
        assert member.name == "é.txt"
        assert member.raw_name == b"\xe9.txt"


def _ntfs_extra(mtime_ft: int, atime_ft: int, ctime_ft: int) -> bytes:
    """An NTFS extra field (0x000A): 4 reserved bytes, then tag 1 with three FILETIMEs."""
    body = struct.pack("<I", 0) + struct.pack("<HHQQQ", 0x0001, 24, mtime_ft, atime_ft, ctime_ft)
    return struct.pack("<HH", 0x000A, len(body)) + body


def _to_filetime(unix_time: int) -> int:
    return (unix_time + 11_644_473_600) * 10_000_000


def test_ntfs_timestamps_used_when_no_extended_timestamp(tmp_path: Path) -> None:
    # An NTFS extra field (0x000A) carries FILETIME mtime/atime/ctime; with no 0x5455
    # field they populate all three member times as tz-aware UTC datetimes.
    mtime, atime, ctime = 1_600_000_000, 1_600_000_100, 1_600_000_200
    path = tmp_path / "ntfs.zip"
    info = zipfile.ZipInfo("t.txt", date_time=(1990, 1, 1, 0, 0, 0))
    info.extra = _ntfs_extra(_to_filetime(mtime), _to_filetime(atime), _to_filetime(ctime))
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(info, b"data")
    with open_archive(path) as ar:
        member = ar["t.txt"]
        assert member.modified == datetime.fromtimestamp(mtime, tz=timezone.utc)
        assert member.accessed == datetime.fromtimestamp(atime, tz=timezone.utc)
        assert member.created == datetime.fromtimestamp(ctime, tz=timezone.utc)


def test_extended_timestamp_beats_ntfs(tmp_path: Path) -> None:
    # Precedence: 0x5455 (Unix) > 0x000A (NTFS) > DOS date_time — regardless of the
    # fields' order in the extra blob. Here NTFS carries all three times but the UT
    # field's mtime wins for `modified`; atime/ctime stay from NTFS (UT carries none).
    ut_mtime, nt_mtime, nt_atime = 1_600_000_000, 1_500_000_000, 1_500_000_100
    extra = _ntfs_extra(_to_filetime(nt_mtime), _to_filetime(nt_atime), 0) + struct.pack(
        "<HHBI", 0x5455, 5, 0x01, ut_mtime
    )
    path = tmp_path / "both.zip"
    info = zipfile.ZipInfo("t.txt", date_time=(1990, 1, 1, 0, 0, 0))
    info.extra = extra
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(info, b"data")
    with open_archive(path) as ar:
        member = ar["t.txt"]
        assert member.modified == datetime.fromtimestamp(ut_mtime, tz=timezone.utc)
        assert member.accessed == datetime.fromtimestamp(nt_atime, tz=timezone.utc)
        assert member.created is None  # NTFS ctime was 0 = "not set"
