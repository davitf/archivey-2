"""
Common types and enums used internally by Archivey.

Most public types are exposed through the `archivey` module, but advanced or
format-specific types can be imported from here as needed.
"""

import io  # Required for ReadableStreamLikeOrSimilar
import sys
from typing import (
    IO,
    TYPE_CHECKING,
    Callable,
    Protocol,
    overload,
    runtime_checkable,
)

if TYPE_CHECKING or sys.version_info >= (3, 11):
    from enum import StrEnum
else:
    from backports.strenum import StrEnum

from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import IntEnum
from typing import Any, ClassVar, Optional, Tuple

# Windows FILE_ATTRIBUTE_* constants (stored in windows_attrs field)
FA_READONLY = 0x0001
FA_HIDDEN = 0x0002
FA_SYSTEM = 0x0004
FA_DIRECTORY = 0x0010
FA_ARCHIVE = 0x0020
FA_REPARSE_POINT = 0x0400
FA_COMPRESSED = 0x0800
FA_ENCRYPTED = 0x4000

# Key used in ArchiveMember.extra to mark Windows NTFS junction points
EXTRA_IS_JUNCTION = "is_junction"


class ContainerFormat(StrEnum):
    """Supported container formats."""

    ZIP = "zip"
    RAR = "rar"
    SEVENZIP = "7z"
    TAR = "tar"
    ISO = "iso"
    FOLDER = "folder"
    RAW_STREAM = "raw_stream"
    UNKNOWN = "unknown"


class StreamFormat(StrEnum):
    """Supported stream formats."""

    GZIP = "gz"
    BZIP2 = "bz2"
    XZ = "xz"
    ZSTD = "zstd"
    LZ4 = "lz4"
    LZIP = "lz"
    ZLIB = "zz"
    BROTLI = "br"
    UNIX_COMPRESS = "Z"
    UNCOMPRESSED = "uncompressed"


@dataclass(frozen=True)
class ArchiveFormat:
    """Supported archive and compression formats."""

    # Render the class variables as enum members in the docs
    # (see scripts/griffe_extensions.py:EnumMembersAsTable)
    __enum_like__ = True

    container: ContainerFormat
    stream: StreamFormat

    def file_extension(self) -> str:
        """Return the file extension for the archive format."""
        parts = []
        if self.container != ContainerFormat.RAW_STREAM:
            parts.append(self.container.value)
        if self.stream != StreamFormat.UNCOMPRESSED:
            parts.append(self.stream.value)
        return ".".join(parts)

    def __str__(self) -> str:
        return self.file_extension()

    ZIP: ClassVar["ArchiveFormat"]
    RAR: ClassVar["ArchiveFormat"]
    SEVENZIP: ClassVar["ArchiveFormat"]
    GZIP: ClassVar["ArchiveFormat"]
    BZIP2: ClassVar["ArchiveFormat"]
    XZ: ClassVar["ArchiveFormat"]
    ZSTD: ClassVar["ArchiveFormat"]
    LZ4: ClassVar["ArchiveFormat"]
    LZIP: ClassVar["ArchiveFormat"]
    ZLIB: ClassVar["ArchiveFormat"]
    BROTLI: ClassVar["ArchiveFormat"]
    UNIX_COMPRESS: ClassVar["ArchiveFormat"]
    TAR: ClassVar["ArchiveFormat"]
    TAR_GZ: ClassVar["ArchiveFormat"]
    TAR_BZ2: ClassVar["ArchiveFormat"]
    TAR_XZ: ClassVar["ArchiveFormat"]
    TAR_ZSTD: ClassVar["ArchiveFormat"]
    TAR_LZ4: ClassVar["ArchiveFormat"]
    TAR_Z: ClassVar["ArchiveFormat"]
    ISO: ClassVar["ArchiveFormat"]
    FOLDER: ClassVar["ArchiveFormat"]
    UNKNOWN: ClassVar["ArchiveFormat"]


ArchiveFormat.ZIP = ArchiveFormat(ContainerFormat.ZIP, StreamFormat.UNCOMPRESSED)
ArchiveFormat.RAR = ArchiveFormat(ContainerFormat.RAR, StreamFormat.UNCOMPRESSED)
ArchiveFormat.SEVENZIP = ArchiveFormat(
    ContainerFormat.SEVENZIP, StreamFormat.UNCOMPRESSED
)
ArchiveFormat.GZIP = ArchiveFormat(ContainerFormat.RAW_STREAM, StreamFormat.GZIP)
ArchiveFormat.BZIP2 = ArchiveFormat(ContainerFormat.RAW_STREAM, StreamFormat.BZIP2)
ArchiveFormat.XZ = ArchiveFormat(ContainerFormat.RAW_STREAM, StreamFormat.XZ)
ArchiveFormat.ZSTD = ArchiveFormat(ContainerFormat.RAW_STREAM, StreamFormat.ZSTD)
ArchiveFormat.LZ4 = ArchiveFormat(ContainerFormat.RAW_STREAM, StreamFormat.LZ4)
ArchiveFormat.LZIP = ArchiveFormat(ContainerFormat.RAW_STREAM, StreamFormat.LZIP)
ArchiveFormat.ZLIB = ArchiveFormat(ContainerFormat.RAW_STREAM, StreamFormat.ZLIB)
ArchiveFormat.BROTLI = ArchiveFormat(ContainerFormat.RAW_STREAM, StreamFormat.BROTLI)
ArchiveFormat.UNIX_COMPRESS = ArchiveFormat(
    ContainerFormat.RAW_STREAM, StreamFormat.UNIX_COMPRESS
)
ArchiveFormat.TAR = ArchiveFormat(ContainerFormat.TAR, StreamFormat.UNCOMPRESSED)
ArchiveFormat.TAR_GZ = ArchiveFormat(ContainerFormat.TAR, StreamFormat.GZIP)
ArchiveFormat.TAR_BZ2 = ArchiveFormat(ContainerFormat.TAR, StreamFormat.BZIP2)
ArchiveFormat.TAR_XZ = ArchiveFormat(ContainerFormat.TAR, StreamFormat.XZ)
ArchiveFormat.TAR_ZSTD = ArchiveFormat(ContainerFormat.TAR, StreamFormat.ZSTD)
ArchiveFormat.TAR_LZ4 = ArchiveFormat(ContainerFormat.TAR, StreamFormat.LZ4)
ArchiveFormat.TAR_Z = ArchiveFormat(ContainerFormat.TAR, StreamFormat.UNIX_COMPRESS)
ArchiveFormat.ISO = ArchiveFormat(ContainerFormat.ISO, StreamFormat.UNCOMPRESSED)
ArchiveFormat.FOLDER = ArchiveFormat(ContainerFormat.FOLDER, StreamFormat.UNCOMPRESSED)
ArchiveFormat.UNKNOWN = ArchiveFormat(
    ContainerFormat.UNKNOWN, StreamFormat.UNCOMPRESSED
)


class MemberType(StrEnum):
    """Possible types of archive members."""

    FILE = "file"
    """A regular file."""
    DIR = "dir"
    """A directory."""
    SYMLINK = "symlink"
    """A symbolic link."""
    HARDLINK = "hardlink"
    """A hard link."""
    OTHER = "other"
    """An other type of member."""


class CreateSystem(IntEnum):
    """
    Operating system that created the archive member, if known.

    These values match the `create_system` field from the ZIP specification
    and the Python `zipfile` module. Other formats may report compatible values
    where applicable.
    """

    FAT = 0
    AMIGA = 1
    VMS = 2
    UNIX = 3
    VM_CMS = 4
    ATARI_ST = 5
    OS2_HPFS = 6
    MACINTOSH = 7
    Z_SYSTEM = 8
    CPM = 9
    TOPS20 = 10
    NTFS = 11
    QDOS = 12
    ACORN_RISCOS = 13
    UNKNOWN = 255


@dataclass
class ArchiveInfo:
    """Metadata about the archive format and container-level properties."""

    format: ArchiveFormat = field(metadata={"description": "The archive format type"})
    version: Optional[str] = field(
        default=None,
        metadata={
            "description": 'The version of the archive format. Format-dependent (e.g. "4" for RAR4, "5" for RAR5).'
        },
    )
    is_solid: bool = field(
        default=False,
        metadata={
            "description": "Whether the archive is solid, i.e. decompressing a member may require decompressing others before it."
        },
    )
    extra: dict[str, Any] = field(
        # Using a lambda instead of "dict" to avoid a mkdocstrings error
        default_factory=lambda: {},
        metadata={
            "description": "Extra format-specific information about the archive."
        },
    )
    comment: Optional[str] = field(
        default=None,
        metadata={
            "description": "A comment associated with the archive. Supported by some formats."
        },
    )


@dataclass
class ArchiveMember:
    """Represents a file within an archive."""

    filename: str = field(
        metadata={
            "description": "The name of the member, normalized: forward slashes, directory names end with /."
        }
    )
    file_size: Optional[int] = field(
        metadata={"description": "The size of the member's data in bytes, if known."}
    )
    compress_size: Optional[int] = field(
        metadata={
            "description": "The size of the member's compressed data in bytes, if known."
        }
    )
    mtime_with_tz: Optional[datetime] = field(
        metadata={
            "description": "The modification time of the member. May include a timezone (likely UTC) if the archive format uses global time, or be a naive datetime if the archive format uses local time."
        }
    )
    type: MemberType = field(metadata={"description": "The type of the member."})
    # Optional fields below — all default to None / False / empty
    raw_filename: Optional[str] = field(
        default=None,
        metadata={
            "description": "The filename exactly as stored in the archive, without normalization. None if the format does not store a separate raw filename."
        },
    )
    atime: Optional[datetime] = field(
        default=None,
        metadata={
            "description": "Last access time of the member, if recorded by the format."
        },
    )
    ctime: Optional[datetime] = field(
        default=None,
        metadata={
            "description": "Creation time (Windows) or inode-change time (Unix) of the member, if recorded by the format."
        },
    )
    mode: Optional[int] = field(
        default=None, metadata={"description": "Unix permissions of the member."}
    )
    uid: Optional[int] = field(
        default=None,
        metadata={"description": "Unix user ID of the member's owner, if known."},
    )
    gid: Optional[int] = field(
        default=None,
        metadata={"description": "Unix group ID of the member's owner, if known."},
    )
    uname: Optional[str] = field(
        default=None,
        metadata={"description": "Username of the member's owner, if known."},
    )
    gname: Optional[str] = field(
        default=None,
        metadata={"description": "Group name of the member's owner, if known."},
    )
    crc32: Optional[int] = field(
        default=None,
        metadata={"description": "The CRC32 checksum of the member's data, if known."},
    )
    compression_method: Optional[str] = field(
        default=None,
        metadata={
            "description": "The compression method used for the member, if known. Format-dependent."
        },
    )
    comment: Optional[str] = field(
        default=None,
        metadata={
            "description": "A comment associated with the member. Supported by some formats."
        },
    )
    create_system: Optional[CreateSystem] = field(
        default=None,
        metadata={
            "description": "The operating system on which the member was created, if known."
        },
    )
    windows_attrs: Optional[int] = field(
        default=None,
        metadata={
            "description": "Windows FILE_ATTRIBUTE_* bitmask, if recorded by the format. See FA_* constants."
        },
    )
    encrypted: bool = field(
        default=False,
        metadata={"description": "Whether the member's data is encrypted, if known."},
    )
    extra: dict[str, Any] = field(
        # Using a lambda instead of "dict" to avoid a mkdocstrings error
        default_factory=lambda: {},
        metadata={"description": "Extra format-specific information about the member."},
    )
    link_target: Optional[str] = field(
        default=None,
        metadata={
            "description": "The target of the link, if the member is a symbolic or hard link. For hard links, this is the path of another file in the archive; for symbolic links, this is the target path relative to the directory containing the link. In some formats, the link target is stored in the member's data, and may not be available when getting the member list, and/or may be encrypted. In those cases, the link target will be filled when iterating through the archive."
        },
    )
    raw_info: Optional[Any] = field(
        default=None,
        metadata={"description": "The raw info object returned by the archive reader."},
    )
    _member_id: Optional[int] = field(
        default=None,
    )

    # A flag indicating whether the member has been modified by a filter.
    _edited_by_filter: bool = field(
        default=False,
    )

    @property
    def mtime(self) -> Optional[datetime]:
        """Returns `mtime_with_tz` without timezone information, for compatibility."""
        if self.mtime_with_tz is None:
            return None
        return self.mtime_with_tz.replace(tzinfo=None)

    @property
    def member_id(self) -> int:
        """Unique ID for this member within the archive.

        Values are assigned in archive order and can be used to
        disambiguate identical filenames or preserve ordering.
        """
        if self._member_id is None:
            raise ValueError("Member index not yet set")
        return self._member_id

    _archive_id: Optional[str] = field(
        default=None,
    )

    @property
    def archive_id(self) -> str:
        """Unique ID for the archive this member belongs to."""
        if self._archive_id is None:
            raise ValueError("Archive ID not yet set")
        return self._archive_id

    # Properties for zipfile compatibility (and others, as much as possible)
    @property
    def date_time(self) -> Optional[Tuple[int, int, int, int, int, int]]:
        """(year, month, day, hour, minute, second) tuple for `zipfile` compatibility."""
        if self.mtime is None:
            return None
        return (
            self.mtime.year,
            self.mtime.month,
            self.mtime.day,
            self.mtime.hour,
            self.mtime.minute,
            self.mtime.second,
        )

    @property
    def is_file(self) -> bool:
        """Convenience property returning ``True`` if the member is a regular file."""
        return self.type == MemberType.FILE

    @property
    def is_dir(self) -> bool:
        """Convenience property returning ``True`` if the member represents a directory."""
        return self.type == MemberType.DIR

    @property
    def is_link(self) -> bool:
        """Convenience property returning ``True`` if the member is a symbolic or hard link."""
        return self.type == MemberType.SYMLINK or self.type == MemberType.HARDLINK

    @property
    def is_other(self) -> bool:
        """Convenience property returning ``True`` if the member's type is neither file, directory nor link."""
        return self.type == MemberType.OTHER

    @property
    def is_junction(self) -> bool:
        """Convenience property returning ``True`` if the member is a Windows NTFS junction point.

        Junction points are represented as ``MemberType.SYMLINK`` with ``extra["is_junction"] == True``.
        """
        return bool(self.extra.get(EXTRA_IS_JUNCTION))

    @property
    def CRC(self) -> Optional[int]:
        """Alias for `crc32` (for `zipfile` compatibility)."""
        return self.crc32

    def replace(self, **kwargs: Any) -> "ArchiveMember":
        """Return a copy of this member with selected fields updated.

        Used primarily by extraction filters to modify metadata without
        mutating the original object.
        """
        replaced = replace(self, **kwargs)
        replaced._edited_by_filter = True
        return replaced


ExtractFilterFunc = Callable[[ArchiveMember, str], ArchiveMember | None]

IteratorFilterFunc = Callable[[ArchiveMember], ArchiveMember | None]


# A type that must match both ExtractFilterFunc and IteratorFilterFunc
# The callable must be able to handle both one and two arguments
class FilterFunc(Protocol):
    """A callable that takes a member and its destination path, and returns a modified
    member or `None` to skip it during extraction or iteration."""

    @overload
    def __call__(self, member: ArchiveMember) -> ArchiveMember | None: ...

    @overload
    def __call__(
        self, member: ArchiveMember, dest_path: str
    ) -> ArchiveMember | None: ...

    def __call__(
        self, member: ArchiveMember, dest_path: str | None = None
    ) -> ArchiveMember | None: ...


class ExtractionFilter(StrEnum):
    """Built-in sanitization policies for archive extraction.

    These match Python's built-in [`tarfile` named filters](https://docs.python.org/3/library/tarfile.html#default-named-filters),
    and can be used to block unsafe paths, strip permissions, or restrict file types.
    """

    FULLY_TRUSTED = "fully_trusted"
    """No filtering or restrictions. Use only with fully trusted archives."""

    TAR = "tar"
    """Blocks absolute paths and files outside destination; strips setuid/setgid/sticky bits and group/other write permissions."""

    DATA = "data"
    """Stricter than 'tar': also blocks special files and unsafe links, and removes executable bits from regular files."""


# Stream type definitions moved here to break circular import
@runtime_checkable
class ReadableBinaryStream(Protocol):
    """Protocol for a readable binary stream."""

    def read(self, n: int = -1, /) -> bytes: ...


ReadableStreamLikeOrSimilar = ReadableBinaryStream | io.IOBase | IO[bytes]
"""A readable binary stream or similar object (e.g. IO[bytes])."""
