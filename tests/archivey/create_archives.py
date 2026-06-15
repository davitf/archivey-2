import argparse
import bz2
import fnmatch
import functools
import gzip
import io
import logging
import lzma
import os
import shutil
import stat
import struct
import subprocess
import tarfile
import tempfile
import zipfile
import zlib
from datetime import timezone
from typing import Any, Callable, Generator

try:
    import pyzstd
except ModuleNotFoundError:
    pyzstd = None

try:  # Optional dependency
    import lz4.frame as lz4_frame  # type: ignore
except ModuleNotFoundError:
    lz4_frame = None

try:  # Optional dependency
    import py7zr  # type: ignore
except ModuleNotFoundError:
    py7zr = None

try:  # Optional dependency
    import pycdlib  # type: ignore
except ModuleNotFoundError:
    pycdlib = None

try:  # Optional dependency
    import zstandard  # type: ignore
except ModuleNotFoundError:
    zstandard = None

try:  # Optional dependency
    import brotli  # type: ignore
except ModuleNotFoundError:
    brotli = None


try:  # Optional dependency
    import py7zr  # type: ignore
except ModuleNotFoundError:
    py7zr = None

try:  # Optional dependency
    import pycdlib  # type: ignore
except ModuleNotFoundError:
    pycdlib = None

try:  # Optional dependency
    import zstandard  # type: ignore
except ModuleNotFoundError:
    zstandard = None

from tests.archivey.sample_archives import (
    SAMPLE_ARCHIVES,
    ArchiveContents,
    FileInfo,
    GenerationMethod,
    SampleArchive,
)
from tests.archivey.testing_utils import write_files_to_dir

from archivey.exceptions import PackageNotInstalledError
from archivey.types import ArchiveFormat, ContainerFormat, MemberType, StreamFormat

_COMPRESSION_METHOD_TO_ZIPFILE_VALUE = {
    "store": zipfile.ZIP_STORED,
    "deflate": zipfile.ZIP_DEFLATED,
    "bzip2": zipfile.ZIP_BZIP2,
    "lzma": zipfile.ZIP_LZMA,
}

DEFAULT_ZIP_COMPRESSION_METHOD = "store"

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


def group_files_by_password_and_compression_method(
    files: list[FileInfo],
) -> Generator[tuple[str | None, str | None, list[FileInfo]], None, None]:
    current_password: str | None = None
    current_compression_method: str | None = None
    current_files: list[FileInfo] = []
    seen_files_in_group: set[str] = set()
    for file in files:
        if (
            file.password != current_password
            or file.compression_method != current_compression_method
            or file.name in seen_files_in_group
        ):
            if current_files:
                yield (current_password, current_compression_method, current_files)
                seen_files_in_group = set()
            current_password = file.password
            current_compression_method = file.compression_method
            current_files = []
        current_files.append(file)
        seen_files_in_group.add(file.name)

    if current_files:
        yield (current_password, current_compression_method, current_files)


def create_zip_archive_with_zipfile(
    archive_path: str,
    contents: ArchiveContents,
    compression_format: ArchiveFormat,
    compression_method: str | None = None,
):
    """
    Create a zip archive using the zipfile module.

    This does not support symlinks.
    """
    assert compression_format == ArchiveFormat.ZIP, (
        f"Only ZIP format is supported, got {compression_format}"
    )
    assert not contents.solid, "Zipfile does not support solid archives"

    for i, (password, _, group_files) in enumerate(
        group_files_by_password_and_compression_method(contents.files)
    ):
        assert password is None, "zipfile does not support writing encrypted files"

        with zipfile.ZipFile(archive_path, "w" if i == 0 else "a") as zipf:
            if i == 0:
                zipf.comment = (contents.archive_comment or "").encode("utf-8")

            for file in group_files:
                # zipfile module does not directly support symlinks in a way that preserves them as symlinks.
                # We are skipping symlink creation here for zipfile, but infozip handles it.
                # If mode is set for a symlink, it would apply to the target if zipfile wrote it as a regular file.
                # assert file.type != MemberType.LINK, (
                #     "Links are not supported in zipfile in a way that preserves them as symlinks."
                # )

                filename = file.name
                file_contents = file.contents

                if file.type == MemberType.DIR:
                    if not filename.endswith("/"):
                        filename += "/"
                    file_contents = b""  # Directories have no content

                elif file.type == MemberType.SYMLINK:
                    # For zipfile, if we have to write a link, we write its target path as content.
                    # The external_attr will mark it as a link.
                    file_contents = (file.link_target or "").encode("utf-8")

                info = zipfile.ZipInfo(filename, date_time=file.mtime.timetuple()[:6])
                info.compress_type = _COMPRESSION_METHOD_TO_ZIPFILE_VALUE[
                    file.compression_method
                    or compression_method
                    or DEFAULT_ZIP_COMPRESSION_METHOD
                ]
                info.comment = (file.comment or "").encode("utf-8")

                if file.type == MemberType.SYMLINK:
                    info.external_attr |= (stat.S_IFLNK | 0o777) << 16
                elif file.type == MemberType.DIR:
                    info.external_attr |= (stat.S_IFDIR | 0o775) << 16
                else:
                    info.external_attr |= (
                        stat.S_IFREG | (file.permissions or 0o644)
                    ) << 16

                if file_contents is None and file.type == MemberType.FILE:
                    assert False, f"File contents are required for {file.name}"

                zipf.writestr(info, file_contents if file_contents is not None else b"")


def create_zip_archive_with_infozip_command_line(
    archive_path: str,
    contents: ArchiveContents,
    compression_format: ArchiveFormat,
    compression_method: str | None = None,
):
    """
    Create a zip archive using the zip command line tool.

    This supports symlinks, unlike the zipfile implementation. The files are written to
    the zip archive in the order of the files list.
    """
    assert compression_format == ArchiveFormat.ZIP, (
        f"Only ZIP format is supported, got {compression_format}"
    )
    assert not contents.solid, "Infozip does not support solid archives"

    abs_archive_path = os.path.abspath(archive_path)
    if os.path.exists(archive_path):
        os.remove(archive_path)

    with tempfile.TemporaryDirectory() as tempdir:
        write_files_to_dir(tempdir, contents.files)

        # In order to apply the password to only the corresponding files, we need to use the --update option.
        for i, (password, compression_method, group_files) in enumerate(
            group_files_by_password_and_compression_method(contents.files)
        ):
            command = ["zip", "-q"]
            if i > 0:
                command += ["--update"]
            command += ["--symlinks"]
            if password:
                command += ["-P", password]

            command += [
                "-Z",
                compression_method
                or compression_method
                or DEFAULT_ZIP_COMPRESSION_METHOD,
            ]
            command += [abs_archive_path]

            # Pass the files to the command in the order they should be written to the archive.
            for file in group_files:
                command.append(file.name)

            # Run the command
            subprocess.run(command, check=True, cwd=tempdir)

        if contents.archive_comment:
            command = ["zip", "-z", archive_path]
            subprocess.run(
                command,
                check=True,
                input=contents.archive_comment.encode("utf-8"),
            )

        comment_file_names: list[str] = []
        comment_file_comments: list[str] = []
        for file in contents.files:
            if file.comment:
                assert "\n" not in file.comment, "File comments cannot contain newlines"
                comment_file_names.append(file.name)
                comment_file_comments.append(file.comment)

        if comment_file_names:
            command = ["zip", "-c", archive_path] + comment_file_names
            logger.info("Running command: %s", " ".join(command))
            subprocess.run(
                command,
                check=True,
                input="\n".join(comment_file_comments).encode("utf-8"),
            )


def create_tar_archive_with_command_line(
    archive_path: str,
    contents: ArchiveContents,
    compression_format: ArchiveFormat,
):
    """
    Create a tar archive using the tar command line tool.
    """
    assert contents.solid is not False, "TAR archives are always solid"

    assert contents.archive_comment is None, (
        "TAR format does not support archive comments"
    )

    abs_archive_path = os.path.abspath(archive_path)
    if os.path.exists(archive_path):
        os.remove(archive_path)

    with tempfile.TemporaryDirectory() as tempdir:
        write_files_to_dir(tempdir, contents.files)

        command = ["tar", "-c", "--no-recursion", "-f", abs_archive_path]

        # Add compression flag based on the compression_format
        if compression_format == ArchiveFormat.TAR_GZ:
            command.append("-z")  # gzip
        elif compression_format == ArchiveFormat.TAR_BZ2:
            command.append("-j")  # bzip2
        elif compression_format == ArchiveFormat.TAR_XZ:
            command.append("-J")  # xz
        elif compression_format == ArchiveFormat.TAR_ZSTD:
            command.append("--zstd")
        elif compression_format == ArchiveFormat.TAR_Z:
            command.append("-Z")
        elif compression_format != ArchiveFormat.TAR:
            # This case should ideally not be reached if enums are used correctly
            raise ValueError(
                f"Unsupported tar compression format: {compression_format}"
            )

        # Add file names to the command
        # These names must be relative to the temporary directory
        for file_info in contents.files:
            command.append(file_info.name)

        logger.info(f"Running command: {' '.join(command)}")
        subprocess.run(["ls", "-lR", tempdir])
        subprocess.run(command, check=True, cwd=tempdir)


class _BrotliWriter(io.BufferedIOBase):
    def __init__(self, path: str) -> None:
        assert brotli is not None, "Brotli is not installed"
        self._f = open(path, "wb")
        self._compressor = brotli.Compressor()

    def write(self, data: bytes) -> int:
        self._f.write(self._compressor.process(data))
        return len(data)

    def close(self) -> None:
        if not self._f.closed:
            self._f.write(self._compressor.finish())
            self._f.close()
        super().close()


class _ZlibWriter(io.BufferedIOBase):
    def __init__(self, path: str) -> None:
        self._f = open(path, "wb")
        self._compressor = zlib.compressobj()

    def write(self, data: bytes) -> int:
        self._f.write(self._compressor.compress(data))
        return len(data)

    def close(self) -> None:
        if not self._f.closed:
            self._f.write(self._compressor.flush())
            self._f.close()
        super().close()


def _brotli_open(path: str, mode: str = "wb") -> _BrotliWriter:
    assert mode == "wb"
    return _BrotliWriter(path)


def _zlib_open(path: str, mode: str = "wb") -> _ZlibWriter:
    assert mode == "wb"
    return _ZlibWriter(path)


def create_lzip_member(data: bytes, dict_size_bits: int = 23) -> bytes:
    """Compress *data* into a single lzip member using stdlib lzma.

    lzip omits the 13-byte LZMA_ALONE header from its raw stream; we compress
    with FORMAT_ALONE (which includes it), then strip the header to get the
    raw LZMA1 stream that lzip expects.
    """
    filters: list[dict] = [
        {
            "id": lzma.FILTER_LZMA1,
            "dict_size": 1 << dict_size_bits,
            "lc": 3,
            "lp": 0,
            "pb": 2,
        }
    ]
    compressed_alone = lzma.compress(data, format=lzma.FORMAT_ALONE, filters=filters)
    lzma_raw = compressed_alone[13:]  # strip the 13-byte LZMA_ALONE header
    header = b"LZIP" + bytes([1, dict_size_bits])
    member_total = len(header) + len(lzma_raw) + 20
    trailer = struct.pack(
        "<IQQ", zlib.crc32(data) & 0xFFFFFFFF, len(data), member_total
    )
    return header + lzma_raw + trailer


class _LzipWriter(io.BufferedIOBase):
    """Write a lzip file (single member) using stdlib lzma."""

    def __init__(self, path: str) -> None:
        self._path = path
        self._buf = io.BytesIO()
        self._pos = 0

    def write(self, data: bytes) -> int:
        self._buf.write(data)
        self._pos += len(data)
        return len(data)

    def close(self) -> None:
        if not self.closed:
            member = create_lzip_member(self._buf.getvalue())
            with open(self._path, "wb") as f:
                f.write(member)
        super().close()

    def tell(self) -> int:  # pragma: no cover - used by tarfile
        return self._pos

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:  # pragma: no cover
        if whence == io.SEEK_CUR and offset == 0:
            return self._pos
        raise io.UnsupportedOperation("seek")


def _lzip_open(path: str, mode: str = "wb") -> _LzipWriter:
    assert mode == "wb"
    return _LzipWriter(path)


SINGLE_FILE_LIBRARY_OPENERS: dict[StreamFormat, Callable[..., io.BytesIO] | None] = {
    StreamFormat.GZIP: gzip.GzipFile,
    StreamFormat.BZIP2: bz2.BZ2File,
    StreamFormat.XZ: lzma.LZMAFile,
    StreamFormat.ZSTD: functools.partial(
        pyzstd.open,
        level_or_option={
            pyzstd.CParameter.checksumFlag: 1,
        },
    )  # type: ignore[reportUnknownReturnType]
    if pyzstd is not None
    else None,
    StreamFormat.LZ4: lz4_frame.open if lz4_frame is not None else None,
    StreamFormat.LZIP: _lzip_open,
    StreamFormat.ZLIB: _zlib_open,
    StreamFormat.BROTLI: _brotli_open if brotli is not None else None,
}


def create_tar_archive_with_tarfile(
    archive_path: str,
    contents: ArchiveContents,
    compression_format: ArchiveFormat,
):
    """
    Create a tar archive using Python's tarfile module.
    Supports setting file modes and different compression formats.
    """
    assert contents.solid is not False, "TAR archives are always solid"
    assert contents.archive_comment is None, (
        "TAR format does not support archive comments"
    )

    logger.info(
        f"Creating TAR archive (tarfile) {archive_path} with compression format {compression_format}"
    )

    abs_archive_path = os.path.abspath(archive_path)
    if os.path.exists(abs_archive_path):
        os.remove(abs_archive_path)

    output_stream: io.BytesIO | None = None
    compress_after = False
    temp_tar_path = abs_archive_path

    if compression_format == ArchiveFormat.TAR:
        tar_mode = "w"  # plain tar
    elif compression_format == ArchiveFormat.TAR_GZ:
        tar_mode = "w:gz"
    elif compression_format == ArchiveFormat.TAR_BZ2:
        tar_mode = "w:bz2"
    elif compression_format == ArchiveFormat.TAR_XZ:
        tar_mode = "w:xz"

    elif (
        compression_format.container == ContainerFormat.TAR
        and compression_format.stream is not None
        and compression_format.stream != StreamFormat.UNCOMPRESSED
    ):
        opener = SINGLE_FILE_LIBRARY_OPENERS[compression_format.stream]

        if opener is None:
            raise PackageNotInstalledError(
                f"Required library for {compression_format.file_extension()} is not installed"
            )
        output_stream = opener(abs_archive_path, "wb")
        tar_mode = "w|"  # will compress manually below
    else:
        raise ValueError(f"Unsupported tar compression format: {compression_format}")

    with tarfile.open(name=temp_tar_path, mode=tar_mode, fileobj=output_stream) as tf:  # type: ignore[reportArgumentType]
        for sample_file in contents.files:
            tarinfo = tarfile.TarInfo(name=sample_file.name)
            tarinfo.mtime = int(
                sample_file.mtime.replace(tzinfo=timezone.utc).timestamp()
            )

            if sample_file.permissions is not None:
                tarinfo.mode = sample_file.permissions

            if sample_file.uid is not None:
                tarinfo.uid = sample_file.uid
            if sample_file.gid is not None:
                tarinfo.gid = sample_file.gid
            if sample_file.uname is not None:
                tarinfo.uname = sample_file.uname
            if sample_file.gname is not None:
                tarinfo.gname = sample_file.gname

            file_contents_bytes = sample_file.contents

            if sample_file.type == MemberType.DIR:
                tarinfo.type = tarfile.DIRTYPE
                if sample_file.permissions is None:
                    tarinfo.mode = 0o755  # Default mode for directories
                tf.addfile(tarinfo)  # No fileobj for directories
                logger.info(f"Adding dir {tarinfo}")
            elif sample_file.type == MemberType.SYMLINK:
                tarinfo.type = tarfile.SYMTYPE
                assert sample_file.link_target is not None, (
                    f"Link target required for {sample_file.name}"
                )
                tarinfo.linkname = sample_file.link_target
                if sample_file.permissions is None:
                    tarinfo.mode = 0o777  # Default mode for symlinks
                logger.info(f"Adding symlink {tarinfo}")
                tf.addfile(tarinfo)  # No fileobj for symlinks
            elif sample_file.type == MemberType.HARDLINK:
                tarinfo.type = tarfile.LNKTYPE
                assert sample_file.link_target is not None, (
                    f"Link target required for {sample_file.name}"
                )
                tarinfo.linkname = sample_file.link_target
                tarinfo.mode = 0o644  # Default mode for hardlinks
                logger.info(f"Adding hardlink {tarinfo}")
                tf.addfile(tarinfo)  # No fileobj for hardlinks
            else:  # MemberType.FILE
                assert file_contents_bytes is not None, (
                    f"Contents required for file {sample_file.name}"
                )
                tarinfo.type = tarfile.REGTYPE
                tarinfo.size = len(file_contents_bytes)
                if sample_file.permissions is None:
                    tarinfo.mode = 0o644  # Default mode for regular files
                logger.info(f"Adding file {tarinfo}")
                tf.addfile(tarinfo, io.BytesIO(file_contents_bytes))

    if output_stream is not None:
        output_stream.close()

    if compress_after:
        with open(temp_tar_path, "rb") as src:
            raw = src.read()
        with open(abs_archive_path, "wb") as dst:
            dst.write(create_lzip_member(raw))
        os.remove(temp_tar_path)


def create_single_file_compressed_archive_with_library(
    archive_path: str,
    contents: ArchiveContents,
    compression_format: ArchiveFormat,
    opener_kwargs: dict[str, Any] = {},
):
    assert compression_format.container == ContainerFormat.RAW_STREAM, (
        f"Only supported compression formats are supported, got {compression_format}"
    )
    opener = SINGLE_FILE_LIBRARY_OPENERS[compression_format.stream]
    if opener is None:
        raise PackageNotInstalledError(
            f"Required library for {compression_format.file_extension()} is not installed"
        )

    assert not contents.solid, f"Single-file archives are not solid. ({archive_path})"
    assert len(contents.files) == 1, (
        f"Single-file archives only support a single file. ({archive_path})"
    )
    file_info = contents.files[0]
    assert file_info.type == MemberType.FILE, (
        f"Only files are supported for single-file archives. ({archive_path})"
    )
    assert contents.archive_comment is None and file_info.comment is None, (
        f"Single-file archives do not support comments. ({archive_path})"
    )

    with opener(archive_path, mode="wb", **opener_kwargs) as out_stream:
        out_stream.write(file_info.contents or b"")


def create_single_file_compressed_archive_with_command_line(
    archive_path: str,
    contents: ArchiveContents,
    compression_format: ArchiveFormat,
    compression_cmd: str = "gzip",
    cmd_args: list[str] = [],
):
    assert compression_format.container == ContainerFormat.RAW_STREAM, (
        f"Only supported compression formats are supported, got {compression_format}"
    )
    assert not contents.solid, f"{compression_cmd} archives are not solid"
    assert len(contents.files) == 1, (
        f"{compression_cmd} archives only support a single file."
    )
    file_info = contents.files[0]
    assert file_info.type == MemberType.FILE, (
        f"Only files are supported for {compression_cmd}."
    )
    assert contents.archive_comment is None, (
        f"{compression_cmd} format does not support archive comments."
    )
    assert contents.header_password is None, (
        f"{compression_cmd} format does not support header passwords."
    )

    abs_archive_path = os.path.abspath(archive_path)
    if os.path.exists(abs_archive_path):
        os.remove(abs_archive_path)

    with tempfile.TemporaryDirectory() as tempdir:
        write_files_to_dir(tempdir, contents.files)

        temp_file_path = os.path.join(tempdir, file_info.name)
        subprocess.run(["ls", "-l", temp_file_path])

        # Run the compression command
        subprocess.run(
            [compression_cmd, *cmd_args, temp_file_path], check=True, cwd=tempdir
        )

        # Get the compressed file path based on the command
        compressed_file_on_temp = temp_file_path + os.path.splitext(archive_path)[1]
        os.rename(compressed_file_on_temp, abs_archive_path)

        # Explicitly set the mtime of the archive file itself
        # os.utime(
        #     abs_archive_path, (file_info.mtime.timestamp(), file_info.mtime.timestamp())
        # )


def create_rar_archive_with_command_line(
    archive_path: str,
    contents: ArchiveContents,
    compression_format: ArchiveFormat,
    rar4_format: bool = False,
):
    assert compression_format == ArchiveFormat.RAR, (
        f"Only RAR format is supported, got {compression_format}"
    )
    if shutil.which("rar") is None:
        raise PackageNotInstalledError("rar command is not installed")
    abs_archive_path = os.path.abspath(archive_path)
    if os.path.exists(abs_archive_path):
        os.remove(abs_archive_path)

    if contents.solid and len({f.password for f in contents.files}) > 1:
        raise ValueError("Solid archives do not support multiple passwords")

    if contents.solid and len({f.compression_method for f in contents.files}) > 1:
        raise ValueError("Solid archives do not support multiple compression methods")

    with tempfile.TemporaryDirectory() as tempdir:
        write_files_to_dir(tempdir, contents.files)

        for i, (password, compression_method, group_files) in enumerate(
            group_files_by_password_and_compression_method(contents.files)
        ):
            command = ["rar", "a", "-oh", "-ol"]
            if rar4_format:
                command.append("-ma4")

            if contents.solid:
                command.append("-s")

            # Handle header password
            if contents.header_password:
                command.append(f"-hp{contents.header_password}")
                if password and password != contents.header_password:
                    raise ValueError(
                        "Header password and file password cannot be different"
                    )

            # Handle file password
            elif password:
                command.append(f"-p{password}")

            # Handle archive comment
            comment_file_path = None
            if i == 0 and contents.archive_comment:
                # rar expects the comment file to be passed with -z<file>
                comment_fd, comment_file_path = tempfile.mkstemp(dir=tempdir)
                with os.fdopen(comment_fd, "wb") as f:
                    f.write(contents.archive_comment.encode("utf-8"))
                command.append(f"-z{comment_file_path}")

            command.append(abs_archive_path)

            # Add file names to the command (relative to tempdir)
            for file_info in group_files:
                # RAR typically includes directories implicitly if files within them are added.
                # However, to ensure empty directories or specific directory metadata (like mtime)
                # are preserved as defined in FileInfo, we add them explicitly.
                # RAR handles adding existing files/dirs.
                command.append(file_info.name.removesuffix("/"))

            env = os.environ.copy()
            env["TZ"] = "UTC"
            subprocess.run(command, check=True, cwd=tempdir, env=env)

            if comment_file_path:
                os.remove(comment_file_path)


def create_7z_archive_with_py7zr(
    archive_path: str, contents: ArchiveContents, compression_format: ArchiveFormat
):
    assert compression_format == ArchiveFormat.SEVENZIP, (
        f"Only 7Z format is supported, got {compression_format}"
    )
    if py7zr is None:
        raise PackageNotInstalledError("py7zr is required to create 7z archives")

    abs_archive_path = os.path.abspath(archive_path)
    if os.path.exists(abs_archive_path):
        os.remove(abs_archive_path)

    # In 7-zip, the solidness of the archive is determined by the files themselves.
    # Each archive has one or more "folders", which are groups of files that are
    # compressed together. An archive is considered solid if at least one folder has
    # more than one file.

    # When writing an archive, py7zr adds all files to the same folder. So, to create
    # a non-solid archive, we need to add each file individually and close the archive
    # after each one.

    if contents.header_password and any(
        f.password is not None and f.password != contents.header_password
        for f in contents.files
    ):
        raise ValueError("Header password and file password cannot be different")

    with tempfile.TemporaryDirectory() as tempdir:
        file_groups: list[list[FileInfo]]
        if contents.solid:
            file_groups = [contents.files]
        else:
            # Create a separate group for each file, so it doesn't get compressed in the
            # same folder as another. But group dirs and symlinks along with a file
            # when writing, as they are not added to folders and the library breaks
            # if we don't add at least one actual file.
            file_groups = [[]]
            last_group_has_file = False
            for file in contents.files:
                if file.type == MemberType.FILE and last_group_has_file:
                    file_groups.append([])
                    last_group_has_file = False
                file_groups[-1].append(file)
                if file.type == MemberType.FILE:
                    last_group_has_file = True

        for file_group in file_groups:
            for i, (password, compression_method, group_files) in enumerate(
                group_files_by_password_and_compression_method(file_group)
            ):
                # Use header password if provided, otherwise use file password
                write_files_to_dir(tempdir, group_files)

                with py7zr.SevenZipFile(
                    abs_archive_path,
                    "a",
                    password=contents.header_password or password,
                    header_encryption=contents.header_password is not None,
                ) as archive:
                    for file in group_files:
                        archive.write(os.path.join(tempdir, file.name), file.name)


def create_7z_archive_with_command_line(
    archive_path: str, contents: ArchiveContents, compression_format: ArchiveFormat
):
    assert compression_format == ArchiveFormat.SEVENZIP, (
        f"Only 7Z format is supported, got {compression_format}"
    )
    if shutil.which("7z") is None:
        raise PackageNotInstalledError("7z command is not installed")
    if contents.archive_comment:
        raise ValueError("Archive comments are not supported with 7z command line")

    abs_archive_path = os.path.abspath(archive_path)
    if os.path.exists(abs_archive_path):
        os.remove(abs_archive_path)

    with tempfile.TemporaryDirectory() as tempdir:
        file_groups = list(
            group_files_by_password_and_compression_method(contents.files)
        )
        logger.info("File groups: %s", file_groups)
        # if len(file_groups) > 1 and any(
        #     file.type == MemberType.SYMLINK and file.link_target_type == MemberType.DIR
        #     for file in contents.files
        # ):
        #     # There are some issues passing symlinks to directories to 7z command line,
        #     # so we can't use the approach of passing the individual filenames.
        #     raise ValueError(
        #         "Can't create 7z archive with symlinks to directories and multiple passwords."
        #     )

        for i, (password, compression_method, group_files) in enumerate(file_groups):
            # Clean any previous files in the temp dir
            for file in os.listdir(tempdir):
                os.remove(os.path.join(tempdir, file))

            write_files_to_dir(tempdir, group_files)

            command = ["7z", "a", "-snl", "-snh"]

            # Handle solid mode
            command.append(f"-ms={'on' if contents.solid else 'off'}")

            if contents.header_password:
                command.append(f"-p{contents.header_password}")
                command.append("-mhe=on")  # Encrypt header
            elif password:
                command.append(f"-p{password}")

            command.append(abs_archive_path)

            command.append(".")

            # if len(file_groups) > 1:
            #     # Add only the files in this group, so that they get added with the
            #     # provided password.
            #     # With this approach, 7z may follow symlinks to directories and add
            #     # their contents instead of a symlink, so it doesn't work for all cases.
            #     for file in group_files:
            #         command.append(file.name)
            # else:
            #     # Just add the current dir, and 7z will add all the contents.

            logger.info("Running command: %s", " ".join(command))
            subprocess.run(command, check=True, cwd=tempdir)


def create_iso_archive_with_pycdlib(
    archive_path: str, contents: ArchiveContents, compression_format: ArchiveFormat
):
    assert compression_format == ArchiveFormat.ISO, (
        f"Only ISO format is supported, got {compression_format}"
    )
    if pycdlib is None:
        raise PackageNotInstalledError("pycdlib is required to create ISO archives")

    abs_archive_path = os.path.abspath(archive_path)
    if os.path.exists(abs_archive_path):
        os.remove(abs_archive_path)

    with tempfile.TemporaryDirectory() as tempdir:
        write_files_to_dir(tempdir, contents.files)

        iso = pycdlib.pycdlib.PyCdlib()
        iso.new(interchange_level=3, rock_ridge="1.09", joliet=3)

        for root_dir, dirs, files in os.walk(tempdir):
            rel_root = os.path.relpath(root_dir, tempdir)
            if rel_root == ".":
                rel_root = ""

            for d in dirs:
                print(f"Adding directory {d} to {rel_root}")
                iso_path = os.path.join("/", rel_root, d)
                iso_path = iso_path.replace(os.sep, "/")
                iso.add_directory(
                    rr_name=d, iso_path=iso_path.upper(), joliet_path=iso_path
                )

            for f_name in files:
                print(f"Adding file {f_name} to {rel_root}")

                src_path = os.path.join(root_dir, f_name)
                iso_path = os.path.join("/", rel_root, f_name)
                iso_path = iso_path.replace(os.sep, "/")
                iso.add_file(
                    src_path,
                    iso_path=iso_path.upper(),
                    rr_name=f_name,
                    joliet_path=iso_path,
                )
        iso.write(abs_archive_path)
        iso.close()


def create_iso_archive_with_genisoimage(
    archive_path: str, contents: ArchiveContents, compression_format: ArchiveFormat
):
    assert compression_format == ArchiveFormat.ISO, (
        f"Only ISO format is supported, got {compression_format}"
    )
    abs_archive_path = os.path.abspath(archive_path)

    volume_id_args = ["-V", contents.archive_comment or ""]
    with tempfile.TemporaryDirectory() as tempdir:
        write_files_to_dir(tempdir, contents.files)

        subprocess.run(
            [
                "genisoimage",
                "-J",
                "-r",
                "-o",
                abs_archive_path,
                *volume_id_args,
                tempdir,
            ],
            check=True,
        )


def create_folder_archive(
    archive_path: str, contents: ArchiveContents, compression_format: ArchiveFormat
):
    assert compression_format == ArchiveFormat.FOLDER, (
        f"Only FOLDER format is supported, got {compression_format}"
    )
    write_files_to_dir(archive_path, contents.files)


GENERATION_METHODS_TO_GENERATOR = {
    GenerationMethod.ZIPFILE: create_zip_archive_with_zipfile,
    GenerationMethod.INFOZIP: create_zip_archive_with_infozip_command_line,
    GenerationMethod.TAR_COMMAND_LINE: create_tar_archive_with_command_line,
    GenerationMethod.TAR_LIBRARY: create_tar_archive_with_tarfile,
    GenerationMethod.RAR_COMMAND_LINE: create_rar_archive_with_command_line,
    GenerationMethod.PY7ZR: create_7z_archive_with_py7zr,
    GenerationMethod.SEVENZIP_COMMAND_LINE: create_7z_archive_with_command_line,
    GenerationMethod.SINGLE_FILE_COMMAND_LINE: create_single_file_compressed_archive_with_command_line,
    GenerationMethod.SINGLE_FILE_LIBRARY: create_single_file_compressed_archive_with_library,
    GenerationMethod.ISO_PYCDLIB: create_iso_archive_with_pycdlib,
    GenerationMethod.ISO_GENISOIMAGE: create_iso_archive_with_genisoimage,
    GenerationMethod.TEMP_DIR_POPULATION: create_folder_archive,
}


def create_archive(archive_info: SampleArchive, base_dir: str) -> str:
    full_path = archive_info.get_archive_path(base_dir)
    os.makedirs(os.path.dirname(full_path), exist_ok=True)

    if archive_info.creation_info.generation_method == GenerationMethod.EXTERNAL:
        # Check that the archive file exists
        if not os.path.exists(full_path):
            raise FileNotFoundError(f"External archive {full_path} does not exist")
        return full_path

    # Assert that header_password is None for formats that don't support it
    generator = GENERATION_METHODS_TO_GENERATOR[
        archive_info.creation_info.generation_method
    ]
    logger.info(
        f"Creating archive {archive_info.filename} with generator {archive_info.creation_info.generation_method} {generator}"
    )
    try:
        generator(
            full_path,
            contents=archive_info.contents,
            compression_format=archive_info.creation_info.format,
            **archive_info.creation_info.generation_method_options,
        )
    except Exception as e:
        logger.error(f"Error creating archive {archive_info.filename}: {e}")
        raise

    return full_path


def filter_archives(
    archives: list[SampleArchive], patterns: list[str] | None
) -> list[SampleArchive]:
    """
    Filter archives based on filename patterns.
    If patterns is None or empty, return all archives.
    Takes the basename of each pattern to match against archive filenames.
    """
    if not patterns:
        return archives

    # Convert patterns to their basenames
    pattern_basenames = [os.path.basename(pattern) for pattern in patterns]

    filtered = []
    for archive in archives:
        if any(
            fnmatch.fnmatch(archive.filename, pattern) for pattern in pattern_basenames
        ):
            filtered.append(archive)
    return filtered


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate test archives")
    parser.add_argument(
        "patterns",
        nargs="*",
        help="Optional list of file patterns to generate. If not specified, generates all archives.",
    )
    parser.add_argument(
        "--base-dir",
        help="Base directory where archives will be generated. Defaults to the script directory.",
    )
    args = parser.parse_args()

    # Use base_dir if provided, otherwise use the directory of the script
    base_dir = (
        args.base_dir
        if args.base_dir
        else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )

    # Filter archives based on patterns if provided
    archives_to_generate = filter_archives(SAMPLE_ARCHIVES, args.patterns)
    archives_to_generate = [
        archive
        for archive in archives_to_generate
        if archive.creation_info.format != ArchiveFormat.FOLDER
    ]

    if not archives_to_generate:
        print("No matching archives found.")
        exit(1)

    logger.info(f"Generating {len(archives_to_generate)} archives:")
    for archive in archives_to_generate:
        output_path = create_archive(archive, base_dir)
        assert os.path.exists(output_path)
        bullet = (
            "-"
            if archive.creation_info.generation_method != GenerationMethod.EXTERNAL
            else "s"
        )
        logger.info(f"  {bullet} {archive.filename}")
