"""ZIP member decode through the shared codec layer (extended methods).

Covers Deflate64 / Zstd / PPMd ZIP members that stdlib ``zipfile`` cannot decode,
plus missing-backend → ``PackageNotInstalledError`` and corrupt-body → ``CorruptionError``.
"""

from __future__ import annotations

import io
import struct
import subprocess
import zipfile
import zlib
from pathlib import Path

import pytest

from archivey import open_archive
from archivey.exceptions import (
    CorruptionError,
    PackageNotInstalledError,
    TruncatedError,
    UnsupportedFeatureError,
)
from archivey.internal.streams import codecs as codecs_module
from archivey.types import CompressionAlgorithm, MemberStreams
from tests.conftest import requires, requires_binary, requires_zstd, zstd_backend

_PAYLOAD = (b"zip-native-codec-payload\n" * 80) + bytes(range(256))


def _build_minimal_zip(
    name: bytes,
    compressed: bytes,
    uncompressed: bytes,
    method: int,
) -> bytes:
    """Hand-build a single-entry ZIP with an arbitrary compression method id."""
    crc = zlib.crc32(uncompressed) & 0xFFFFFFFF
    name_len = len(name)
    local = struct.pack(
        "<IHHHHHIIIHH",
        0x04034B50,
        20,
        0,
        method,
        0,
        0,
        crc,
        len(compressed),
        len(uncompressed),
        name_len,
        0,
    )
    local += name + compressed
    cd = struct.pack(
        "<IHHHHHHIIIHHHHHII",
        0x02014B50,
        20,
        20,
        0,
        method,
        0,
        0,
        crc,
        len(compressed),
        len(uncompressed),
        name_len,
        0,
        0,
        0,
        0,
        0,
        0,
    )
    cd += name
    eocd = struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, 1, 1, len(cd), len(local), 0)
    return local + cd + eocd


def _7z_zip(tmp_path: Path, method: str, payload: bytes) -> Path:
    src = tmp_path / "payload.bin"
    src.write_bytes(payload)
    archive = tmp_path / f"{method.lower()}.zip"
    result = subprocess.run(
        ["7z", "a", "-tzip", f"-mm={method}", str(archive), src.name, "-y"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not archive.is_file():
        pytest.skip(f"7z CLI cannot write ZIP {method} fixture: {result.stderr}")
    with zipfile.ZipFile(archive) as zf:
        info = zf.infolist()[0]
        if method == "Deflate64" and info.compress_type != 9:
            pytest.skip(
                f"7z wrote ZIP method {info.compress_type} instead of Deflate64 (9)"
            )
        if method == "PPMd" and info.compress_type != 98:
            pytest.skip(
                f"7z wrote ZIP method {info.compress_type} instead of PPMd (98)"
            )
    return archive


@requires_binary("7z")
@requires("inflate64")
def test_zip_deflate64_roundtrip(tmp_path: Path) -> None:
    archive = _7z_zip(tmp_path, "Deflate64", _PAYLOAD)
    with open_archive(archive) as ar:
        members = ar.members()
        assert len(members) == 1
        assert members[0].compression[0].algo is CompressionAlgorithm.DEFLATE64
        assert ar.read(members[0]) == _PAYLOAD


@requires_binary("7z")
@requires("pyppmd")
def test_zip_ppmd_roundtrip(tmp_path: Path) -> None:
    archive = _7z_zip(tmp_path, "PPMd", _PAYLOAD)
    with open_archive(archive) as ar:
        members = ar.members()
        assert len(members) == 1
        assert members[0].compression[0].algo is CompressionAlgorithm.PPMD
        assert ar.read(members[0]) == _PAYLOAD


@requires_zstd()
def test_zip_zstd_handbuilt_roundtrip() -> None:
    zstd = zstd_backend()
    compressed = zstd.compress(_PAYLOAD)
    data = _build_minimal_zip(b"zstd.txt", compressed, _PAYLOAD, 93)
    with open_archive(io.BytesIO(data)) as ar:
        (member,) = ar.members()
        assert member.compression[0].algo is CompressionAlgorithm.ZSTD
        assert ar.read(member) == _PAYLOAD


@requires_binary("7z")
def test_zip_deflate64_without_inflate64_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = _7z_zip(tmp_path, "Deflate64", _PAYLOAD)
    monkeypatch.setattr(codecs_module, "_inflate64", None)
    with open_archive(archive) as ar:
        (member,) = ar.members()
        with pytest.raises(PackageNotInstalledError, match="inflate64"):
            ar.read(member)


@requires_binary("7z")
def test_zip_ppmd_without_pyppmd_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = _7z_zip(tmp_path, "PPMd", _PAYLOAD)
    monkeypatch.setattr(codecs_module, "_pyppmd", None)
    with open_archive(archive) as ar:
        (member,) = ar.members()
        with pytest.raises(PackageNotInstalledError, match="pyppmd"):
            ar.read(member)


def test_zip_zstd_without_backend_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    # Hand-built fixture so the test does not need a zstd writer; only the reader backend
    # presence is gated.
    compressed = b"not-real-zstd"  # never decoded — open fails on missing backend first
    data = _build_minimal_zip(b"z.txt", compressed, b"x" * 10, 93)
    monkeypatch.setattr(codecs_module, "_zstd", None)
    with open_archive(io.BytesIO(data)) as ar:
        (member,) = ar.members()
        with pytest.raises(PackageNotInstalledError):
            ar.read(member)


def test_zip_corrupt_deflate_body_raises_corruption() -> None:
    # Flip bits in a real DEFLATE bitstream so inflate fails loudly and surfaces as
    # CorruptionError / TruncatedError via the shared codec translator (not a raw zlib.error).
    good = zlib.compress(_PAYLOAD)[2:-4]  # raw deflate (strip zlib wrapper)
    corrupt = bytearray(good)
    mid = len(corrupt) // 2
    corrupt[mid] ^= 0xFF
    corrupt[mid + 1] ^= 0xFF
    data = _build_minimal_zip(b"bad.txt", bytes(corrupt), _PAYLOAD, 8)
    with open_archive(io.BytesIO(data)) as ar:
        (member,) = ar.members()
        with pytest.raises((CorruptionError, TruncatedError)):
            ar.read(member)


def test_zip_unknown_method_raises_unsupported() -> None:
    data = _build_minimal_zip(b"x.bin", b"raw", b"raw", method=97)
    with open_archive(io.BytesIO(data)) as ar:
        (member,) = ar.members()
        with pytest.raises(UnsupportedFeatureError, match="compression method 97"):
            ar.read(member)


def test_zip_method_99_without_aes_extra_raises() -> None:
    # Method 99 with no 0x9901 extra is not a valid AE member.
    data = _build_minimal_zip(b"x.bin", b"raw", b"raw", method=99)
    with open_archive(io.BytesIO(data)) as ar:
        (member,) = ar.members()
        with pytest.raises(UnsupportedFeatureError, match="0x9901"):
            ar.read(member)


def test_zip_stdlib_methods_still_roundtrip(tmp_path: Path) -> None:
    archive = tmp_path / "stdlib.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("stored.txt", _PAYLOAD, compress_type=zipfile.ZIP_STORED)
        zf.writestr("deflated.txt", _PAYLOAD, compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("bzip2.txt", _PAYLOAD, compress_type=zipfile.ZIP_BZIP2)
        zf.writestr("lzma.txt", _PAYLOAD, compress_type=zipfile.ZIP_LZMA)
    with open_archive(archive) as ar:
        by_name = {m.name: m for m in ar.members()}
        for name in ("stored.txt", "deflated.txt", "bzip2.txt", "lzma.txt"):
            assert ar.read(by_name[name]) == _PAYLOAD


def test_zip_concurrent_codec_path_interleaved(tmp_path: Path) -> None:
    archive = tmp_path / "concurrent.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("a.txt", b"aaaa" * 1000, compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("b.txt", b"bbbb" * 1000, compress_type=zipfile.ZIP_DEFLATED)
    with open_archive(archive, member_streams=MemberStreams.CONCURRENT) as ar:
        s1 = ar.open("a.txt")
        s2 = ar.open("b.txt")
        assert s1.read(4) == b"aaaa"
        assert s2.read(4) == b"bbbb"
        assert s1.read() == b"aaaa" * 999
        assert s2.read() == b"bbbb" * 999
        s1.close()
        s2.close()
