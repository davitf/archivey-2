"""Listing resource limits: materialization caps vs stream_members escape hatch."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from archivey import (
    ArchiveyConfig,
    ExtractionLimits,
    ListingLimits,
    ResourceLimitError,
    open_archive,
)
from archivey.internal.listing_limits import (
    ListingLimitTracker,
    member_metadata_bytes,
)
from archivey.types import ArchiveMember, MemberType


def _zip_with_members(names: list[str], *, comment: bytes | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name in names:
            z.writestr(name, b"x")
        if comment is not None:
            z.comment = comment
    return buf.getvalue()


def test_max_members_raises_on_members() -> None:
    data = _zip_with_members([f"f{i}.txt" for i in range(5)])
    cfg = ArchiveyConfig(listing_limits=ListingLimits(max_members=3))
    with open_archive(io.BytesIO(data), config=cfg) as reader:
        with pytest.raises(ResourceLimitError, match="max_members"):
            reader.members()
        assert reader._materialized is None
        with pytest.raises(ResourceLimitError, match="max_members"):
            reader.members_report()
        assert reader._materialized is None


def test_max_metadata_bytes_raises_on_long_names() -> None:
    # Each name is long enough that three of them exceed a tiny metadata budget.
    names = [f"{'n' * 200}_{i}.txt" for i in range(3)]
    data = _zip_with_members(names)
    cfg = ArchiveyConfig(listing_limits=ListingLimits(max_metadata_bytes=400))
    with open_archive(io.BytesIO(data), config=cfg) as reader:
        with pytest.raises(ResourceLimitError, match="max_metadata_bytes"):
            reader.members()


def test_huge_archive_comment_counts_toward_metadata() -> None:
    data = _zip_with_members(["a.txt"], comment=b"C" * 1000)
    cfg = ArchiveyConfig(listing_limits=ListingLimits(max_metadata_bytes=500))
    with open_archive(io.BytesIO(data), config=cfg) as reader:
        with pytest.raises(ResourceLimitError, match="max_metadata_bytes"):
            reader.members()


def test_defaults_allow_linux_scale_member_counts() -> None:
    # ~100k would be heavy for a unit test; assert the default numeric contract and that
    # a modest archive under defaults succeeds.
    assert ListingLimits().max_members == 1_048_576
    assert ListingLimits().max_metadata_bytes == 64 * 2**20
    data = _zip_with_members([f"f{i}.txt" for i in range(200)])
    with open_archive(io.BytesIO(data)) as reader:
        assert len(reader.members()) == 200


def test_unlimited_disables_guards() -> None:
    data = _zip_with_members([f"f{i}.txt" for i in range(20)])
    cfg = ArchiveyConfig(listing_limits=ListingLimits.UNLIMITED)
    with open_archive(io.BytesIO(data), config=cfg) as reader:
        assert len(reader.members()) == 20


def test_stream_members_unguarded_when_members_would_fail() -> None:
    data = _zip_with_members([f"f{i}.txt" for i in range(5)])
    cfg = ArchiveyConfig(listing_limits=ListingLimits(max_members=2))
    with open_archive(io.BytesIO(data), config=cfg) as reader:
        names = [m.name for m, _ in reader.stream_members()]
        assert names == [f"f{i}.txt" for i in range(5)]
        with pytest.raises(ResourceLimitError, match="max_members"):
            reader.members()


def test_matched_defaults_list_then_extract(tmp_path: Path) -> None:
    assert ListingLimits().max_members == ExtractionLimits().max_entries == 1_048_576
    src = tmp_path / "a.zip"
    src.write_bytes(_zip_with_members([f"f{i}.txt" for i in range(10)]))
    dest = tmp_path / "out"
    with open_archive(src) as reader:
        assert len(reader.members()) == 10
        reader.extract_all(dest)
    assert (dest / "f0.txt").exists()


def test_extract_all_config_cannot_raise_listing_limits(tmp_path: Path) -> None:
    src = tmp_path / "a.zip"
    src.write_bytes(_zip_with_members([f"f{i}.txt" for i in range(5)]))
    dest = tmp_path / "out"
    tight = ArchiveyConfig(listing_limits=ListingLimits(max_members=2))
    loose = ArchiveyConfig(listing_limits=ListingLimits(max_members=1000))
    with open_archive(src, config=tight) as reader:
        with pytest.raises(ResourceLimitError, match="max_members"):
            # Materializing extract prep uses the open-time listing caps, not loose.
            reader.extract_all(dest, config=loose)


def test_tar_extract_all_enforces_listing_limits(tmp_path: Path) -> None:
    """Scan-required backends must not bypass listing caps via unguarded stream_members."""
    import tarfile

    tar_path = tmp_path / "a.tar"
    with tarfile.open(tar_path, "w") as tf:
        for i in range(5):
            info = tarfile.TarInfo(name=f"f{i}.txt")
            payload = b"x"
            info.size = len(payload)
            tf.addfile(info, io.BytesIO(payload))
    dest = tmp_path / "out"
    cfg = ArchiveyConfig(listing_limits=ListingLimits(max_members=2))
    with open_archive(tar_path, config=cfg) as reader:
        with pytest.raises(ResourceLimitError, match="max_members"):
            reader.extract_all(dest)


def test_streaming_scan_members_enforces_listing_limits(tmp_path: Path) -> None:
    """scan_members on a streaming reader must enforce caps and not publish a cache."""
    import tarfile

    tar_path = tmp_path / "a.tar"
    with tarfile.open(tar_path, "w") as tf:
        for i in range(5):
            info = tarfile.TarInfo(name=f"f{i}.txt")
            payload = b"x"
            info.size = len(payload)
            tf.addfile(info, io.BytesIO(payload))
    cfg = ArchiveyConfig(listing_limits=ListingLimits(max_members=2))
    with open_archive(tar_path, config=cfg, streaming=True) as reader:
        with pytest.raises(ResourceLimitError, match="max_members"):
            reader.scan_members()
        # Cache must stay unpublished after a limit trip.
        assert reader._materialized is None
        assert reader.members_report_if_available() is None


def test_metadata_accounting_counts_name_and_raw_name() -> None:
    member = ArchiveMember(
        type=MemberType.FILE,
        name="café.txt",
        raw_name="caf\xe9.txt".encode("latin-1"),
        comment="hi",
        extra={"note": "x", "nested": {"k": "v"}, "opaque": object()},
    )
    # Non-ASCII name uses the 4×len upper bound; ASCII comment/extra use len().
    expected = (
        4 * len("café.txt")
        + len("caf\xe9.txt".encode("latin-1"))
        + len("hi")
        + len("x")
        + len("v")
    )
    assert member_metadata_bytes(member) == expected
    # Upper bound must not under-count real UTF-8 size (Unicode name-bomb safety).
    assert member_metadata_bytes(member) >= (
        len("café.txt".encode("utf-8", "surrogateescape"))
        + len("caf\xe9.txt".encode("latin-1"))
        + len("hi".encode("utf-8", "surrogateescape"))
        + len("x".encode("utf-8", "surrogateescape"))
        + len("v".encode("utf-8", "surrogateescape"))
    )


def test_tracker_ascii_exact_and_nonascii_upper_bound() -> None:
    ascii_member = ArchiveMember(type=MemberType.FILE, name="plain.txt")
    tracker = ListingLimitTracker(ListingLimits(max_metadata_bytes=10_000))
    tracker.account_member(ascii_member)
    assert tracker.metadata_bytes == len("plain.txt")

    tracker.reset()
    name = "a\udc80b"  # surrogateescape-style code points
    member = ArchiveMember(type=MemberType.FILE, name=name)
    tracker.account_member(member)
    assert tracker.metadata_bytes == 4 * len(name)
    assert tracker.metadata_bytes >= len(name.encode("utf-8", "surrogateescape"))


def test_metadata_accounting_never_undercounts_utf8() -> None:
    # Astral + BMP non-ASCII: 4*len is a safe ceiling over real UTF-8 size.
    name = "文件📂.txt"
    member = ArchiveMember(type=MemberType.FILE, name=name)
    weight = member_metadata_bytes(member)
    assert weight == 4 * len(name)
    assert weight >= len(name.encode("utf-8"))
