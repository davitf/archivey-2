import logging
import os
import stat
import tarfile
from datetime import datetime, timezone
from typing import TYPE_CHECKING, BinaryIO, Iterator, List, Optional, cast

from archivey.exceptions import (
    ArchiveCorruptedError,
    ArchiveEOFError,
    ArchiveError,
    ArchiveMemberCannotBeOpenedError,
    ArchiveStreamNotSeekableError,
)
from archivey.formats.compressed_streams import open_stream
from archivey.internal.base_reader import (
    ArchiveInfo,
    ArchiveMember,
    BaseArchiveReader,
)
from archivey.internal.io_helpers import (
    ensure_binaryio,
    ensure_bufferedio,
    is_seekable,
    read_exact,
    run_with_exception_translation,
)
from archivey.types import ArchiveFormat, ContainerFormat, MemberType, StreamFormat

if TYPE_CHECKING:
    from io import BufferedIOBase

logger = logging.getLogger(__name__)


class TarReader(BaseArchiveReader):
    """Reader for TAR archives and compressed TAR archives."""

    def _translate_exception(self, e: Exception) -> Optional[ArchiveError]:
        if isinstance(e, tarfile.ReadError):
            if "unexpected end of data" in str(e).lower():
                return ArchiveEOFError("TAR archive is truncated")
            return ArchiveCorruptedError(f"Error reading TAR archive: {e}")

        return None

    def __init__(
        self,
        archive_path: BinaryIO | str,
        format: ArchiveFormat,
        *,
        streaming_only: bool = False,
        pwd: bytes | str | None = None,
    ):
        """Initialize the reader.

        Args:
            archive_path: Path to the TAR archive
            pwd: Password for decryption (not supported for TAR)
            format: The format of the archive. If None, will be detected from the file extension.
        """
        if format.container != ContainerFormat.TAR:
            raise ValueError(f"Unsupported archive format: {format}")

        if pwd is not None:
            raise ValueError("TAR format does not support password protection")

        super().__init__(
            format=format,
            archive_path=archive_path,
            streaming_only=streaming_only,
            members_list_supported=False,
            pwd=pwd,
        )
        self._streaming_only = streaming_only
        self._format_info: ArchiveInfo | None = None
        self._fileobj: BufferedIOBase | None = None
        self._close_fileobj: bool

        logger.debug(
            "TarReader init: %s %s %s",
            archive_path,
            format,
            streaming_only,
        )

        if format.stream != StreamFormat.UNCOMPRESSED:
            self.compression_method = str(format.stream.value)
            # Ensure the stream is buffered. tarfile may fail when reading a file
            # if read() returns fewer bytes than requested (specifically
            # inside tarfile._FileInFile.read(), line 696 in Python 3.13.5).
            self._fileobj = ensure_bufferedio(
                open_stream(format.stream, archive_path, self.config)
            )

            self._close_fileobj = True
            logger.debug(
                "Compressed tar opened: %s seekable=%s",
                self._fileobj,
                self._fileobj.seekable(),
            )

        else:
            self.compression_method = "store"
            if isinstance(archive_path, str):
                self._fileobj = open(archive_path, "rb")
                self._close_fileobj = True
            else:
                self._fileobj = ensure_bufferedio(archive_path)
                self._close_fileobj = False

        if not streaming_only and not is_seekable(self._fileobj):
            raise ArchiveStreamNotSeekableError(
                f"Tried to open a random-access {format.file_extension()} file, but inner stream is not seekable ({self._fileobj})"
            )

        open_mode = "r|" if streaming_only else "r:"

        def _open_tar() -> tarfile.TarFile:
            # Fail on any error.
            return tarfile.open(
                name=archive_path if isinstance(archive_path, str) else None,
                fileobj=cast("BinaryIO", self._fileobj),
                mode=open_mode,
                errorlevel=2,
            )

        self._archive: tarfile.TarFile | None = run_with_exception_translation(
            _open_tar,
            self._translate_exception,
            archive_path=str(archive_path),
        )
        logger.debug(
            "Tar opened: %s seekable=%s",
            self._archive,
            self._fileobj.seekable(),
        )

    def _close_archive(self) -> None:
        """Close the archive and release any resources."""
        self._archive.close()  # type: ignore
        self._archive = None

        if self._close_fileobj and self._fileobj is not None:
            self._fileobj.close()
            self._fileobj = None

    def get_members_if_available(self) -> List[ArchiveMember] | None:
        if self._streaming_only:
            return None
        return self.get_members()

    def _tarinfo_to_archive_member(self, info: tarfile.TarInfo) -> ArchiveMember:
        filename = info.name
        if info.isdir() and not filename.endswith("/"):
            filename += "/"

        uid = info.uid if info.uid != 0 else None
        gid = info.gid if info.gid != 0 else None

        return ArchiveMember(
            filename=filename,
            raw_filename=info.name,
            file_size=info.size,
            compress_size=None,
            # TAR files store times in UTC.
            mtime_with_tz=datetime.fromtimestamp(info.mtime, tz=timezone.utc)
            if info.mtime
            else None,
            type=(
                MemberType.FILE
                if info.isfile()
                else MemberType.DIR
                if info.isdir()
                else MemberType.SYMLINK
                if info.issym()
                else MemberType.HARDLINK
                if info.islnk()
                else MemberType.OTHER
            ),
            mode=stat.S_IMODE(info.mode) if hasattr(info, "mode") else None,
            uid=uid,
            gid=gid,
            uname=info.uname or None,
            gname=info.gname or None,
            link_target=info.linkname if info.issym() or info.islnk() else None,
            crc32=None,  # TAR doesn't have CRC
            compression_method=self.compression_method,
            extra={
                "type": info.type,
                "mode": info.mode,
                "linkname": info.linkname,
                "devmajor": info.devmajor,
                "devminor": info.devminor,
            },
            raw_info=info,
        )

    def _check_tar_integrity(self, last_tarinfo: tarfile.TarInfo) -> None:
        # See what's after the last tarinfo. It should be two empty blocks.
        data_size = last_tarinfo.size
        # Round up to the next multiple of 512.
        data_blocks = (data_size + 511) & ~511
        next_member_offset = last_tarinfo.offset_data + data_blocks

        if self._fileobj is None:
            logger.warning("Cannot check tar integrity: file object is missing")
            return

        if is_seekable(self._fileobj):
            self._fileobj.seek(next_member_offset)
        else:
            # We should ideally use self._fileobj.tell() here, but it doesn't work
            # for non-seekable streams. TarFile wraps the stream in a file-like object
            # that has a tell() method.
            remaining = next_member_offset - self._archive.fileobj.tell()  # type: ignore

            if remaining > 0:
                data = read_exact(
                    self._fileobj, remaining
                )  # self._fileobj.read(remaining)
                assert len(data) == remaining, (
                    f"Expected {remaining} bytes, got {len(data)}"
                )
            elif remaining < 0:
                # The pointer has moved past the end of the file, we can't check for
                # integrity.
                return

        expected_zeroes = 512 * 2
        data = read_exact(
            self._fileobj, expected_zeroes
        )  # self._fileobj.read(expected_zeroes)
        if len(data) < expected_zeroes:
            raise ArchiveCorruptedError(
                f"Missing data after last tarinfo: {len(data)} bytes"
            )
        if data != b"\x00" * expected_zeroes:
            raise ArchiveCorruptedError(f"Invalid data after last tarinfo: {data!r}")

    def _prepare_member_for_open(
        self, member: ArchiveMember, *, pwd: bytes | str | None, for_iteration: bool
    ) -> ArchiveMember:
        if self._streaming_only and not for_iteration:
            raise ValueError(
                "Archive opened in streaming mode does not support opening specific members."
            )
        if pwd is not None:
            raise ValueError("TAR format does not support password protection.")
        return member

    def _open_member(
        self,
        member: ArchiveMember,
        pwd: str | bytes | None,
        for_iteration: bool,
    ) -> BinaryIO:
        assert self._archive is not None

        tarinfo = cast("tarfile.TarInfo", member.raw_info)

        assert self._archive is not None
        stream = self._archive.extractfile(tarinfo)
        if stream is None:
            raise ArchiveMemberCannotBeOpenedError(
                f"Member {member.filename} cannot be opened"
            )
        return ensure_binaryio(stream)

    def get_archive_info(self) -> ArchiveInfo:
        """Get detailed information about the archive's format.

        Returns:
            ArchiveInfo: Detailed format information
        """
        self.check_archive_open()
        assert self._archive is not None

        if self._format_info is None:
            format = self.format
            self._format_info = ArchiveInfo(
                format=format,
                is_solid=format.stream is not None
                and format.stream != StreamFormat.UNCOMPRESSED,
                extra={
                    "format_version": self._archive.format
                    if hasattr(self._archive, "format")
                    else None,
                    "encoding": self._archive.encoding
                    if hasattr(self._archive, "encoding")
                    else None,
                },
            )
        return self._format_info

    def iter_members_for_registration(self) -> Iterator[ArchiveMember]:
        self.check_archive_open()
        assert self._archive is not None

        try:
            tarinfo: tarfile.TarInfo | None = None
            for tarinfo in self._archive:
                yield self._tarinfo_to_archive_member(tarinfo)

            if self.config.tar_check_integrity and tarinfo is not None:
                self._check_tar_integrity(tarinfo)
        except (tarfile.TarError, OSError) as e:
            translated = self._translate_exception(e)
            if translated is not None:
                raise translated from e
            raise

    @classmethod
    def is_tar_file(cls, file: BinaryIO | str | os.PathLike[str]) -> bool:
        return tarfile.is_tarfile(file)
