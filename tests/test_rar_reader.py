"""Native RAR reader fixture coverage."""

from __future__ import annotations

import dataclasses
import io
import struct
import time
import zlib
from pathlib import Path

import pytest

from archivey import ExtractionStatus, open_archive
from archivey.exceptions import (
    ArchiveyError,
    CorruptionError,
    EncryptionError,
    PackageNotInstalledError,
    TruncatedError,
)
from archivey.internal.backends import rar_unrar
from archivey.internal.backends.rar_parser import RAR5_ID, RAR_ID, parse_rar_archive
from archivey.types import MemberType
from tests.conftest import requires, requires_binary

_FIXTURES = Path(__file__).parent / "fixtures" / "rar"

_BASIC_CONTENTS = {
    "file1.txt": b"Hello, world!",
    "empty_file.txt": b"",
    "subdir/file2.txt": b"Hello, universe!",
    "implicit_subdir/file3.txt": b"Hello there!",
}


def _fixture(name: str) -> Path:
    path = _FIXTURES / name
    if not path.is_file():
        pytest.skip(f"missing vendored fixture {name}")
    return path


@requires_binary("unrar")
@pytest.mark.parametrize(
    "name",
    ["basic_nonsolid__.rar", "basic_nonsolid__rar4.rar"],
)
def test_basic_nonsolid_list_and_read(name: str) -> None:
    with open_archive(_fixture(name)) as archive:
        assert archive.info.is_solid is False
        files = {m.name: m for m in archive.members() if m.is_file}
        assert set(files) == set(_BASIC_CONTENTS)
        for member_name, expected in _BASIC_CONTENTS.items():
            assert archive.read(files[member_name]) == expected


@requires_binary("unrar")
@pytest.mark.parametrize(
    "name",
    ["basic_solid__.rar", "basic_solid__rar4.rar"],
)
def test_basic_solid_stream_and_random(name: str) -> None:
    with open_archive(_fixture(name)) as archive:
        assert archive.info.is_solid is True
        streamed = {
            member.name: stream.read()
            for member, stream in archive.stream_members()
            if member.is_file and stream is not None
        }
        assert streamed == _BASIC_CONTENTS
        assert archive.read("file1.txt") == _BASIC_CONTENTS["file1.txt"]


@requires_binary("unrar")
@pytest.mark.parametrize(
    "name",
    ["symlinks_solid__.rar", "symlinks_solid__rar4.rar"],
)
def test_solid_symlink_demux_and_link_targets(name: str) -> None:
    with open_archive(_fixture(name)) as archive:
        assert archive.info.is_solid is True
        by_name = {m.name: m for m in archive.members()}
        assert by_name["symlink_to_file1.txt"].type is MemberType.SYMLINK
        assert by_name["symlink_to_file1.txt"].link_target == "file1.txt"
        assert by_name["subdir/link_to_file1.txt"].link_target == "../file1.txt"

        payload_names: list[str] = []
        pipe_bytes = 0
        for member, stream in archive.stream_members():
            if member.is_file and stream is not None:
                data = stream.read()
                payload_names.append(member.name)
                pipe_bytes += len(data)
                assert data == b"Hello, world!"
            else:
                assert stream is None
                assert member.type in (
                    MemberType.SYMLINK,
                    MemberType.DIRECTORY,
                    MemberType.HARDLINK,
                )
        # Only payload FILE members advance the unrar p pipe.
        assert payload_names == ["file1.txt"]
        assert pipe_bytes == 13


@requires_binary("unrar")
def test_solid_hardlink_demux_and_targets() -> None:
    with open_archive(_fixture("hardlinks_solid__.rar")) as archive:
        assert archive.info.is_solid is True
        by_name = {m.name: m for m in archive.members()}
        assert by_name["subdir/hardlink_to_file1.txt"].type is MemberType.HARDLINK
        assert by_name["subdir/hardlink_to_file1.txt"].link_target == "file1.txt"
        assert by_name["hardlink_to_file2.txt"].link_target == "subdir/file2.txt"

        payloads = {
            member.name: stream.read()
            for member, stream in archive.stream_members()
            if member.is_file and stream is not None
        }
        assert payloads == {
            "file1.txt": b"Hello 1!",
            "subdir/file2.txt": b"Hello 2!",
        }
        assert archive.read("subdir/hardlink_to_file1.txt") == b"Hello 1!"


@requires("cryptography")
@requires_binary("unrar")
@pytest.mark.parametrize(
    "name",
    ["encrypted_header__.rar", "encrypted_header__rar4.rar"],
)
def test_encrypted_header_lists_with_password(name: str) -> None:
    path = _fixture(name)
    with pytest.raises(EncryptionError):
        open_archive(path)
    with open_archive(path, password="header_password") as archive:
        assert archive.info.is_encrypted is True
        assert archive.read("file1.txt") == b"Hello, world!"


@requires_binary("unrar")
@pytest.mark.parametrize(
    "name",
    ["encryption__.rar", "encryption__rar4.rar"],
)
def test_encrypted_data_requires_password(name: str) -> None:
    path = _fixture(name)
    with open_archive(path) as archive:
        assert archive.info.is_encrypted is True
        with pytest.raises((EncryptionError, CorruptionError)):
            archive.read("secret.txt")
    with open_archive(path, password="password") as archive:
        assert archive.read("secret.txt") == b"This is secret"
        assert archive.read("also_secret.txt") == b"This is also secret"


@requires_binary("unrar")
def test_stored_m0_direct_read() -> None:
    with open_archive(_fixture("stored_m0.rar")) as archive:
        member = next(m for m in archive.members() if m.is_file)
        assert member.compression[0].algo.name == "STORED"
        assert archive.read(member) == b"stored payload"


_FILE_VERSION_CONTENTS = {
    "file.txt;1": b"version-one",
    "file.txt;2": b"version-two!!",
    "file.txt": b"version-three!!!",
}


@requires_binary("unrar")
@pytest.mark.parametrize(
    "name",
    ["file_version__.rar", "file_version__rar4.rar"],
)
def test_file_version_list_and_read(name: str) -> None:
    with open_archive(_fixture(name)) as archive:
        files = {m.name: m for m in archive.members() if m.is_file}
        assert set(files) == set(_FILE_VERSION_CONTENTS)
        for member_name, expected in _FILE_VERSION_CONTENTS.items():
            member = files[member_name]
            if member_name == "file.txt":
                assert member.is_current is True
                assert "rar.file_version" not in member.extra
            else:
                assert member.is_current is False
                assert member.extra["rar.file_version"] == int(
                    member_name.rsplit(";", 1)[1]
                )
            assert archive.read(member) == expected
            assert archive.read(member_name) == expected


@requires_binary("unrar")
@pytest.mark.parametrize(
    "name",
    ["file_version__.rar", "file_version__rar4.rar"],
)
def test_file_version_extract_all_skips_history(name: str, tmp_path: Path) -> None:
    dest = tmp_path / "out"
    dest.mkdir()
    with open_archive(_fixture(name)) as archive:
        results = archive.extract_all(dest).results
    by_name = {r.member.name: r for r in results if r.member.is_file}
    assert by_name["file.txt;1"].status is ExtractionStatus.SKIPPED
    assert by_name["file.txt;2"].status is ExtractionStatus.SKIPPED
    assert by_name["file.txt"].status is ExtractionStatus.EXTRACTED
    assert (dest / "file.txt").read_bytes() == _FILE_VERSION_CONTENTS["file.txt"]
    assert not (dest / "file.txt;1").exists()
    assert not (dest / "file.txt;2").exists()


@requires_binary("unrar")
def test_file_version_solid_demux_aligned() -> None:
    expected = {
        "a.txt;1": b"AAA-v1",
        "b.txt": b"BBB-payload",
        "a.txt": b"AAA-v2-longer",
    }
    with open_archive(_fixture("file_version_solid__.rar")) as archive:
        assert archive.info.is_solid is True
        streamed = {
            member.name: stream.read()
            for member, stream in archive.stream_members()
            if member.is_file and stream is not None
        }
        assert streamed == expected
        for member_name, payload in expected.items():
            assert archive.read(member_name) == payload
        history = archive.get("a.txt;1")
        assert history is not None
        assert history.is_current is False
        assert history.extra["rar.file_version"] == 1


@requires_binary("unrar")
def test_blake2sp_only_hash() -> None:
    with open_archive(_fixture("blake2sp.rar")) as archive:
        member = next(m for m in archive.members() if m.is_file)
        assert "crc32" not in member.hashes
        assert "blake2sp" in member.hashes
        assert archive.read(member) == b"stored payload"


@requires_binary("unrar")
def test_blake2sp_verified_no_unverifiable_diagnostic(
    caplog: pytest.LogCaptureFixture,
) -> None:
    import logging

    with caplog.at_level(logging.WARNING, logger="archivey.integrity"):
        with open_archive(_fixture("blake2sp.rar")) as archive:
            member = next(m for m in archive.members() if m.is_file)
            assert archive.read(member) == b"stored payload"
            assert archive.diagnostics.total_count == 0
    assert not any(
        "Cannot verify digest 'blake2sp'" in rec.message for rec in caplog.records
    )


@requires_binary("unrar")
def test_blake2sp_corrupt_payload_raises(tmp_path: Path) -> None:
    raw = _fixture("blake2sp.rar").read_bytes()
    payload = b"stored payload"
    offset = raw.find(payload)
    assert offset >= 0
    mutated = bytearray(raw)
    mutated[offset] ^= 0x01
    corrupt = tmp_path / "blake2sp_corrupt.rar"
    corrupt.write_bytes(mutated)
    with open_archive(corrupt) as archive:
        member = next(m for m in archive.members() if m.is_file)
        assert "blake2sp" in member.hashes
        with pytest.raises(CorruptionError, match="blake2sp"):
            archive.read(member)


@requires_binary("unrar")
def test_blake2sp_unrar_oracle_crosscheck() -> None:
    import shutil
    import subprocess

    if shutil.which("unrar") is None:
        pytest.skip("unrar unavailable")
    fixture = _fixture("blake2sp.rar")
    with open_archive(fixture) as archive:
        member = next(m for m in archive.members() if m.is_file)
        native = archive.read(member)
    proc = subprocess.run(
        ["unrar", "p", "-inul", str(fixture)],
        check=True,
        capture_output=True,
    )
    assert proc.stdout == native == b"stored payload"


@requires_binary("unrar")
def test_multi_volume_roundtrip() -> None:
    part1 = _fixture("tinyvol.part1.rar")
    assert (_FIXTURES / "tinyvol.part2.rar").is_file()
    with open_archive(part1) as archive:
        assert archive.info.is_multivolume is True
        assert archive.info.extra.get("rar.volume_count") == 2
        data = archive.read("payload.bin")
        assert data == b"ABCDEFGH" * 200


@requires_binary("unrar")
def test_multi_volume_rnn_roundtrip() -> None:
    """Classic RAR4 volumes: ``name.rar`` + ``name.r00`` (``-vn`` naming)."""
    first = _fixture("tinyvol_rnn.rar")
    assert (_FIXTURES / "tinyvol_rnn.r00").is_file()
    with open_archive(first) as archive:
        assert archive.info.is_multivolume is True
        assert archive.info.extra.get("rar.volume_count") == 2
        assert archive.read("payload.bin") == b"ABCDEFGH" * 200


@requires_binary("unrar")
def test_multi_volume_stream_materialization() -> None:
    paths = [_fixture("tinyvol.part1.rar"), _FIXTURES / "tinyvol.part2.rar"]
    streams = [p.open("rb") for p in paths]
    try:
        with open_archive(streams) as archive:
            assert archive.info.is_multivolume is True
            assert archive.read("payload.bin") == b"ABCDEFGH" * 200
    finally:
        for stream in streams:
            stream.close()


def test_incomplete_multi_volume_raises() -> None:
    # Lone volume-1 sibling with volume/next flags and no part2.
    part1 = _fixture("tinyvol.part1.rar").read_bytes()
    with pytest.raises(TruncatedError, match="multi-volume"):
        open_archive(io.BytesIO(part1))


@requires_binary("unrar")
@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("rar15-comment.rar", {"FILE1.TXT": b"foooo\r\n", "FILE2.TXT": b"baaaar\r\n"}),
        (
            "rar202-comment-nopsw.rar",
            {"FILE1.TXT": b"file1\r\n", "FILE2.TXT": b"file2\r\n"},
        ),
    ],
)
def test_rar15_and_rar2_list_and_read(name: str, expected: dict[str, bytes]) -> None:
    """RAR 1.5 / 2.x archives list and read via native headers + unrar."""
    with open_archive(_fixture(name)) as archive:
        files = {m.name: m for m in archive.members() if m.is_file}
        assert set(files) == set(expected)
        for member_name, payload in expected.items():
            assert archive.read(files[member_name]) == payload


def test_extract_version_20_payload_accepted() -> None:
    """Craft a RAR3 archive whose payload FILE declares extract version 20."""
    from archivey.internal.backends.rar_parser import _RAR3_LONG_BLOCK, _crc32

    name = b"x.txt"
    payload = b"hi"
    # FILE fields include pack_size as the first le32; with LONG_BLOCK that
    # field is also the block add_size so the parser skips the payload.
    file_fields = struct.pack(
        "<LLBLLBBHL",
        len(payload),
        len(payload),
        3,  # Unix
        zlib.crc32(payload) & 0xFFFFFFFF,
        0,
        20,  # extract version → also used by RAR2-era and some RAR3 stored members
        0x30,  # M0
        len(name),
        0o100644,
    )
    file_fields += name
    flags = _RAR3_LONG_BLOCK
    header_without_crc = struct.pack(
        "<BHH",
        0x74,
        flags,
        7 + len(file_fields),
    )
    header_without_crc += file_fields
    file_crc = _crc32(header_without_crc) & 0xFFFF
    file_hdr = struct.pack("<H", file_crc) + header_without_crc

    main_body = b"\x00" * 6  # reserved
    main_without_crc = struct.pack("<BHH", 0x73, 0, 7 + len(main_body)) + main_body
    main_crc = _crc32(main_without_crc) & 0xFFFF
    main_hdr = struct.pack("<H", main_crc) + main_without_crc

    end_without_crc = struct.pack("<BHH", 0x7B, 0, 7)
    end_crc = _crc32(end_without_crc) & 0xFFFF
    end_hdr = struct.pack("<H", end_crc) + end_without_crc

    blob = RAR_ID + main_hdr + file_hdr + payload + end_hdr
    archive = parse_rar_archive(io.BytesIO(blob))
    assert len(archive.members) == 1
    member = archive.members[0]
    assert member.filename == "x.txt"
    assert member.extract_version == 20
    assert member.file_size == 2
    assert member.compress_size == 2


def _rar3_file_block(
    name: bytes,
    *,
    flags: int,
    pack_lo: int,
    unp_lo: int,
    pack_hi: int = 0,
    unp_hi: int = 0,
    method: int = 0x30,
) -> bytes:
    """Build one RAR3 FILE block with a valid 16-bit header CRC."""
    from archivey.internal.backends.rar_parser import (
        _RAR3_FILE_LARGE,
        _RAR3_LONG_BLOCK,
        _crc32,
    )

    file_fields = struct.pack(
        "<LLBLLBBHL",
        pack_lo,
        unp_lo,
        3,  # Unix
        0,  # crc32
        0,  # dos time
        20,  # extract version
        method,
        len(name),
        0o100644,
    )
    if flags & _RAR3_FILE_LARGE:
        file_fields += struct.pack("<LL", pack_hi, unp_hi)
    file_fields += name
    flags |= _RAR3_LONG_BLOCK
    header_without_crc = struct.pack("<BHH", 0x74, flags, 7 + len(file_fields))
    header_without_crc += file_fields
    file_crc = _crc32(header_without_crc) & 0xFFFF
    return struct.pack("<H", file_crc) + header_without_crc


def _rar3_main_and_end() -> tuple[bytes, bytes]:
    from archivey.internal.backends.rar_parser import _crc32

    main_without_crc = struct.pack("<BHH", 0x73, 0, 7 + 6) + b"\x00" * 6
    main_hdr = struct.pack("<H", _crc32(main_without_crc) & 0xFFFF) + main_without_crc
    end_without_crc = struct.pack("<BHH", 0x7B, 0, 7)
    end_hdr = struct.pack("<H", _crc32(end_without_crc) & 0xFFFF) + end_without_crc
    return main_hdr, end_hdr


def test_rar3_large_packed_member_skips_full_64bit_size() -> None:
    """F5: a RAR3 ``FILE_LARGE`` member's packed-data skip must use the full 64-bit
    size (HIGH_PACK_SIZE), not just the low 32 bits.

    The first member claims a 4 GiB packed size (low 32 = 0, high = 1) with no actual
    data, immediately followed by a second FILE header. Skipping only the low 32 bits
    (0 bytes) would misparse that second header as a member; skipping the full 4 GiB
    lands past EOF, so exactly one member is seen.
    """
    from archivey.internal.backends.rar_parser import _RAR3_FILE_LARGE

    main_hdr, end_hdr = _rar3_main_and_end()
    big = _rar3_file_block(
        b"big.bin", flags=_RAR3_FILE_LARGE, pack_lo=0, unp_lo=0, pack_hi=1, unp_hi=1
    )
    trailing = _rar3_file_block(b"sneaky.txt", flags=0, pack_lo=0, unp_lo=0)
    blob = RAR_ID + main_hdr + big + trailing + end_hdr
    archive = parse_rar_archive(io.BytesIO(blob))
    assert [m.filename for m in archive.members] == ["big.bin"]
    assert archive.members[0].compress_size == 1 << 32


def test_rar3_mismatched_split_continuation_is_corruption() -> None:
    """F6: a SPLIT_BEFORE continuation that names a different file after a non-split
    member must not be silently merged into the previous member."""
    from archivey.internal.backends.rar_parser import _RAR3_FILE_SPLIT_BEFORE

    main_hdr, end_hdr = _rar3_main_and_end()
    first = _rar3_file_block(b"a.txt", flags=0, pack_lo=0, unp_lo=0)
    forged = _rar3_file_block(
        b"b.txt", flags=_RAR3_FILE_SPLIT_BEFORE, pack_lo=0, unp_lo=0
    )
    blob = RAR_ID + main_hdr + first + forged + end_hdr
    with pytest.raises(CorruptionError):
        parse_rar_archive(io.BytesIO(blob))


def test_rar3_same_name_split_before_without_split_after_is_corruption() -> None:
    """F6: same filename + SPLIT_BEFORE still rejects when the previous part was not
    marked SPLIT_AFTER (not a genuine volume continuation)."""
    from archivey.internal.backends.rar_parser import _RAR3_FILE_SPLIT_BEFORE

    main_hdr, end_hdr = _rar3_main_and_end()
    first = _rar3_file_block(b"a.txt", flags=0, pack_lo=0, unp_lo=0)
    cont = _rar3_file_block(
        b"a.txt", flags=_RAR3_FILE_SPLIT_BEFORE, pack_lo=0, unp_lo=0
    )
    blob = RAR_ID + main_hdr + first + cont + end_hdr
    with pytest.raises(CorruptionError):
        parse_rar_archive(io.BytesIO(blob))


def test_rar3_split_after_then_different_name_is_corruption() -> None:
    """F6: a SPLIT_AFTER part followed by SPLIT_BEFORE with a different name is not a
    continuation — reject rather than fold the unrelated member's size/CRC in."""
    from archivey.internal.backends.rar_parser import (
        _RAR3_FILE_SPLIT_AFTER,
        _RAR3_FILE_SPLIT_BEFORE,
    )

    main_hdr, end_hdr = _rar3_main_and_end()
    first = _rar3_file_block(
        b"a.txt", flags=_RAR3_FILE_SPLIT_AFTER, pack_lo=0, unp_lo=0
    )
    forged = _rar3_file_block(
        b"b.txt", flags=_RAR3_FILE_SPLIT_BEFORE, pack_lo=0, unp_lo=0
    )
    blob = RAR_ID + main_hdr + first + forged + end_hdr
    with pytest.raises(CorruptionError):
        parse_rar_archive(io.BytesIO(blob))


def test_rar3_matching_split_continuation_merges() -> None:
    """F6 positive path: same name + previous SPLIT_AFTER collapses into one member."""
    from archivey.internal.backends.rar_parser import (
        _RAR3_FILE_SPLIT_AFTER,
        _RAR3_FILE_SPLIT_BEFORE,
    )

    main_hdr, end_hdr = _rar3_main_and_end()
    first = _rar3_file_block(
        b"a.txt", flags=_RAR3_FILE_SPLIT_AFTER, pack_lo=3, unp_lo=3
    )
    cont = _rar3_file_block(
        b"a.txt", flags=_RAR3_FILE_SPLIT_BEFORE, pack_lo=5, unp_lo=5
    )
    # Each FILE header's claimed pack size must be skipped before the next header.
    blob = RAR_ID + main_hdr + first + b"AAA" + cont + b"BBBBB" + end_hdr
    archive = parse_rar_archive(io.BytesIO(blob))
    assert [m.filename for m in archive.members] == ["a.txt"]
    assert archive.members[0].compress_size == 8
    assert archive.members[0].spanned_volumes is True


def test_rar5_hostile_packed_size_is_corruption() -> None:
    """Atheris: huge RAR5 vint add_size must not raise raw OverflowError on seek."""
    import zlib

    def vint(n: int) -> bytes:
        out = bytearray()
        while True:
            b = n & 0x7F
            n >>= 7
            if n:
                out.append(b | 0x80)
            else:
                out.append(b)
                return bytes(out)

    def block(body: bytes) -> bytes:
        header_wo_crc = vint(len(body)) + body
        crc = zlib.crc32(header_wo_crc) & 0xFFFFFFFF
        return struct.pack("<I", crc) + header_wo_crc

    main = block(vint(1) + vint(0) + vint(0))  # MAIN, no flags, main_flags=0
    hostile = block(vint(99) + vint(0x02) + vint(1 << 70))  # unknown + DATA + huge
    blob = RAR5_ID + main + hostile
    with pytest.raises(CorruptionError, match="seekable range|packed size"):
        parse_rar_archive(io.BytesIO(blob))


def test_rar5_out_of_range_windowstime_is_tolerated() -> None:
    """Atheris: hostile FILETIME must not raise raw ValueError from fromtimestamp."""
    from archivey.internal.backends.rar_parser import _load_windowstime

    # FILETIME ticks far beyond datetime's year range.
    buf = struct.pack("<II", 0xFFFFFFFF, 0x7FFFFFFF)
    dt, pos = _load_windowstime(buf, 0)
    assert dt is None
    assert pos == 8


def test_rar_reader_masks_hostile_unix_mode() -> None:
    """Atheris: huge RAR5 mode vint must not OverflowError in ``stat.S_IMODE``."""
    from archivey.internal.backends.rar_parser import RarMemberInfo
    from archivey.internal.backends.rar_reader import RarReader

    info = RarMemberInfo(
        filename="a.txt",
        orig_filename=b"a.txt",
        file_size=0,
        compress_size=0,
        compress_type=0x30,
        crc32=None,
        blake2sp_hash=None,
        mtime=None,
        mode=(1 << 80) | 0o100644,
        host_os=3,
        flags=0,
        file_redir=None,
        file_encryption=None,
        header_offset=0,
        header_size=0,
        data_offset=0,
        extract_version=50,
        file_solid=False,
        is_directory=False,
        is_symlink=False,
        is_hardlink_or_copy=False,
        is_encrypted=False,
        volume_index=0,
        split_before=False,
        split_after=False,
    )
    # Build a reader without opening a real archive — call the mapper directly.
    reader = object.__new__(RarReader)
    reader._diagnostics_collector = None
    reader._archive_name = "<test>"
    member = RarReader._to_member(reader, info)
    assert member.mode == 0o0644

    # Win32 attrs are masked to 32 bits (FILE_ATTRIBUTE_* width).
    info_win = dataclasses.replace(info, host_os=2, mode=(1 << 40) | 0x20)
    member_win = RarReader._to_member(reader, info_win)
    assert member_win.mode is None
    assert member_win.windows_attrs == 0x20


def test_non_rarlab_unrar_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = tmp_path / "unrar"
    fake.write_text("#!/bin/sh\necho 'unrar-free fake'\n")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(rar_unrar, "_cached_unrar", None)
    with pytest.raises(PackageNotInstalledError, match="RARLAB"):
        rar_unrar.find_rarlab_unrar()


def test_missing_unrar_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(rar_unrar, "_cached_unrar", None)
    with pytest.raises(PackageNotInstalledError, match="RARLAB"):
        rar_unrar.find_rarlab_unrar()


@requires("cryptography")
def test_header_crypto_gating(monkeypatch: pytest.MonkeyPatch) -> None:
    from archivey.internal.streams import crypto

    monkeypatch.setattr(crypto, "_crypto_available", lambda: False)
    with pytest.raises(PackageNotInstalledError, match="cryptography"):
        open_archive(_fixture("encrypted_header__.rar"), password="header_password")


def test_rar_parser_bounds_member_count(monkeypatch: pytest.MonkeyPatch) -> None:
    """Member-table bombs must fail at parse, not OOM (mirrors 7z header-size bound)."""
    import archivey.internal.backends.rar_parser as rar_parser

    monkeypatch.setattr(rar_parser, "_MAX_ARCHIVE_MEMBERS", 2)
    with pytest.raises(CorruptionError, match="member count exceeds"):
        parse_rar_archive(_fixture("basic_nonsolid__.rar").open("rb"))


def test_rar_members_enforces_listing_limits() -> None:
    from archivey import ArchiveyConfig, ListingLimits, ResourceLimitError

    cfg = ArchiveyConfig(listing_limits=ListingLimits(max_members=2))
    with open_archive(_fixture("basic_nonsolid__.rar"), config=cfg) as reader:
        with pytest.raises(ResourceLimitError, match="max_members"):
            reader.members()


def test_fix_rar3_astral_truncation() -> None:
    """RAR3 compresses names as UTF-16, which truncates non-BMP chars to a PUA/surrogate
    code unit; the 8-bit name field is preferred when it recovers the real character."""
    from archivey.internal.backends.rar_parser import _fix_rar3_astral_truncation

    # U+1F600 truncated to U+F600 in the UTF-16 name; the UTF-8 8-bit name recovers it.
    truncated = "emoji_\uf600.txt"
    recovered = "emoji_\U0001f600.txt"
    assert _fix_rar3_astral_truncation(truncated, recovered.encode()) == recovered
    # No 8-bit/UTF-16 disagreement -> keep the decompressed name unchanged.
    assert _fix_rar3_astral_truncation("plain.txt", b"plain.txt") == "plain.txt"
    # A PUA char present in both fields (genuine, not a truncation) is preserved.
    pua = "\uf600.txt"
    assert _fix_rar3_astral_truncation(pua, pua.encode()) == pua
    # An 8-bit field that is not valid UTF-8 cannot override; keep the UTF-16 name.
    assert _fix_rar3_astral_truncation("name.txt", b"\xff\xfe") == "name.txt"


def test_rar3_non_bmp_filename_not_truncated() -> None:
    """Regression: an emoji in a RAR3 name must survive as U+1F600, not the PUA U+F600
    the raw UTF-16 field decodes to (external fixture from the v1 reader's bug)."""
    with open_archive(_fixture("encoding__rar4.rar")) as archive:
        names = {m.name for m in archive.members()}
    assert "emoji_😀.txt" in names
    # None of the recovered names retain a surrogate/PUA truncation artifact.
    for name in names:
        assert not any(
            0xE000 <= ord(c) <= 0xF8FF or 0xD800 <= ord(c) <= 0xDFFF for c in name
        )


# Fixtures built by review/next/01-rar-reader-findings/make_hostile_fixtures.py:
# nonsolid, compressed members whose stored names are a bare unrar switch and an
# ``@listfile`` argument, alongside a normal control member.
_HOSTILE_ARGV_CONTENTS = {
    "canary.txt": b"CANARY-CANARY-CANARY-\n" * 64,
    "-inul": b"DASH-INUL-PAYLOAD-\n" * 64,
    "@atfile": b"AT-ATFILE-PAYLOAD-\n" * 64,
}


@requires_binary("unrar")
@pytest.mark.parametrize("name", ["hostile_argv__.rar", "hostile_argv__rar4.rar"])
def test_hostile_member_name_reads_its_own_bytes(name: str) -> None:
    """F3 (review/next/01-rar-reader-findings/unrar-boundary.md): a member whose
    stored name is a bare ``unrar`` switch (``-inul``) or an ``@listfile`` argument
    (``@atfile``) must be addressed to exactly that member.

    Fixed by passing the member via a ``-n./`` include mask instead of positionally,
    so ``unrar`` cannot parse the name as a switch or a local-file read. Each hostile
    member now returns its own bytes, and never another member's.
    """
    with open_archive(_fixture(name)) as archive:
        members = {m.name: m for m in archive.members() if m.is_file}
        assert {"canary.txt", "-inul", "@atfile"} <= set(members)
        for member_name, expected in _HOSTILE_ARGV_CONTENTS.items():
            assert archive.read(members[member_name]) == expected, (
                f"reading {member_name!r} did not return its own bytes (F3 argv injection)"
            )


def test_rar5_header_size_vint_is_bounded() -> None:
    """F2: the RAR5 header-size vint pre-read is length-capped, so a crafted run of
    continuation bytes cannot drive an unbounded, O(n^2) read of the source."""
    payload = RAR5_ID + b"\x00\x00\x00\x00" + b"\x80" * 2_000_000
    start = time.perf_counter()
    with pytest.raises(ArchiveyError):
        parse_rar_archive(io.BytesIO(payload))
    # Bounded work: the cap rejects after a handful of bytes rather than reading 2 MB.
    assert time.perf_counter() - start < 1.0


def test_unrar_member_include_switch_rejects_wildcards() -> None:
    """A member name containing an unrar wildcard (``*``/``?``) cannot be addressed to
    one member (no escape exists), so the unrar path refuses it with a typed error;
    other names become a ``-n./`` include mask that neutralizes ``-``/``@`` prefixes."""
    from archivey.exceptions import UnsupportedFeatureError
    from archivey.internal.backends.rar_unrar import _member_include_switch

    assert _member_include_switch("-inul") == "-n./-inul"
    assert _member_include_switch("@atfile") == "-n./@atfile"
    assert _member_include_switch("dir/normal.txt") == "-n./dir/normal.txt"
    for bad in ("weird*.txt", "a?b.txt"):
        with pytest.raises(UnsupportedFeatureError):
            _member_include_switch(bad)


@requires("cryptography")
@pytest.mark.parametrize(
    "name", ["encrypted_header__.rar", "encrypted_header__rar4.rar"]
)
def test_header_encryption_wrong_password_is_encryption_error(name: str) -> None:
    """F1: a wrong header password surfaces as ``EncryptionError`` (not
    ``CorruptionError``) even without a check value (always RAR3, checkval-less RAR5),
    so password-candidate iteration keeps trying instead of aborting."""
    path = _fixture(name)
    with pytest.raises(EncryptionError):
        with open_archive(path, password="DEFINITELY_WRONG") as archive:
            archive.members()
    # A candidate list whose first entry is wrong must fall through to the correct one.
    with open_archive(
        path, password=["DEFINITELY_WRONG", "header_password"]
    ) as archive:
        assert len(archive.members()) > 0


class _FakeUnrarProc:
    """Minimal ``Popen`` stand-in for ``_UnrarOwnedStream`` exit-code tests."""

    def __init__(self, returncode: int) -> None:
        self.returncode = returncode

    def poll(self) -> int:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        return self.returncode

    def terminate(self) -> None:
        return None

    def kill(self) -> None:
        return None


def _close_unrar_owned(
    *,
    rc: int,
    named_member: bool = False,
    has_verifiable_hash: bool = False,
) -> None:
    from archivey.internal.backends.rar_reader import _UnrarOwnedStream

    stream = _UnrarOwnedStream(
        io.BytesIO(b""),
        _FakeUnrarProc(rc),  # type: ignore[arg-type]
        named_member=named_member,
        has_verifiable_hash=has_verifiable_hash,
    )
    stream.close()


def test_unrar_owned_stream_maps_exit_11_to_encryption_error() -> None:
    """F4: unrar exit 11 (bad password) always maps, even when a hash is present."""
    with pytest.raises(EncryptionError):
        _close_unrar_owned(rc=11, has_verifiable_hash=True)


@pytest.mark.parametrize("rc", [2, 3])
def test_unrar_owned_stream_maps_fatal_crc_when_no_hash(rc: int) -> None:
    """F4: exits 2/3 map to CorruptionError when archivey has no verifiable hash."""
    with pytest.raises(CorruptionError, match="fatal or CRC"):
        _close_unrar_owned(rc=rc, named_member=True, has_verifiable_hash=False)


@pytest.mark.parametrize("rc", [2, 3])
def test_unrar_owned_stream_suppresses_fatal_crc_when_hash_present(rc: int) -> None:
    """F4: with a verifiable hash, archivey's digest check is authoritative — ignore
    unrar's sometimes-spurious CRC/fatal codes (legacy RAR 1.5 false positives)."""
    _close_unrar_owned(rc=rc, named_member=True, has_verifiable_hash=True)


def test_unrar_owned_stream_maps_exit_10_for_named_open() -> None:
    """F4: exit 10 (no files matched) on a named ``-n`` open is CorruptionError."""
    with pytest.raises(CorruptionError, match="no matching member"):
        _close_unrar_owned(rc=10, named_member=True, has_verifiable_hash=False)


def test_unrar_owned_stream_suppresses_exit_10_when_hash_present() -> None:
    """F4: exit 10 is also suppressed when archivey verifies the member itself."""
    _close_unrar_owned(rc=10, named_member=True, has_verifiable_hash=True)


def test_unrar_owned_stream_ignores_exit_10_on_solid_all_pipe() -> None:
    """F4: exit 10 on the solid ALL-pipe is not an error (empty match is expected
    when no named filter is used)."""
    _close_unrar_owned(rc=10, named_member=False, has_verifiable_hash=False)


def test_unrar_owned_stream_success_and_warning_pass() -> None:
    """F4: exits 0 (success) and 1 (warning) close cleanly."""
    _close_unrar_owned(rc=0, named_member=True)
    _close_unrar_owned(rc=1, named_member=True)


def test_unrar_owned_stream_negative_rc_from_terminate_is_not_error() -> None:
    """F4: a negative return code means we terminated the process (early close)."""
    _close_unrar_owned(rc=-15, named_member=True, has_verifiable_hash=False)
