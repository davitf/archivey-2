import io
import logging
import os
import struct
from datetime import datetime, timezone
from typing import Any, BinaryIO, Iterator, Optional

from archivey.exceptions import (
    ArchiveCorruptedError,
    ArchiveError,
    ArchiveStreamNotSeekableError,
)
from archivey.formats.compressed_streams import get_stream_open_fn
from archivey.formats.format_detection import EXTENSION_TO_FORMAT
from archivey.internal.base_reader import BaseArchiveReader
from archivey.internal.io_helpers import (  # Updated import
    is_seekable,
    is_stream,
    open_if_file,
    read_exact,
    run_with_exception_translation,
)
from archivey.internal.streams.decompress import DecompressorStream

# from archivey.internal.utils import open_if_file # Removed
from archivey.types import (
    ArchiveFormat,
    ArchiveInfo,
    ArchiveMember,
    ContainerFormat,
    CreateSystem,
    MemberType,
)

logger = logging.getLogger(__name__)


def _read_null_terminated_bytes(f: BinaryIO) -> bytes:
    str_bytes = bytearray()
    while True:
        b = f.read(1)
        if not b or b == b"\x00":
            break
        str_bytes.extend(b)
    return bytes(str_bytes)


def read_gzip_metadata(
    path: str | BinaryIO, member: ArchiveMember, use_stored_metadata: bool = False
) -> None:
    """
    Extract metadata from a .gz file without decompressing and update the ArchiveMember:
    - original internal filename (if present) goes into extra field
    - modification time (as POSIX timestamp)
    - CRC32 of uncompressed data
    - uncompressed size (modulo 2^32)
    - compression method
    - compression level
    - operating system
    - extra field data
    """

    extra_fields: dict[str, Any] = {}

    with open_if_file(path) as f:
        # Read the fixed 10-byte GZIP header
        header = read_exact(f, 10)
        if len(header) != 10 or header[:2] != b"\x1f\x8b":
            raise ArchiveCorruptedError("Not a valid GZIP file")

        # Parse header fields
        id1, id2, cm, flg, mtime_timestamp, xfl, os = struct.unpack("<4BIBB", header)

        if mtime_timestamp != 0:
            # gzip timestamps are in UTC.
            extra_fields["mtime"] = datetime.fromtimestamp(
                mtime_timestamp, tz=timezone.utc
            )
            logger.info(
                "GZIP metadata: mtime_timestamp=%s, mtime=%s",
                mtime_timestamp,
                extra_fields["mtime"],
            )
            if use_stored_metadata:
                member.mtime_with_tz = extra_fields["mtime"]

        # Add compression method and level
        extra_fields["compress_type"] = cm  # 8 = deflate, consistent with ZIP
        extra_fields["compress_level"] = xfl  # Compression level (0-9)

        member.create_system = (
            CreateSystem(os)
            if os in CreateSystem._value2member_map_
            else CreateSystem.UNKNOWN
        )

        # Handle optional fields
        if flg & 0x04:  # FEXTRA
            # The extra field contains a 2-byte length and then the data
            xlen = struct.unpack("<H", read_exact(f, 2))[0]
            extra_fields["extra"] = read_exact(f, xlen)  # Store raw extra field data

        if flg & 0x08:  # FNAME
            # The filename is a null-terminated string
            name_bytes = _read_null_terminated_bytes(f)
            stored_name = name_bytes.decode("utf-8", errors="replace")
            extra_fields["original_filename"] = stored_name
            member.raw_filename = stored_name  # always record what the archive stored
            if use_stored_metadata:
                member.filename = stored_name

        if flg & 0x10:  # FCOMMENT
            comment_bytes = _read_null_terminated_bytes(f)
            extra_fields["comment"] = comment_bytes.decode("utf-8", errors="replace")

        if flg & 0x02:  # FHCRC
            read_exact(f, 2)  # Skip CRC16

        if extra_fields:
            if member.extra is None:
                member.extra = {}
            member.extra.update(extra_fields)

        # Now seek to trailer and read CRC32 and ISIZE
        try:
            f.seek(-8, 2)
            crc32, isize = struct.unpack("<II", read_exact(f, 8))
            member.crc32 = crc32
            member.file_size = isize
        except io.UnsupportedOperation:
            # Stream not seekable or not seekable to end
            logger.info(
                "Stream not seekable to end when reading GZIP metadata: %s", path
            )
            pass




class SingleFileReader(BaseArchiveReader):
    """Reader for raw compressed files (gz, bz2, xz, zstd, lz4)."""

    def __init__(
        self,
        format: ArchiveFormat,
        archive_path: BinaryIO | str,
        *,
        pwd: bytes | str | None = None,
        streaming_only: bool = False,
    ):
        """Initialize the reader.

        Args:
            archive_path: Path to the compressed file
            pwd: Password for decryption (not supported for compressed files)
            format: The format of the archive. If None, will be detected from the file extension.
            **kwargs: Additional options (ignored)
        """
        if format.container != ContainerFormat.RAW_STREAM:
            raise ValueError(f"Unsupported archive format: {format}")

        if pwd is not None:
            raise ValueError("Compressed files do not support password protection")

        super().__init__(
            format=format,
            archive_path=archive_path,
            streaming_only=streaming_only,
            members_list_supported=True,
            pwd=pwd,
        )

        if self.path_str is None:
            assert is_stream(self.path_or_stream)
            # Opening from a stream
            member_name = "uncompressed"
            mtime = None
            compress_size = None
            seekable = is_seekable(self.path_or_stream)

        else:
            base_name = os.path.basename(self.path_str)
            base_no_ext, ext = os.path.splitext(base_name)
            # Check if the extension is a known compressed format
            if ext.lower() in EXTENSION_TO_FORMAT:
                member_name = base_no_ext
            else:
                member_name = base_name + ".uncompressed"

            mtime = datetime.fromtimestamp(
                os.path.getmtime(self.path_str), tz=timezone.utc
            )
            compress_size = os.path.getsize(self.path_str)
            seekable = True

        if not seekable and not streaming_only:
            raise ArchiveStreamNotSeekableError(
                "Tried to open a random-access compressed file, but stream is not seekable"
            )

        self.use_stored_metadata = self.config.use_single_file_stored_metadata

        # Create a single member representing the decompressed file
        self.member = ArchiveMember(
            filename=member_name,
            raw_filename=None,  # single-file formats generally don't store the filename
            file_size=None,  # Not available for all formats
            compress_size=compress_size,
            mtime_with_tz=mtime,
            type=MemberType.FILE,
            compression_method=self.format.file_extension(),
            crc32=None,
        )

        if seekable:
            if self.format == ArchiveFormat.GZIP:
                read_gzip_metadata(archive_path, self.member, self.use_stored_metadata)

        # Open the file to see if it's supported by the library and valid.
        # To avoid opening the file twice, we'll store the reference and return it
        # on the first open() call.
        self._opener, self._exception_translator = get_stream_open_fn(
            self.format.stream, self.config
        )

        self.fileobj: BinaryIO | None = run_with_exception_translation(
            lambda: self._opener(archive_path),
            self._exception_translator,
            archive_path=self.path_str,
            member_name=self.member.filename,
        )

        if seekable and self.fileobj is not None and self.fileobj.seekable():
            if self.format in (ArchiveFormat.XZ, ArchiveFormat.LZIP):
                if isinstance(self.fileobj, DecompressorStream):
                    self.member.file_size = self.fileobj.try_get_size()

    def _translate_exception(self, e: Exception) -> Optional[ArchiveError]:
        return self._exception_translator(e)

    def iter_members_for_registration(self) -> Iterator[ArchiveMember]:
        yield self.member

    def _close_archive(self) -> None:
        """Close the archive and release any resources."""
        if self.fileobj is not None:
            self.fileobj.close()
            self.fileobj = None

    def get_archive_info(self) -> ArchiveInfo:
        """Get detailed information about the archive's format."""
        return ArchiveInfo(
            format=self.format,
            is_solid=False,
        )

    def _open_member(
        self,
        member: ArchiveMember,
        pwd: str | bytes | None,
        for_iteration: bool,
    ) -> BinaryIO:
        if pwd is not None:
            raise ValueError("Compressed files do not support password protection")

        if self.fileobj is None:
            return self._opener(self.path_or_stream)

        fileobj = self.fileobj
        self.fileobj = None
        return fileobj
