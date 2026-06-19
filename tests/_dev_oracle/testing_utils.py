from __future__ import annotations

import os
import subprocess
import zlib
from datetime import timezone
from typing import TYPE_CHECKING, Optional

import pytest
from archivey.config import ArchiveyConfig
from archivey.internal.dependency_checker import get_dependency_versions
from archivey.internal.utils import set_file_mtime, set_file_permissions
from archivey.types import ArchiveFormat, ContainerFormat, MemberType, StreamFormat

if TYPE_CHECKING:
    from tests.archivey.sample_archives import (
        FileInfo,
    )


def write_files_to_dir(dir: str | os.PathLike, files: list[FileInfo]):
    """Write the provided FileInfo objects to ``dir``."""
    for file in sorted(
        files,
        key=lambda x: [
            MemberType.FILE,
            MemberType.HARDLINK,
            MemberType.SYMLINK,
            MemberType.DIR,
        ].index(x.type),
    ):
        full_path = os.path.join(dir, file.name)
        mtime_utc = file.mtime.replace(tzinfo=timezone.utc)

        if file.type == MemberType.DIR:
            os.makedirs(full_path, exist_ok=True)
        elif file.type == MemberType.HARDLINK:
            assert file.link_target is not None, "Link target is required"
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            os.link(
                os.path.join(dir, file.link_target),
                full_path,
            )
        elif file.type == MemberType.SYMLINK:
            assert file.link_target is not None, "Link target is required"
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            os.symlink(
                file.link_target,
                full_path,
                target_is_directory=file.link_target_type == MemberType.DIR,
            )
        else:
            assert file.contents is not None, "File contents are required"
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "wb") as f:
                f.write(file.contents)

        if file.type != MemberType.HARDLINK:
            set_file_mtime(full_path, mtime_utc, file.type)

            default_permissions_by_type = {
                MemberType.DIR: 0o755,
                MemberType.SYMLINK: 0o777,
                MemberType.FILE: 0o644,
            }
            perm = file.permissions or default_permissions_by_type[file.type]
            set_file_permissions(full_path, perm, file.type)

    # List the files to help debug failures
    try:
        subprocess.run(["ls", "-alF", "-R", "--time-style=full-iso", dir], check=True)
    except (subprocess.CalledProcessError, OSError):
        try:
            subprocess.run(["ls", "-alF", "-R", dir])
        except OSError:
            pass  # ls not available on this platform


def skip_if_package_missing(format: ArchiveFormat, config: Optional[ArchiveyConfig]):
    if format.stream and format.stream != StreamFormat.UNCOMPRESSED:
        format = ArchiveFormat(ContainerFormat.RAW_STREAM, format.stream)

    if config is None:
        config = ArchiveyConfig()

    if format.container == ContainerFormat.SEVENZIP:
        pytest.importorskip("py7zr")
    elif format.container == ContainerFormat.ISO:
        pytest.importorskip("pycdlib")
    elif format.container == ContainerFormat.RAR:
        pytest.importorskip("rarfile")
        if get_dependency_versions().unrar_version is None:
            pytest.skip("unrar not installed, skipping RAR truncation test")
    elif format.stream == StreamFormat.LZ4:
        pytest.importorskip("lz4")
    elif format.stream == StreamFormat.GZIP and config.use_rapidgzip:
        pytest.importorskip("rapidgzip")
    elif format.stream == StreamFormat.BZIP2 and config.use_indexed_bzip2:
        pytest.importorskip("indexed_bzip2")
    elif format.stream == StreamFormat.ZSTD and config.use_zstandard:
        pytest.importorskip("zstandard")
    elif format.stream == StreamFormat.ZSTD:
        import sys

        if sys.version_info < (3, 14):
            pytest.importorskip("pyzstd")
        # On Python 3.14+, compression.zstd is always available as stdlib fallback
    elif format.stream == StreamFormat.XZ and config.use_python_xz:
        pytest.importorskip("xz")
    elif format.stream == StreamFormat.BROTLI:
        pytest.importorskip("brotli")
    elif format.stream == StreamFormat.UNIX_COMPRESS:
        pytest.importorskip("uncompresspy")


def normalize_newlines(s: str | None) -> str | None:
    return s.replace("\r\n", "\n") if s else None


def get_crc32(data: bytes) -> int:
    """
    Compute CRC32 checksum for a file within an archive.
    Returns a hex string.
    """
    crc32_value: int = 0

    # Read the file in chunks
    crc32_value = zlib.crc32(data, crc32_value)
    return crc32_value & 0xFFFFFFFF


def remove_duplicate_files(files: list[FileInfo]) -> list[FileInfo]:
    """Remove duplicate files, leaving only the last one for each file name."""
    return list({file.name: file for file in files}.values())
