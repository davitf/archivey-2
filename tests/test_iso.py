"""ISO 9660 backend tests — Stage 4 (namespace auto-select + fidelity, cost, write/
password rejection, non-seekable rejection, corrupt handling) and the registry
degradation slice (ISO without pycdlib). Skipped when pycdlib is absent."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from archivey import (
    ArchiveFormat,
    CompressionAlgorithm,
    CompressionMethod,
    MemberStreams,
    MemberType,
    detect_format,
    format_availability,
    open_archive,
)
from archivey.cost import AccessCost, ListingCost, StreamCapability
from archivey.exceptions import (
    CorruptionError,
    StreamNotSeekableError,
    UnsupportedOperationError,
)
from archivey.internal.registry import FormatSupport, get_registry
from tests.conftest import requires
from tests.streams_util import NonSeekableBytesIO

pytestmark = requires("pycdlib")


def _build_iso(*, rock_ridge: bool, joliet: bool) -> bytes:
    """Build a small ISO with a file, a nested file, a directory, and (RR) a symlink."""
    import pycdlib

    iso = pycdlib.PyCdlib()
    kwargs = {}
    if rock_ridge:
        kwargs["rock_ridge"] = "1.09"
    if joliet:
        kwargs["joliet"] = 3
    iso.new(interchange_level=3, **kwargs)
    iso.add_fp(
        io.BytesIO(b"hello world"),
        11,
        "/FILE.TXT;1",
        rr_name="file.txt" if rock_ridge else None,
        joliet_path="/file.txt" if joliet else None,
    )
    iso.add_fp(
        io.BytesIO(b""),
        0,
        "/EMPTY.TXT;1",
        rr_name="empty.txt" if rock_ridge else None,
        joliet_path="/empty.txt" if joliet else None,
    )
    iso.add_directory(
        "/DIR",
        rr_name="subdir" if rock_ridge else None,
        joliet_path="/subdir" if joliet else None,
    )
    iso.add_fp(
        io.BytesIO(b"nested!"),
        7,
        "/DIR/N.TXT;1",
        rr_name="n.txt" if rock_ridge else None,
        joliet_path="/subdir/n.txt" if joliet else None,
    )
    if rock_ridge:
        iso.add_symlink("/SYM.TXT;1", "sym", "file.txt")
    out = io.BytesIO()
    iso.write_fp(out)
    iso.close()
    return out.getvalue()


@pytest.fixture
def rock_ridge_iso(tmp_path: Path) -> Path:
    path = tmp_path / "rr.iso"
    path.write_bytes(_build_iso(rock_ridge=True, joliet=True))
    return path


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def test_iso_detected_by_extended_window() -> None:
    info = detect_format(io.BytesIO(_build_iso(rock_ridge=True, joliet=False)))
    assert info.format == ArchiveFormat.ISO
    assert info.detected_by == "magic"  # CD001 at offset 32 769


# ---------------------------------------------------------------------------
# Cost / format properties
# ---------------------------------------------------------------------------


def test_iso_cost(rock_ridge_iso: Path) -> None:
    with open_archive(rock_ridge_iso) as ar:
        assert ar.format == ArchiveFormat.ISO
        assert ar.cost.listing_cost == ListingCost.INDEXED
        assert ar.cost.access_cost == AccessCost.DIRECT
        assert ar.cost.stream_capability == StreamCapability.SEEKABLE
        assert ar.info.is_solid is False


# ---------------------------------------------------------------------------
# Namespace auto-select + metadata fidelity
# ---------------------------------------------------------------------------


def test_rock_ridge_namespace_and_fidelity(rock_ridge_iso: Path) -> None:
    with open_archive(rock_ridge_iso) as ar:
        assert ar.info.extra["iso.namespace"] == "rock_ridge"
        by_name = {m.name: m for m in ar.members()}
        f = by_name["file.txt"]  # original case + length preserved
        assert f.mode is not None and f.uid is not None and f.gid is not None
        assert f.modified is not None and f.modified.tzinfo is not None
        sym = by_name["sym"]
        assert sym.type == MemberType.SYMLINK
        assert sym.link_target == "file.txt"
        assert by_name["subdir/"].type == MemberType.DIRECTORY


def test_joliet_namespace_and_fidelity(tmp_path: Path) -> None:
    path = tmp_path / "joliet.iso"
    path.write_bytes(_build_iso(rock_ridge=False, joliet=True))
    with open_archive(path) as ar:
        assert ar.info.extra["iso.namespace"] == "joliet"
        f = ar.get("file.txt")  # Joliet preserves case
        # Joliet carries no POSIX metadata.
        assert f.mode is None and f.uid is None and f.gid is None


def test_plain_iso_namespace_and_fidelity(tmp_path: Path) -> None:
    path = tmp_path / "plain.iso"
    path.write_bytes(_build_iso(rock_ridge=False, joliet=False))
    with open_archive(path) as ar:
        assert ar.info.extra["iso.namespace"] == "iso9660"
        names = {m.name for m in ar.members()}
        # Plain ISO 9660: upper-case 8.3 names, ;version suffix stripped.
        assert "FILE.TXT" in names
        assert "DIR/" in names
        assert ar.get("FILE.TXT").mode is None  # no POSIX metadata


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def test_read_members(rock_ridge_iso: Path) -> None:
    with open_archive(rock_ridge_iso) as ar:
        assert ar.read("file.txt") == b"hello world"
        assert ar.read("subdir/n.txt") == b"nested!"
        assert ar.read("file.txt") == b"hello world"  # re-read / random access


def test_read_symlink_follows_to_target(rock_ridge_iso: Path) -> None:
    with open_archive(rock_ridge_iso) as ar:
        assert ar.read("sym") == b"hello world"


def test_read_from_seekable_stream() -> None:
    data = _build_iso(rock_ridge=True, joliet=False)
    with open_archive(io.BytesIO(data)) as ar:
        assert ar.read("file.txt") == b"hello world"


def test_read_empty_member(rock_ridge_iso: Path) -> None:
    with open_archive(rock_ridge_iso) as ar:
        assert ar.read("empty.txt") == b""


def test_seek_within_opened_member(rock_ridge_iso: Path) -> None:
    # The opened member stream is seekable (PyCdlibIO via _PyCdlibStream/DelegatingStream)
    # once SEEKABLE is declared.
    with open_archive(rock_ridge_iso, member_streams=MemberStreams.SEEKABLE) as ar:
        with ar.open("file.txt") as f:
            assert f.read(5) == b"hello"
            f.seek(0)
            assert f.read() == b"hello world"


def test_streaming_over_seekable_iso(rock_ridge_iso: Path) -> None:
    # ISO is random-access, but a streaming=True (forward-only) pass over a seekable source
    # still works and yields the members with their data.
    with open_archive(rock_ridge_iso, streaming=True) as ar:
        collected = {
            m.name: (s.read() if s is not None else None) for m, s in ar.stream_members()
        }
        assert collected["file.txt"] == b"hello world"
        assert collected["empty.txt"] == b""


def test_file_member_storage_attributes(rock_ridge_iso: Path) -> None:
    # ISO members are stored uncompressed and unencrypted, with no per-member checksum.
    with open_archive(rock_ridge_iso) as ar:
        m = ar.get("file.txt")
        assert m.type == MemberType.FILE
        assert m.size == len(b"hello world")
        assert m.compressed_size == m.size
        assert m.compression == (CompressionMethod(algo=CompressionAlgorithm.STORED),)
        assert m.is_encrypted is False
        assert not m.hashes


# ---------------------------------------------------------------------------
# Rejections: password, write, non-seekable
# ---------------------------------------------------------------------------


def test_password_rejected(rock_ridge_iso: Path) -> None:
    with pytest.raises(UnsupportedOperationError):
        open_archive(rock_ridge_iso, password=b"secret")


def test_write_rejected() -> None:
    # No ISO write backend is registered, so requesting a writer raises.
    with pytest.raises(UnsupportedOperationError):
        get_registry().writer_for_format(ArchiveFormat.ISO)


def test_non_seekable_iso_rejected() -> None:
    data = _build_iso(rock_ridge=True, joliet=False)
    with pytest.raises(StreamNotSeekableError):
        open_archive(NonSeekableBytesIO(data), format=ArchiveFormat.ISO)


# ---------------------------------------------------------------------------
# Corrupt input
# ---------------------------------------------------------------------------

# Fixed tree (same member order as corpus ``basic``); layout varies by namespace flags.
_PYCDLIB_CYCLE_ENTRIES: tuple[tuple[str, bytes, bool], ...] = (
    ("file1.txt", b"Hello, world!", False),
    ("subdir/", b"", True),
    ("empty_file.txt", b"", False),
    ("empty_subdir/", b"", True),
    ("subdir/file2.txt", b"Hello, universe!", False),
    ("implicit_subdir/file3.txt", b"Hello there!", False),
)
# ``(rock_ridge, joliet) -> (image_len, bitflip_offset, byte_before_flip, namespace)``
_PYCDLIB_CYCLE_CASES: tuple[tuple[bool, bool, int, int, int, str], ...] = (
    # Plain ISO 9660 PVD walk: ``/SUBDIR`` extent 26, +66 closes a back-edge to root 23.
    (False, False, 61440, 53314, 0x01, "iso9660"),
    # Rock Ridge PVD walk: RR padding shifts the cycle byte to +32 on the same extent.
    (True, False, 63488, 53280, 0x01, "rock_ridge"),
    # Joliet SVD walk on the RR+Joliet image (found by the mutation harness).
    (True, True, 81920, 71746, 0x01, "rock_ridge"),
)


def _build_pycdlib_cycle_fixture(*, rock_ridge: bool, joliet: bool) -> bytes:
    """ISO built from ``_PYCDLIB_CYCLE_ENTRIES`` with the requested namespaces."""
    import pycdlib

    iso = pycdlib.PyCdlib()
    kwargs: dict[str, object] = {"interchange_level": 3}
    if rock_ridge:
        kwargs["rock_ridge"] = "1.09"
    if joliet:
        kwargs["joliet"] = 3
    iso.new(**kwargs)
    made_dirs: set[str] = set()

    def _ensure_dirs(rel: str) -> None:
        parts = rel.split("/")[:-1]
        for i in range(1, len(parts) + 1):
            joined = "/".join(parts[:i])
            if joined and joined not in made_dirs:
                made_dirs.add(joined)
                iso_path = "/" + "/".join(p.upper()[:8] for p in joined.split("/"))
                iso.add_directory(
                    iso_path,
                    rr_name=parts[i - 1] if rock_ridge else None,
                    joliet_path="/" + joined if joliet else None,
                )

    counter = 0
    for name, contents, is_dir in _PYCDLIB_CYCLE_ENTRIES:
        rel = name.rstrip("/")
        _ensure_dirs(name)
        if is_dir:
            if rel not in made_dirs:
                made_dirs.add(rel)
                iso_path = "/" + "/".join(p.upper()[:8] for p in rel.split("/"))
                iso.add_directory(
                    iso_path,
                    rr_name=rel.split("/")[-1] if rock_ridge else None,
                    joliet_path="/" + rel if joliet else None,
                )
        else:
            counter += 1
            iso_dir = "/".join(p.upper()[:8] for p in rel.split("/")[:-1])
            iso_path = ("/" + iso_dir + "/" if iso_dir else "/") + f"F{counter}.TXT;1"
            iso.add_fp(
                io.BytesIO(contents),
                len(contents),
                iso_path,
                rr_name=rel.split("/")[-1] if rock_ridge else None,
                joliet_path="/" + rel if joliet else None,
            )
    out = io.BytesIO()
    iso.write_fp(out)
    iso.close()
    return out.getvalue()


def _pycdlib_directory_cycle_image(
    *,
    rock_ridge: bool,
    joliet: bool,
    expected_len: int,
    bitflip_offset: int,
    byte_before_flip: int,
) -> bytes:
    """Flip one bit in ``/subdir``'s directory extent so pycdlib's open walk cycles."""
    data = bytearray(_build_pycdlib_cycle_fixture(rock_ridge=rock_ridge, joliet=joliet))
    assert len(data) == expected_len, "fixture layout drifted — revisit cycle case table"
    assert data[bitflip_offset] == byte_before_flip
    data[bitflip_offset] ^= 0x01
    return bytes(data)


def test_corrupt_iso_raises() -> None:
    # CD001 is present (so detection still picks ISO) but the volume descriptor is cut off,
    # so pycdlib cannot parse it -> CorruptionError.
    truncated = _build_iso(rock_ridge=True, joliet=False)[:32780]
    with pytest.raises(CorruptionError):
        open_archive(io.BytesIO(truncated), format=ArchiveFormat.ISO)


@pytest.mark.timeout(5)
@pytest.mark.parametrize(
    ("rock_ridge", "joliet", "expected_len", "bitflip_offset", "byte_before_flip", "namespace"),
    [
        pytest.param(*case, id=case_id)
        for case, case_id in zip(
            _PYCDLIB_CYCLE_CASES,
            ("plain", "rock-ridge", "joliet"),
            strict=True,
        )
    ],
)
def test_pycdlib_directory_cycle_does_not_hang(
    rock_ridge: bool,
    joliet: bool,
    expected_len: int,
    bitflip_offset: int,
    byte_before_flip: int,
    namespace: str,
) -> None:
    """Regression: corrupt ``/subdir`` must not hang pycdlib during ``open_fp``.

    ``pycdlib._walk_directories`` (used for the PVD / Rock Ridge tree *and* each
    supplementary namespace such as Joliet) enqueues child directory extents with no visit
    tracking. One flipped bit in a directory record can add a child that points back at an
    ancestor extent; pycdlib then loops forever. ``open_fp`` walks every present namespace,
    so a Joliet-only cycle still bites RR+Joliet images even when archivey reads Rock Ridge.
    Without archivey's extent cycle guard these cases hang until pytest-timeout kills them.
    """
    image = _pycdlib_directory_cycle_image(
        rock_ridge=rock_ridge,
        joliet=joliet,
        expected_len=expected_len,
        bitflip_offset=bitflip_offset,
        byte_before_flip=byte_before_flip,
    )
    with open_archive(io.BytesIO(image), format=ArchiveFormat.ISO) as ar:
        assert ar.info.extra["iso.namespace"] == namespace
        names = {m.name for m in ar.members()}
    if namespace == "iso9660":
        assert "F1.TXT" in names
    else:
        assert "file1.txt" in names


def test_filesystem_oserror_propagates_unwrapped(tmp_path: Path) -> None:
    # A genuine OSError (missing file) is unrelated to ISO decoding and must propagate
    # unchanged, not be reclassified as CorruptionError (error-handling spec).
    missing = tmp_path / "does-not-exist.iso"
    with pytest.raises(FileNotFoundError):
        open_archive(missing, format=ArchiveFormat.ISO)


# ---------------------------------------------------------------------------
# Availability (FULL when pycdlib is present)
# ---------------------------------------------------------------------------


def test_iso_full_support_with_pycdlib() -> None:
    assert format_availability(ArchiveFormat.ISO).support == FormatSupport.FULL


def test_open_from_mid_positioned_stream(rock_ridge_iso: Path) -> None:
    # pycdlib addresses the image with absolute offsets; open_archive normalizes a
    # mid-positioned stream to a zero-origin view, so an embedded image still opens.
    junk = b"J" * 51
    stream = io.BytesIO(junk + rock_ridge_iso.read_bytes())
    stream.seek(len(junk))
    with open_archive(stream, format=ArchiveFormat.ISO) as ar:
        assert any(m.is_file for m in ar.members())
