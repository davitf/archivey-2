"""Native RAR reader fixture coverage."""

from __future__ import annotations

import io
import struct
import zlib
from pathlib import Path

import pytest

from archivey import open_archive
from archivey.exceptions import (
    CorruptionError,
    EncryptionError,
    PackageNotInstalledError,
    TruncatedError,
    UnsupportedFeatureError,
)
from archivey.internal.backends import rar_unrar
from archivey.internal.backends.rar_parser import RAR_ID, parse_rar_archive
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


@requires_binary("unrar")
def test_blake2sp_only_hash() -> None:
    with open_archive(_fixture("blake2sp.rar")) as archive:
        member = next(m for m in archive.members() if m.is_file)
        assert "crc32" not in member.hashes
        assert "blake2sp" in member.hashes
        assert archive.read(member) == b"stored payload"


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


def test_rar2_extract_version_rejected() -> None:
    """Craft a RAR3 archive whose payload FILE declares extract version 20."""
    # Minimal RAR3: mark + main + one stored file (unp_ver=20) + endarc.
    # Header CRCs are computed so the parser accepts the blocks.
    name = b"x.txt"
    payload = b"hi"
    # FILE header fields after the 7-byte block header (no LONG_BLOCK add_size yet):
    # pack_size, unp_size, host_os, crc, dostime, unp_ver, method, name_size, attr
    file_body = struct.pack(
        "<LLBLLBBHL",
        len(payload),
        len(payload),
        3,  # Unix
        zlib.crc32(payload) & 0xFFFFFFFF,
        0,
        20,  # extract version → RAR2-era
        0x30,  # M0
        len(name),
        0o100644,
    )
    file_body += name
    # Omit LONG_BLOCK: pack_size lives only in the fixed FILE fields.
    flags = 0
    header_without_crc = struct.pack(
        "<BHH",
        0x74,
        flags,
        7 + len(file_body),
    )
    header_without_crc += file_body
    file_crc = zlib.crc32(header_without_crc) & 0xFFFF
    file_hdr = struct.pack("<H", file_crc) + header_without_crc

    main_body = b"\x00" * 6  # reserved
    main_without_crc = struct.pack("<BHH", 0x73, 0, 7 + len(main_body)) + main_body
    # CRC covers from type through crc_pos (after reserved); match parser.
    main_crc = zlib.crc32(main_without_crc) & 0xFFFF
    main_hdr = struct.pack("<H", main_crc) + main_without_crc

    end_without_crc = struct.pack("<BHH", 0x7B, 0, 7)
    end_crc = zlib.crc32(end_without_crc) & 0xFFFF
    end_hdr = struct.pack("<H", end_crc) + end_without_crc

    blob = RAR_ID + main_hdr + file_hdr + payload + end_hdr
    with pytest.raises(UnsupportedFeatureError, match="extract version 20"):
        parse_rar_archive(io.BytesIO(blob))


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
