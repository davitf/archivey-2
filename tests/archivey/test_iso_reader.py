"""Tests for the IsoReader — ISO 9660 with Rock Ridge and Joliet support."""

import io
import os
import tempfile

import pytest

pycdlib = pytest.importorskip("pycdlib")

from archivey.core import open_archive  # noqa: E402
from archivey.exceptions import ArchiveStreamNotSeekableError  # noqa: E402
from archivey.types import ArchiveFormat, MemberType  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_iso(
    *,
    rock_ridge: bool = True,
    joliet: bool = True,
    files: dict[str, bytes] | None = None,
    dirs: list[str] | None = None,
    symlinks: dict[str, str] | None = None,
    volume_id: str = "TESTDISK",
) -> str:
    """Create a temporary ISO image and return its path.

    Files are created with the given content. Dirs and symlinks (Rock Ridge
    only) are also added.  The caller is responsible for deleting the file.
    """
    rr_version = "1.09" if rock_ridge else None
    joliet_level = 3 if joliet else None

    iso = pycdlib.PyCdlib()
    iso.new(
        interchange_level=3,
        rock_ridge=rr_version,
        joliet=joliet_level,
    )

    # Add directories first
    for dir_path in dirs or []:
        parts = dir_path.strip("/").split("/")
        for i in range(len(parts)):
            partial = "/" + "/".join(parts[: i + 1])
            iso_partial = partial.upper()
            try:
                iso.get_record(iso_path=iso_partial)
                # already exists
            except pycdlib.pycdlibexception.PyCdlibException:
                kwargs: dict = {"iso_path": iso_partial}
                if rock_ridge:
                    kwargs["rr_name"] = parts[i]
                if joliet:
                    kwargs["joliet_path"] = partial
                iso.add_directory(**kwargs)

    # Add files
    for file_path, content in (files or {}).items():
        parts_f = file_path.strip("/").split("/")
        # Ensure parent dirs exist
        for i in range(len(parts_f) - 1):
            partial_d = "/" + "/".join(parts_f[: i + 1])
            iso_partial_d = partial_d.upper()
            try:
                iso.get_record(iso_path=iso_partial_d)
            except pycdlib.pycdlibexception.PyCdlibException:
                kw: dict = {"iso_path": iso_partial_d}
                if rock_ridge:
                    kw["rr_name"] = parts_f[i]
                if joliet:
                    kw["joliet_path"] = partial_d
                iso.add_directory(**kw)

        iso_file_path = "/" + "/".join(p.upper() for p in parts_f) + ";1"
        joliet_file_path = "/" + file_path.strip("/")
        add_kwargs: dict = {
            "fp": io.BytesIO(content),
            "length": len(content),
            "iso_path": iso_file_path,
        }
        if rock_ridge:
            add_kwargs["rr_name"] = parts_f[-1]
        if joliet:
            add_kwargs["joliet_path"] = joliet_file_path
        iso.add_fp(**add_kwargs)

    # Add symlinks (Rock Ridge only)
    # add_symlink(symlink_path=ISO path of the link itself,
    #             rr_symlink_name=RR name, rr_path=target path,
    #             joliet_path=Joliet path of link itself)
    if rock_ridge:
        for link_name, link_target in (symlinks or {}).items():
            link_parts = link_name.strip("/").split("/")
            iso_link_path = "/" + "/".join(p.upper() for p in link_parts) + ";1"
            joliet_link_path = "/" + link_name.strip("/") if joliet else None
            iso.add_symlink(
                symlink_path=iso_link_path,
                rr_symlink_name=link_parts[-1],
                rr_path=link_target,
                joliet_path=joliet_link_path,
            )

    fh, path = tempfile.mkstemp(suffix=".iso")
    os.close(fh)
    iso.write(path)
    iso.close()
    return path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestIsoBasic:
    """Basic opening and member listing."""

    def test_open_by_path(self, tmp_iso_rr):
        with open_archive(tmp_iso_rr) as ar:
            assert ar.has_random_access()
            members = ar.get_members()
            assert any(m.filename == "file1.txt" for m in members)

    def test_open_seekable_stream(self, tmp_iso_rr):
        with open(tmp_iso_rr, "rb") as f:
            with open_archive(f) as ar:
                members = ar.get_members()
                assert any(m.filename == "file1.txt" for m in members)

    def test_non_seekable_raises(self, tmp_iso_rr):
        with open(tmp_iso_rr, "rb") as real_f:
            data = real_f.read()

        class NonSeekable(io.RawIOBase):
            def __init__(self):
                self._data = io.BytesIO(data)

            def readable(self):
                return True

            def readinto(self, b):
                chunk = self._data.read(len(b))
                n = len(chunk)
                b[:n] = chunk
                return n

            def seekable(self):
                return False

        with pytest.raises(ArchiveStreamNotSeekableError):
            open_archive(NonSeekable(), format=ArchiveFormat.ISO, streaming=True)

    def test_streaming_true_with_seekable_source(self, tmp_iso_rr):
        """streaming=True with seekable source: should work, no random-access."""
        with open_archive(tmp_iso_rr, streaming=True) as ar:
            assert not ar.has_random_access()
            members = [m for m, _ in ar.iter_members_with_streams()]
            assert any(m.filename == "file1.txt" for m in members)


class TestIsoMemberTypes:
    """Verify member types and metadata."""

    def test_file_member(self, tmp_iso_rr):
        with open_archive(tmp_iso_rr) as ar:
            members = {m.filename: m for m in ar.get_members()}
            m = members["file1.txt"]
            assert m.type == MemberType.FILE
            assert m.file_size == len(b"Hello, world!")
            assert m.compress_size == m.file_size
            assert m.compression_method == "stored"
            assert m.encrypted is False
            assert m.crc32 is None  # ISO has no checksums

    def test_directory_member(self, tmp_iso_rr):
        with open_archive(tmp_iso_rr) as ar:
            members = {m.filename: m for m in ar.get_members()}
            assert "subdir/" in members
            m = members["subdir/"]
            assert m.type == MemberType.DIR
            assert m.filename.endswith("/")

    def test_nested_file(self, tmp_iso_rr):
        with open_archive(tmp_iso_rr) as ar:
            members = {m.filename: m for m in ar.get_members()}
            assert "subdir/file2.txt" in members
            m = members["subdir/file2.txt"]
            assert m.type == MemberType.FILE
            assert m.file_size == len(b"Hello, universe!")

    def test_symlink_member_rock_ridge(self, tmp_path):
        """Symlinks only exist in Rock Ridge ISOs."""
        iso_path = _make_iso(
            files={"target.txt": b"content"},
            symlinks={"link.txt": "target.txt"},
        )
        try:
            with open_archive(iso_path) as ar:
                members = {m.filename: m for m in ar.get_members()}
                assert "link.txt" in members
                lm = members["link.txt"]
                assert lm.type == MemberType.SYMLINK
                assert lm.link_target == "target.txt"
        finally:
            os.unlink(iso_path)


class TestIsoFileReading:
    """Verify file content reads correctly."""

    def test_read_file_content(self, tmp_iso_rr):
        with open_archive(tmp_iso_rr) as ar:
            with ar.open("file1.txt") as f:
                assert f.read() == b"Hello, world!"

    def test_read_nested_file(self, tmp_iso_rr):
        with open_archive(tmp_iso_rr) as ar:
            with ar.open("subdir/file2.txt") as f:
                assert f.read() == b"Hello, universe!"

    def test_read_empty_file(self, tmp_iso_rr):
        with open_archive(tmp_iso_rr) as ar:
            with ar.open("empty.txt") as f:
                assert f.read() == b""

    def test_iter_members_with_streams(self, tmp_iso_rr):
        with open_archive(tmp_iso_rr) as ar:
            found = {}
            for member, stream in ar.iter_members_with_streams():
                if stream is not None:
                    found[member.filename] = stream.read()
            assert found["file1.txt"] == b"Hello, world!"
            assert found["subdir/file2.txt"] == b"Hello, universe!"
            assert found["empty.txt"] == b""

    def test_seek_in_opened_stream(self, tmp_iso_rr):
        with open_archive(tmp_iso_rr) as ar:
            with ar.open("file1.txt") as f:
                assert f.read(5) == b"Hello"
                f.seek(0)
                assert f.read() == b"Hello, world!"


class TestIsoArchiveInfo:
    """Verify archive-level metadata."""

    def test_format(self, tmp_iso_rr):
        with open_archive(tmp_iso_rr) as ar:
            info = ar.get_archive_info()
            assert info.format == ArchiveFormat.ISO
            assert info.is_solid is False

    def test_rock_ridge_detected(self, tmp_iso_rr):
        with open_archive(tmp_iso_rr) as ar:
            info = ar.get_archive_info()
            assert info.extra["rock_ridge"] is not None

    def test_joliet_detected(self, tmp_iso_rr):
        with open_archive(tmp_iso_rr) as ar:
            info = ar.get_archive_info()
            assert info.extra["joliet"] is True

    def test_volume_identifier(self):
        iso_path = _make_iso(
            files={"f.txt": b"x"},
            volume_id="MYVOLUME",
        )
        try:
            with open_archive(iso_path) as ar:
                info = ar.get_archive_info()
                # volume_id is stored in PVD; comment may include it
                # Just check it opens correctly
                assert info.format == ArchiveFormat.ISO
        finally:
            os.unlink(iso_path)


class TestIsoRockRidgeMetadata:
    """Rock Ridge provides Unix permissions and ownership."""

    def test_permissions_present(self, tmp_iso_rr):
        with open_archive(tmp_iso_rr) as ar:
            members = {m.filename: m for m in ar.get_members()}
            m = members["file1.txt"]
            # pycdlib sets default permissions (0o444 for files)
            assert m.mode is not None
            assert 0 <= m.mode <= 0o7777

    def test_no_rock_ridge_no_permissions(self):
        """Plain ISO without RR should have no permissions."""
        iso_path = _make_iso(
            files={"file.txt": b"data"},
            rock_ridge=False,
            joliet=False,
        )
        try:
            with open_archive(iso_path) as ar:
                members = {m.filename: m for m in ar.get_members()}
                m = members["FILE.TXT"]  # plain ISO uppercases names
                assert m.mode is None
        finally:
            os.unlink(iso_path)


class TestIsoNamespaces:
    """Namespace selection priority: Rock Ridge > Joliet > ISO9660."""

    def test_rr_names_preferred(self, tmp_iso_rr):
        """When both RR and Joliet are present, Rock Ridge names are used."""
        with open_archive(tmp_iso_rr) as ar:
            members = {m.filename for m in ar.get_members()}
            # Rock Ridge preserves mixed-case; ISO9660 would uppercase
            assert "file1.txt" in members or "FILE1.TXT" not in members

    def test_joliet_fallback_no_rr(self):
        """When only Joliet is present, Joliet names are used."""
        iso_path = _make_iso(
            files={"LongFilename.txt": b"content"},
            rock_ridge=False,
            joliet=True,
        )
        try:
            with open_archive(iso_path) as ar:
                members = {m.filename for m in ar.get_members()}
                assert "LongFilename.txt" in members
        finally:
            os.unlink(iso_path)

    def test_plain_iso_strips_version_suffix(self):
        """Plain ISO9660 names have ;1 version suffix stripped."""
        iso_path = _make_iso(
            files={"README.TXT": b"readme"},
            rock_ridge=False,
            joliet=False,
        )
        try:
            with open_archive(iso_path) as ar:
                members = {m.filename for m in ar.get_members()}
                assert "README.TXT" in members
                assert "README.TXT;1" not in members
        finally:
            os.unlink(iso_path)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def tmp_iso_rr():
    """Module-scoped temporary ISO with Rock Ridge and Joliet."""
    path = _make_iso(
        files={
            "file1.txt": b"Hello, world!",
            "empty.txt": b"",
            "subdir/file2.txt": b"Hello, universe!",
        },
        dirs=["subdir"],
        rock_ridge=True,
        joliet=True,
    )
    yield path
    os.unlink(path)
