import io
import logging
import os
import stat
import struct
import zipfile
from datetime import datetime, timezone
from typing import BinaryIO, Iterator, Optional, cast

from archivey.exceptions import (
    ArchiveCorruptedError,
    ArchiveEncryptedError,
    ArchiveError,
    ArchiveStreamNotSeekableError,
    ArchiveUnsupportedFeatureError,
)
from archivey.internal.base_reader import (
    BaseArchiveReader,
)
from archivey.internal.io_helpers import (
    is_seekable,
    is_stream,
    run_with_exception_translation,
)
from archivey.internal.utils import decode_bytes_with_fallback, str_to_bytes
from archivey.types import (
    ArchiveFormat,
    ArchiveInfo,
    ArchiveMember,
    CreateSystem,
    MemberType,
)

# Encoding fallbacks used when decoding strings stored in the ZIP metadata.
_ZIP_ENCODINGS = ["utf-8", "cp437", "cp1252", "latin-1"]

logger = logging.getLogger(__name__)


def get_zipinfo_timestamp(zip_info: zipfile.ZipInfo) -> datetime | None:
    """Return the modification time stored in ``zip_info``.

    Extended timestamp extra fields are used when available because the
    standard ``ZipInfo.date_time`` field only stores timestamps with a two-second
    granularity.
    """
    if zip_info.date_time == (1980, 0, 0, 0, 0, 0):
        return None

    try:
        main_modtime = datetime(*zip_info.date_time)
    except ValueError:
        logger.warning(
            f"Invalid date time in zipinfo for {zip_info.filename}: {zip_info.date_time}"
        )
        main_modtime = None

    if not zip_info.extra:
        return main_modtime

    # Parse extended timestamp extra field (0x5455)
    pos = 0
    while pos < len(zip_info.extra):
        if len(zip_info.extra) - pos < 4:  # Need at least 4 bytes for header
            break

        tp, ln = struct.unpack("<HH", zip_info.extra[pos : pos + 4])

        if tp == 0x5455:  # Extended Timestamp
            flags = zip_info.extra[pos + 4]

            # Check if modification time is present (bit 0)
            if flags & 0x01:
                # Read modification time (4 bytes, Unix timestamp)
                mod_time = int.from_bytes(zip_info.extra[pos + 5 : pos + 9], "little")

                # Convert to datetime
                if mod_time > 0:
                    extra_modtime = datetime.fromtimestamp(mod_time, tz=timezone.utc)
                    logger.debug(
                        "Modtime: main=%s, extra=%s timestamp=%s",
                        main_modtime,
                        extra_modtime,
                        mod_time,
                    )
                    return extra_modtime

        # Skip this field: 4 bytes header + data_size
        pos += 4 + ln

    logger.info("Modtime: main=%s", main_modtime)
    return main_modtime


# Taken from zipfile.compressor_names
ZIP_COMPRESSION_METHODS = {
    0: "store",
    1: "shrink",
    2: "reduce",
    3: "reduce",
    4: "reduce",
    5: "reduce",
    6: "implode",
    7: "tokenize",
    8: "deflate",
    9: "deflate64",
    10: "implode",
    12: "bzip2",
    14: "lzma",
    18: "terse",
    19: "lz77",
    97: "wavpack",
    98: "ppmd",
}


class ZipReader(BaseArchiveReader):
    """Reader for ZIP archives."""

    def _translate_exception(self, e: Exception) -> Optional[ArchiveError]:
        if isinstance(e, zipfile.BadZipFile):
            return ArchiveCorruptedError("Error reading ZIP archive")
        if isinstance(e, RuntimeError) and "password required" in str(e).lower():
            return ArchiveEncryptedError("Password required")
        if isinstance(e, RuntimeError) and "Bad password" in str(e):
            return ArchiveEncryptedError("Wrong password specified")
        if isinstance(e, io.UnsupportedOperation) and (
            "seek" in str(e) or "non" in str(e)
        ):
            return ArchiveStreamNotSeekableError(
                "ZIP archives do not support non-seekable streams"
            )
        if isinstance(
            e, NotImplementedError
        ) and "That compression method is not supported" in str(e):
            return ArchiveUnsupportedFeatureError("Compression method is not supported")
        return None

    def __init__(
        self,
        format: ArchiveFormat,
        archive_path: BinaryIO | str | bytes | os.PathLike[str],
        pwd: bytes | str | None = None,
        streaming_only: bool = False,
    ):
        if format != ArchiveFormat.ZIP:
            raise ValueError(f"Unsupported archive format: {format}")

        super().__init__(
            format=format,
            archive_path=archive_path,
            pwd=pwd,
            streaming_only=streaming_only,
            members_list_supported=True,
        )

        if is_stream(self.path_or_stream) and not is_seekable(self.path_or_stream):
            raise ArchiveStreamNotSeekableError(
                "ZIP archives do not support non-seekable streams"
            )

        self._format_info: ArchiveInfo | None = None

        def _open_zip() -> zipfile.ZipFile:
            # The typeshed definition of ZipFile is incorrect, it should allow byte streams.
            return zipfile.ZipFile(archive_path, "r")  # type: ignore

        self._archive: zipfile.ZipFile | None = run_with_exception_translation(
            _open_zip,
            self._translate_exception,
            archive_path=str(archive_path),
        )

    def _close_archive(self) -> None:
        """Close the archive and release any resources."""
        self._archive.close()  # type: ignore
        self._archive = None

    def _zipinfo_to_archive_member(self, info: zipfile.ZipInfo) -> ArchiveMember:
        """Convert ``ZipInfo`` to :class:`ArchiveMember`."""
        mode = info.external_attr >> 16
        is_dir = info.is_dir()
        is_link = stat.S_ISLNK(mode)

        compression_method = ZIP_COMPRESSION_METHODS.get(info.compress_type, "unknown")

        logger.info(
            f"Filename: {info.filename}: compression_method={compression_method} {info.compress_type}"
        )

        return ArchiveMember(
            filename=info.filename.replace("\\", "/"),
            raw_filename=info.filename,
            file_size=info.file_size,
            compress_size=info.compress_size,
            mtime_with_tz=get_zipinfo_timestamp(info),
            type=MemberType.DIR
            if is_dir
            else MemberType.SYMLINK
            if is_link
            else MemberType.FILE,
            mode=stat.S_IMODE(mode) if info.external_attr != 0 else None,
            crc32=info.CRC if hasattr(info, "CRC") else None,
            compression_method=compression_method,
            comment=decode_bytes_with_fallback(info.comment, _ZIP_ENCODINGS)
            if info.comment
            else None,
            encrypted=bool(info.flag_bits & 0x1),
            create_system=CreateSystem(info.create_system)
            if info.create_system in CreateSystem._value2member_map_
            else CreateSystem.UNKNOWN,
            extra={
                "compress_type": info.compress_type,
                "compress_size": info.compress_size,
                "create_system": info.create_system,
                "create_version": info.create_version,
                "extract_version": info.extract_version,
                "flag_bits": info.flag_bits,
                "volume": info.volume,
            },
            raw_info=info,
            link_target=self._read_link_target(info),
        )

    def _read_link_target(self, info: zipfile.ZipInfo) -> str | None:
        """Return the symlink target for ``info`` if it is a symlink."""
        assert self._archive is not None

        if stat.S_ISLNK(info.external_attr >> 16):
            with self._archive.open(info.filename) as f:
                return f.read().decode("utf-8")
        return None

    def _open_member(
        self,
        member: ArchiveMember,
        pwd: str | bytes | None,
        for_iteration: bool,
    ) -> BinaryIO:
        assert self._archive is not None

        return cast(
            "BinaryIO",
            self._archive.open(
                cast("zipfile.ZipInfo", member.raw_info),
                pwd=str_to_bytes(
                    pwd if pwd is not None else self.get_archive_password()
                ),
            ),
        )

    def get_archive_info(self) -> ArchiveInfo:
        """Get detailed information about the archive's format."""
        self.check_archive_open()
        assert self._archive is not None

        if self._format_info is None:
            self._format_info = ArchiveInfo(
                format=self.format,
                is_solid=False,  # ZIP archives are never solid
                comment=decode_bytes_with_fallback(
                    self._archive.comment, _ZIP_ENCODINGS
                )
                if self._archive.comment
                else None,
                extra={
                    "is_encrypted": any(
                        info.flag_bits & 0x1 for info in self._archive.infolist()
                    ),
                },
            )
        return self._format_info

    def iter_members_for_registration(self) -> Iterator[ArchiveMember]:
        assert self._archive is not None

        for info in self._archive.infolist():
            yield self._zipinfo_to_archive_member(info)

    @classmethod
    def is_zip_file(cls, file: BinaryIO | str | os.PathLike[str]) -> bool:
        return zipfile.is_zipfile(file)
