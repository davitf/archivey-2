"""Native 7z reader fixture coverage."""

from __future__ import annotations

import io
import os
import struct
import subprocess
import zlib
from pathlib import Path

import pytest

from archivey import ExtractionStatus, open_archive
from archivey.exceptions import (
    ArchiveyUsageError,
    EncryptionError,
    PackageNotInstalledError,
    UnsupportedFeatureError,
)
from archivey.internal.backends.sevenzip_parser import SevenZipCoder, SevenZipFolder
from archivey.internal.backends.sevenzip_reader import SevenZipReader
from archivey.internal.config import DEFAULT_STREAM_CONFIG
from archivey.internal.streams import codecs, crypto
from archivey.types import MemberType
from tests.conftest import requires, requires_binary, requires_zstd

_FILES = {
    "alpha.txt": b"alpha\n" * 100,
    "nested/beta.bin": bytes(range(64)) * 16,
}


def _py7zr():
    return pytest.importorskip("py7zr")


def _py7zr_version() -> tuple[int, ...]:
    raw = getattr(_py7zr(), "__version__", "0")
    return tuple(int(part) for part in raw.split(".") if part.isdigit())


def _filters(*names: str) -> list[dict[str, int]]:
    py7zr = _py7zr()
    return [{"id": getattr(py7zr, f"FILTER_{name}")} for name in names]


def _write_py7zr_archive(
    path: Path,
    files: dict[str, bytes],
    *,
    filters: list[dict[str, int]] | None = None,
    password: str | None = None,
    header_encryption: bool = False,
) -> None:
    py7zr = _py7zr()
    source = path.parent / f"{path.stem}-src"
    for name, data in files.items():
        target = source / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    with py7zr.SevenZipFile(
        path,
        "w",
        filters=filters,
        password=password,
        header_encryption=header_encryption,
    ) as archive:
        for name in sorted(files):
            archive.write(source / name, arcname=name)


def _assert_roundtrip(
    path: Path, files: dict[str, bytes], *, password: str | list[str] | None = None
) -> None:
    with open_archive(path, password=password) as archive:
        members = {
            member.name: member for member in archive.members() if member.is_file
        }
        assert set(members) == set(files)
        for name, expected in files.items():
            assert archive.read(members[name]) == expected


@pytest.mark.parametrize(
    ("label", "filter_names"),
    [
        pytest.param("stored", ("COPY",), id="stored"),
        pytest.param("lzma2", ("LZMA2",), id="lzma2"),
        pytest.param("lzma2-bcj", ("X86", "LZMA2"), id="lzma2-bcj"),
        pytest.param("lzma2-delta", ("DELTA", "LZMA2"), id="lzma2-delta"),
        pytest.param("deflate", ("DEFLATE",), id="deflate"),
        pytest.param("bzip2", ("BZIP2",), id="bzip2"),
        pytest.param("zstd", ("ZSTD",), marks=requires_zstd(), id="zstd"),
        pytest.param("brotli", ("BROTLI",), marks=requires("brotli"), id="brotli"),
        pytest.param("ppmd", ("PPMD",), marks=requires("pyppmd"), id="ppmd"),
    ],
)
def test_py7zr_codec_fixtures_roundtrip(
    tmp_path: Path, label: str, filter_names: tuple[str, ...]
) -> None:
    if label == "ppmd" and _py7zr_version() < (1, 1):
        pytest.skip("py7zr < 1.1 cannot build reliable PPMd 7z fixtures")
    archive = tmp_path / f"{label}.7z"
    _write_py7zr_archive(archive, _FILES, filters=_filters(*filter_names))

    _assert_roundtrip(archive, _FILES)


def test_solid_archive_stream_and_random_access(tmp_path: Path) -> None:
    archive = tmp_path / "solid.7z"
    _write_py7zr_archive(archive, _FILES, filters=_filters("LZMA2"))

    with open_archive(archive) as reader:
        assert reader.info.is_solid is True
        streamed = {
            member.name: stream.read()
            for member, stream in reader.stream_members()
            if member.is_file and stream is not None
        }
        assert streamed == _FILES
        assert reader.read("nested/beta.bin") == _FILES["nested/beta.bin"]


def test_aes_encrypted_archive_roundtrip(tmp_path: Path) -> None:
    archive = tmp_path / "aes.7z"
    _write_py7zr_archive(archive, _FILES, password="secret")

    _assert_roundtrip(archive, _FILES, password="secret")
    with open_archive(archive) as reader:
        encrypted = next(member for member in reader.members() if member.is_file)
        with pytest.raises(EncryptionError):
            reader.read(encrypted)


def test_header_encrypted_archive_requires_password(tmp_path: Path) -> None:
    archive = tmp_path / "header-encrypted.7z"
    _write_py7zr_archive(archive, _FILES, password="secret", header_encryption=True)

    with pytest.raises(EncryptionError, match="header"):
        open_archive(archive).close()
    _assert_roundtrip(archive, _FILES, password="secret")


@requires("bcj")
def test_lzma1_bcj_fixture_roundtrip(tmp_path: Path) -> None:
    """py7zr LZMA1+BCJ archives decode via staged pybcj (not combined liblzma)."""
    archive = tmp_path / "lzma1-bcj.7z"
    _write_py7zr_archive(archive, _FILES, filters=_filters("X86", "LZMA"))
    _assert_roundtrip(archive, _FILES)


@requires("bcj")
@requires_binary("7z")
def test_7z_cli_lzma1_bcj_avoids_liblzma_truncation(tmp_path: Path) -> None:
    """7-Zip CLI LZMA1+BCJ can silently truncate under combined liblzma filters.

    A ~12800-byte payload with 0xE8 call patterns reproduces the look-ahead flush
    failure (output 12796 instead of 12800). Staged pybcj must return full bytes.
    """
    payload = bytearray(os.urandom(12800))
    for offset in range(0, 12800 - 5, 40):
        payload[offset] = 0xE8
    payload_bytes = bytes(payload)
    src = tmp_path / "payload.bin"
    src.write_bytes(payload_bytes)
    archive = tmp_path / "lzma1-bcj-cli.7z"
    result = subprocess.run(
        ["7z", "a", "-t7z", "-m0=BCJ", "-m1=LZMA", str(archive), src.name, "-y"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.skip(f"7z CLI cannot write LZMA1+BCJ fixtures: {result.stderr}")

    _assert_roundtrip(archive, {src.name: payload_bytes})


@requires_binary("7z")
@requires("inflate64")
def test_7z_cli_deflate64_fixture_roundtrip(tmp_path: Path) -> None:
    payload = tmp_path / "payload.bin"
    payload.write_bytes(bytes(range(251)) * 200)
    archive = tmp_path / "deflate64.7z"
    result = subprocess.run(
        ["7z", "a", "-t7z", "-m0=Deflate64", str(archive), payload.name, "-y"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.skip(f"7z CLI cannot write Deflate64 7z fixtures: {result.stderr}")

    _assert_roundtrip(archive, {payload.name: payload.read_bytes()})


@requires_binary("7z")
@requires("cryptography")
def test_7z_cli_multi_password_archive_roundtrip(tmp_path: Path) -> None:
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_bytes(b"first secret")
    second.write_bytes(b"second secret")
    archive = tmp_path / "multi-password.7z"
    commands = (
        ["7z", "a", "-t7z", str(archive), first.name, "-pfirst", "-y"],
        ["7z", "a", "-t7z", str(archive), second.name, "-psecond", "-y"],
    )
    for command in commands:
        result = subprocess.run(
            command, cwd=tmp_path, check=False, capture_output=True, text=True
        )
        if result.returncode != 0:
            pytest.skip(
                f"7z CLI cannot build multi-password 7z fixture: {result.stderr}"
            )

    _assert_roundtrip(
        archive,
        {first.name: first.read_bytes(), second.name: second.read_bytes()},
        password=["first", "second"],
    )


@requires_binary("7z")
@requires("cryptography")
def test_7z_multi_password_rejects_wrong_candidate_via_crc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Wrong keys that decompress to the right length must still lose on CRC."""
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_bytes(b"first secret")
    second.write_bytes(b"second secret")
    archive = tmp_path / "multi-password-crc.7z"
    for command in (
        ["7z", "a", "-t7z", str(archive), first.name, "-pfirst", "-y"],
        ["7z", "a", "-t7z", str(archive), second.name, "-psecond", "-y"],
    ):
        result = subprocess.run(
            command, cwd=tmp_path, check=False, capture_output=True, text=True
        )
        if result.returncode != 0:
            pytest.skip(
                f"7z CLI cannot build multi-password 7z fixture: {result.stderr}"
            )

    original_pipeline = SevenZipReader._open_folder_pipeline
    first_kdf = "first".encode("utf-16le")
    garbage = b"\x05\x7f\xc6\x01\xebI\x03j\x88\x93\x8e\xe5\xb5"

    def pipeline_with_wrong_first(self, source, folder, *, password, seekable=False):
        # After the first folder unlocks, known-good "first" is tried on the second
        # folder. Simulate a decompressor that yields plausible garbage of the
        # expected length instead of raising, so only the CRC confirm rejects it.
        if password == first_kdf:
            for index, candidate in enumerate(self._archive.folders):
                if candidate is folder and self._folder_unpack_size(index) == len(
                    garbage
                ):
                    return io.BytesIO(garbage)
        return original_pipeline(
            self, source, folder, password=password, seekable=seekable
        )

    monkeypatch.setattr(
        SevenZipReader, "_open_folder_pipeline", pipeline_with_wrong_first
    )

    with open_archive(archive, password=["first", "second"]) as reader:
        members = {member.name: member for member in reader.members() if member.is_file}
        assert reader.read(members["first.txt"]) == b"first secret"
        assert reader.read(members["second.txt"]) == b"second secret"


@requires_binary("7z")
def test_7z_cli_multi_volume_archive_roundtrip(tmp_path: Path) -> None:
    payload = tmp_path / "large.bin"
    payload.write_bytes(bytes(range(256)) * 1200)
    result = subprocess.run(
        ["7z", "a", "-t7z", "-v100k", str(tmp_path / "vol.7z"), payload.name, "-y"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.skip(f"7z CLI cannot build multi-volume 7z fixture: {result.stderr}")
    first_volume = tmp_path / "vol.7z.001"
    if not first_volume.exists() or not (tmp_path / "vol.7z.002").exists():
        pytest.skip("7z CLI did not split the fixture into multiple volumes")

    _assert_roundtrip(first_volume, {payload.name: payload.read_bytes()})


def _reader_for_unit_tests() -> SevenZipReader:
    reader = object.__new__(SevenZipReader)
    reader._stream_config = DEFAULT_STREAM_CONFIG  # noqa: SLF001 - focused unit test
    reader._diagnostics_collector = None  # noqa: SLF001 - focused unit test
    reader._key_cache = crypto.SevenZipKeyCache()  # noqa: SLF001 - focused unit test
    return reader


def _folder(method: bytes, properties: bytes | None = None) -> SevenZipFolder:
    return SevenZipFolder(
        coders=[
            SevenZipCoder(
                method=method,
                num_in_streams=1,
                num_out_streams=1,
                properties=properties,
            )
        ],
        bind_pairs=[],
        packed_indices=[0],
        unpack_sizes=[0],
        crc=None,
        digest_defined=False,
    )


def test_bcj2_folder_is_rejected() -> None:
    reader = _reader_for_unit_tests()

    with pytest.raises(UnsupportedFeatureError, match="BCJ2"):
        reader._open_folder_pipeline(  # noqa: SLF001 - focused reader unit test
            io.BytesIO(b""), _folder(b"\x03\x03\x01\x1b"), password=None
        )


def test_unknown_folder_method_is_rejected() -> None:
    reader = _reader_for_unit_tests()

    with pytest.raises(UnsupportedFeatureError, match="0x99"):
        reader._open_folder_pipeline(  # noqa: SLF001 - focused reader unit test
            io.BytesIO(b""), _folder(b"\x99"), password=None
        )


def test_ppmd_without_pyppmd_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    reader = _reader_for_unit_tests()
    monkeypatch.setattr(codecs, "_pyppmd", None)
    properties = struct.pack("<BL", 6, 1 << 20)

    with pytest.raises(PackageNotInstalledError, match="pyppmd"):
        reader._open_folder_pipeline(  # noqa: SLF001 - focused reader unit test
            io.BytesIO(b""), _folder(b"\x03\x04\x01", properties), password=None
        )


def test_aes_without_crypto_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    reader = _reader_for_unit_tests()
    monkeypatch.setattr(crypto, "_crypto_available", lambda: False)
    properties = b"\xc0\x00\x00\x00"  # one-byte salt, one-byte IV, both zero

    with pytest.raises(PackageNotInstalledError, match="cryptography"):
        reader._open_folder_pipeline(  # noqa: SLF001 - focused reader unit test
            io.BytesIO(b""), _folder(b"\x06\xf1\x07\x01", properties), password=b"pw"
        )


def _u64(value: int) -> bytes:
    assert 0 <= value < 0x80
    return bytes([value])


def _bools(values: list[bool]) -> bytes:
    out = bytearray()
    current = 0
    mask = 0x80
    for value in values:
        if value:
            current |= mask
        mask >>= 1
        if mask == 0:
            out.append(current)
            current = 0
            mask = 0x80
    if mask != 0x80:
        out.append(current)
    return bytes(out)


def _property(prop: int, payload: bytes) -> bytes:
    return bytes([prop]) + _u64(len(payload)) + payload


def _names_payload(names: list[str]) -> bytes:
    encoded = bytearray(b"\x00")
    for name in names:
        encoded.extend(name.encode("utf-16le"))
        encoded.extend(b"\x00\x00")
    return bytes(encoded)


def _anti_item_archive(payload: bytes = b"obsolete") -> bytes:
    crc = zlib.crc32(payload) & 0xFFFFFFFF
    pack_info = b"\x06" + _u64(0) + _u64(1) + b"\x09" + _u64(len(payload)) + b"\x00"
    folder = _u64(1) + b"\x00"  # one COPY coder
    unpack_info = (
        b"\x07\x0b"
        + _u64(1)
        + b"\x00"
        + folder
        + b"\x0c"
        + _u64(len(payload))
        + b"\x0a"
        + b"\x01"
        + crc.to_bytes(4, "little")
        + b"\x00"
    )
    streams_info = b"\x04" + pack_info + unpack_info + b"\x00"
    files_info = (
        b"\x05"
        + _u64(2)
        + _property(0x0E, _bools([False, True]))
        + _property(0x10, _bools([True]))
        + _property(0x11, _names_payload(["gone.txt", "gone.txt"]))
        + b"\x00"
    )
    header = b"\x01" + streams_info + files_info + b"\x00"
    start_header = (
        len(payload).to_bytes(8, "little")
        + len(header).to_bytes(8, "little")
        + (zlib.crc32(header) & 0xFFFFFFFF).to_bytes(4, "little")
    )
    signature = (
        b"7z\xbc\xaf'\x1c\x00\x04"
        + (zlib.crc32(start_header) & 0xFFFFFFFF).to_bytes(4, "little")
        + start_header
    )
    return signature + payload + header


def test_synthetic_anti_item_lists_and_extracts_safely(tmp_path: Path) -> None:
    archive = tmp_path / "anti.7z"
    archive.write_bytes(_anti_item_archive())

    with open_archive(archive) as reader:
        content, anti = reader.members()
        assert content.name == "gone.txt"
        assert content.type is MemberType.FILE
        assert content.is_anti is False
        assert content.is_current is False
        assert anti.name == "gone.txt"
        assert anti.type is MemberType.ANTI
        assert anti.is_anti is True
        assert anti.is_file is False
        assert anti.is_current is True
        assert reader.read(content) == b"obsolete"
        with pytest.raises(ArchiveyUsageError, match="anti"):
            reader.read(anti)
        for member, stream in reader.stream_members():
            if member.is_anti:
                assert stream is None
            elif member.is_file:
                assert stream is not None
                stream.read()
                stream.close()

    fresh = tmp_path / "fresh"
    with open_archive(archive) as reader:
        results = reader.extract_all(fresh).results
    assert [result.status for result in results] == [
        ExtractionStatus.SKIPPED,
        ExtractionStatus.EXTRACTED,
    ]
    assert not (fresh / "gone.txt").exists()

    existing = tmp_path / "existing"
    existing.mkdir()
    preexisting = existing / "gone.txt"
    preexisting.write_bytes(b"keep me")
    with open_archive(archive) as reader:
        reader.extract_all(existing)
    assert preexisting.read_bytes() == b"keep me"


@requires_binary("7z")
def test_anti_item_fresh_extract_matches_7z_cli(tmp_path: Path) -> None:
    """Build a real anti-item archive with the 7z CLI and compare fresh-dest trees.

    Recipe: archive keep.txt + gone.txt, delete gone.txt on disk, then ``7z u`` with
    anti-item update options into a new archive. Fresh ``7z x`` and archivey extract
    must both leave keep.txt and omit gone.txt.
    """
    work = tmp_path / "work"
    work.mkdir()
    (work / "keep.txt").write_text("keep\n", encoding="utf-8")
    (work / "gone.txt").write_text("gone\n", encoding="utf-8")
    base = tmp_path / "base.7z"
    archive = tmp_path / "with_anti.7z"
    create = subprocess.run(
        ["7z", "a", "-t7z", str(base), "keep.txt", "gone.txt", "-y"],
        cwd=work,
        check=False,
        capture_output=True,
        text=True,
    )
    if create.returncode != 0:
        pytest.skip(f"7z CLI cannot build base archive: {create.stderr}")
    (work / "gone.txt").unlink()
    update = subprocess.run(
        [
            "7z",
            "u",
            str(base),
            "-u-",
            f"-up0q3x2y2z1!{archive}",
            "keep.txt",
            "gone.txt",
            "-y",
        ],
        cwd=work,
        check=False,
        capture_output=True,
        text=True,
    )
    if update.returncode != 0 or not archive.is_file():
        pytest.skip(f"7z CLI cannot build anti-item update archive: {update.stderr}")

    with open_archive(archive) as reader:
        members = reader.members()
        by_name = {m.name: m for m in members}
        assert by_name["gone.txt"].type is MemberType.ANTI
        assert by_name["gone.txt"].is_anti is True
        assert by_name["gone.txt"].is_current is True
        assert by_name["keep.txt"].is_anti is False
        assert by_name["keep.txt"].is_current is True
        with pytest.raises(ArchiveyUsageError):
            reader.read(by_name["gone.txt"])

    archivey_dest = tmp_path / "archivey"
    cli_dest = tmp_path / "cli"
    cli_dest.mkdir()
    with open_archive(archive) as reader:
        reader.extract_all(archivey_dest)
    subprocess.run(
        ["7z", "x", str(archive), f"-o{cli_dest}", "-y"],
        check=True,
        capture_output=True,
    )

    assert sorted(
        p.relative_to(archivey_dest) for p in archivey_dest.rglob("*") if p.is_file()
    ) == sorted(p.relative_to(cli_dest) for p in cli_dest.rglob("*") if p.is_file())
    assert (archivey_dest / "keep.txt").read_bytes() == (
        cli_dest / "keep.txt"
    ).read_bytes()
    assert not (archivey_dest / "gone.txt").exists()
    assert not (cli_dest / "gone.txt").exists()


def test_filetime_conversion_and_invalid_timestamp_issue() -> None:
    from archivey.internal.backends.sevenzip_reader import _filetime_to_datetime

    # A normal FILETIME converts with no issue; 0/None mean "unset" (no value, no issue).
    dt, issue = _filetime_to_datetime(
        132_000_000_000_000_000, "a.txt", field="modified"
    )
    assert dt is not None and issue is None
    assert _filetime_to_datetime(0, "a.txt", field="modified") == (None, None)
    assert _filetime_to_datetime(None, "a.txt", field="modified") == (None, None)

    # An out-of-range value yields no datetime and a reported issue (surfaced as a
    # MEMBER_TIMESTAMP_INVALID diagnostic rather than being swallowed silently).
    dt, issue = _filetime_to_datetime(0xFFFFFFFFFFFFFFFF, "a.txt", field="created")
    assert dt is None
    assert issue is not None and issue.field == "created"


def test_files_info_count_is_bounded_against_header_size() -> None:
    # A crafted 7z header can declare an absurd file count in a few bytes; the parser must
    # reject it against the header size instead of pre-allocating one object per claimed
    # file and OOM-ing the process (threat-model O1 / review L1). Encode num_files = 2**40
    # in the 7z uint64 form (0xFF marker + 8 LE bytes) and feed it straight to the reader.
    from archivey.exceptions import CorruptionError
    from archivey.internal.backends.sevenzip_parser import _read_files_info

    huge = (1 << 40).to_bytes(8, "little")
    buffer = io.BytesIO(b"\xff" + huge)  # a 9-byte "header" claiming 2**40 files
    with pytest.raises(CorruptionError, match="exceeds the .* header"):
        _read_files_info(buffer)


def test_next_header_offset_overflow_is_typed_corruption() -> None:
    """Huge nextHeaderOffset must not raise OverflowError on seek (Atheris finding)."""
    import struct
    import zlib

    from archivey.exceptions import CorruptionError
    from archivey.internal.backends.sevenzip_parser import (
        MAGIC_7Z,
        parse_sevenzip_archive,
    )

    # Valid signature CRC over a start_header that claims an absurd next-header offset.
    next_offset = (1 << 64) - 1
    next_size = 16
    next_crc = 0
    start_header = struct.pack("<QQI", next_offset, next_size, next_crc)
    start_crc = zlib.crc32(start_header) & 0xFFFFFFFF
    blob = MAGIC_7Z + bytes([0, 4]) + struct.pack("<I", start_crc) + start_header

    def _boom(*_a: object, **_k: object) -> bytes:
        raise AssertionError("decode_folder must not be reached")

    with pytest.raises(CorruptionError, match="next-header offset"):
        parse_sevenzip_archive(io.BytesIO(blob), decode_folder=_boom)  # type: ignore[arg-type]


def test_next_header_size_cap_is_typed_corruption() -> None:
    import struct
    import zlib

    from archivey.exceptions import CorruptionError
    from archivey.internal.backends.sevenzip_parser import (
        _MAX_NEXT_HEADER_SIZE,
        MAGIC_7Z,
        parse_sevenzip_archive,
    )

    next_offset = 0
    next_size = _MAX_NEXT_HEADER_SIZE + 1
    next_crc = 0
    start_header = struct.pack("<QQI", next_offset, next_size, next_crc)
    start_crc = zlib.crc32(start_header) & 0xFFFFFFFF
    blob = MAGIC_7Z + bytes([0, 4]) + struct.pack("<I", start_crc) + start_header

    def _boom(*_a: object, **_k: object) -> bytes:
        raise AssertionError("decode_folder must not be reached")

    with pytest.raises(CorruptionError, match="next-header size"):
        parse_sevenzip_archive(io.BytesIO(blob), decode_folder=_boom)  # type: ignore[arg-type]
