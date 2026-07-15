"""Native RAR reader fixture coverage."""

from __future__ import annotations

import dataclasses
import io
import struct
import zlib
from pathlib import Path

import pytest

from archivey import ExtractionStatus, open_archive
from archivey.exceptions import (
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
                assert member.extra["rar.file_version"] == int(member_name.rsplit(";", 1)[1])
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
