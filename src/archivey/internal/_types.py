"""Core data types for the Archivey public API."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, Mapping

if TYPE_CHECKING:
    from archivey.internal._intent import CostReceipt

logger = logging.getLogger("archivey.normalization")


class ContainerFormat(str, Enum):
    ZIP = "zip"
    TAR = "tar"
    RAR = "rar"
    SEVEN_Z = "7z"
    ISO = "iso"
    DIRECTORY = "directory"
    RAW_STREAM = "raw_stream"
    UNKNOWN = "unknown"


class StreamFormat(str, Enum):
    UNCOMPRESSED = "uncompressed"
    GZIP = "gz"
    BZIP2 = "bz2"
    XZ = "xz"
    ZSTD = "zst"
    LZ4 = "lz4"


@dataclass(frozen=True)
class ArchiveFormat:
    container: ContainerFormat
    stream: StreamFormat

    def file_extension(self) -> str:
        if self.stream == StreamFormat.UNCOMPRESSED:
            return self.container.value
        return f"{self.container.value}.{self.stream.value}"

    def __repr__(self) -> str:
        # Return the named instance name if available
        for name, val in _FORMAT_INSTANCES.items():
            if val == self:
                return f"ArchiveFormat.{name}"
        return f"ArchiveFormat({self.container!r}, {self.stream!r})"


# Registry of named format instances
_FORMAT_INSTANCES: dict[str, ArchiveFormat] = {}


def _add_format(name: str, container: ContainerFormat, stream: StreamFormat) -> ArchiveFormat:
    fmt = ArchiveFormat(container, stream)
    _FORMAT_INSTANCES[name] = fmt
    return fmt


# Predefined named instances assigned as class attributes
ArchiveFormat.ZIP = _add_format("ZIP", ContainerFormat.ZIP, StreamFormat.UNCOMPRESSED)  # type: ignore[attr-defined]
ArchiveFormat.TAR = _add_format("TAR", ContainerFormat.TAR, StreamFormat.UNCOMPRESSED)  # type: ignore[attr-defined]
ArchiveFormat.TAR_GZ = _add_format("TAR_GZ", ContainerFormat.TAR, StreamFormat.GZIP)  # type: ignore[attr-defined]
ArchiveFormat.TAR_BZ2 = _add_format("TAR_BZ2", ContainerFormat.TAR, StreamFormat.BZIP2)  # type: ignore[attr-defined]
ArchiveFormat.TAR_XZ = _add_format("TAR_XZ", ContainerFormat.TAR, StreamFormat.XZ)  # type: ignore[attr-defined]
ArchiveFormat.TAR_ZST = _add_format("TAR_ZST", ContainerFormat.TAR, StreamFormat.ZSTD)  # type: ignore[attr-defined]
ArchiveFormat.TAR_LZ4 = _add_format("TAR_LZ4", ContainerFormat.TAR, StreamFormat.LZ4)  # type: ignore[attr-defined]
ArchiveFormat.GZ = _add_format("GZ", ContainerFormat.RAW_STREAM, StreamFormat.GZIP)  # type: ignore[attr-defined]
ArchiveFormat.BZ2 = _add_format("BZ2", ContainerFormat.RAW_STREAM, StreamFormat.BZIP2)  # type: ignore[attr-defined]
ArchiveFormat.XZ = _add_format("XZ", ContainerFormat.RAW_STREAM, StreamFormat.XZ)  # type: ignore[attr-defined]
ArchiveFormat.ZST = _add_format("ZST", ContainerFormat.RAW_STREAM, StreamFormat.ZSTD)  # type: ignore[attr-defined]
ArchiveFormat.SEVEN_Z = _add_format("SEVEN_Z", ContainerFormat.SEVEN_Z, StreamFormat.UNCOMPRESSED)  # type: ignore[attr-defined]
ArchiveFormat.RAR = _add_format("RAR", ContainerFormat.RAR, StreamFormat.UNCOMPRESSED)  # type: ignore[attr-defined]
ArchiveFormat.ISO = _add_format("ISO", ContainerFormat.ISO, StreamFormat.UNCOMPRESSED)  # type: ignore[attr-defined]
ArchiveFormat.DIRECTORY = _add_format("DIRECTORY", ContainerFormat.DIRECTORY, StreamFormat.UNCOMPRESSED)  # type: ignore[attr-defined]
ArchiveFormat.UNKNOWN = _add_format("UNKNOWN", ContainerFormat.UNKNOWN, StreamFormat.UNCOMPRESSED)  # type: ignore[attr-defined]


class MemberType(Enum):
    FILE = "file"
    DIRECTORY = "directory"
    SYMLINK = "symlink"
    HARDLINK = "hardlink"
    OTHER = "other"


class CompressionAlgo(Enum):
    STORED = "stored"
    DEFLATE = "deflate"
    DEFLATE64 = "deflate64"
    BZIP2 = "bzip2"
    LZMA = "lzma"
    LZMA2 = "lzma2"
    ZSTD = "zstd"
    LZ4 = "lz4"
    BROTLI = "brotli"
    PPMD = "ppmd"
    BCJ = "bcj"
    BCJ2 = "bcj2"
    DELTA = "delta"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class CompressionMethod:
    algo: CompressionAlgo
    level: int | None = None
    properties: bytes | None = None


class CreateSystem(Enum):
    """OS that created the archive entry (mirrors ZIP create_system values)."""

    FAT = 0
    AMIGA = 1
    OPENVMS = 2
    UNIX = 3
    VM_CMS = 4
    ATARI_ST = 5
    OS2_HPFS = 6
    MACINTOSH = 7
    Z_SYSTEM = 8
    CPM = 9
    WINDOWS_NTFS = 10
    MVS = 11
    VSE = 12
    ACORN_RISC = 13
    VFAT = 14
    ALTERNATE_MVS = 15
    BEOS = 16
    TANDEM = 17
    OS_400 = 18
    OS_X_DARWIN = 19
    UNKNOWN = 255


def _normalize_name(
    raw_bytes: bytes | None,
    decoded: str,
    member_type: MemberType,
    encoding: str = "utf-8",
) -> str:
    """Normalize archive member name per spec rules. Emits warning if name changed."""
    name = decoded

    # 1. Replace backslashes with forward slashes
    name = name.replace("\\", "/")

    # 2. Strip leading / and ./
    while name.startswith("/") or name.startswith("./"):
        if name.startswith("/"):
            name = name[1:]
        elif name.startswith("./"):
            name = name[2:]

    # 3. Collapse // and foo/../bar sequences
    parts = name.split("/")
    normalized_parts: list[str] = []
    for part in parts:
        if part == "..":
            if normalized_parts:
                normalized_parts.pop()
        elif part in (".", ""):
            pass
        else:
            normalized_parts.append(part)

    name = "/".join(normalized_parts)

    # 4. Append / for directory members
    if member_type == MemberType.DIRECTORY and not name.endswith("/"):
        name = name + "/"

    # 5. Never produce empty string - root dir becomes "."
    if not name or name == "/":
        name = "."

    if name != decoded:
        logger.warning("Member name normalized: %r -> %r", decoded, name)

    return name


@dataclass
class ArchiveMember:
    """Represents a single archive entry. Mutable; callers must treat as read-only."""

    # Type
    type: MemberType

    # Identity
    name: str
    raw_name: bytes | None = None

    # Sizes
    size: int | None = None
    compressed_size: int | None = None

    # Timestamps
    modified: datetime | None = None
    accessed: datetime | None = None
    created: datetime | None = None

    # Permissions & ownership
    mode: int | None = None
    uid: int | None = None
    gid: int | None = None
    uname: str | None = None
    gname: str | None = None

    # Link semantics
    link_target: str | None = None
    link_target_member: "ArchiveMember | None" = field(default=None, compare=False)

    # Compression
    compression: tuple[CompressionMethod, ...] = field(default_factory=tuple)

    # Flags
    is_encrypted: bool = False
    is_sparse: bool = False

    # Provenance
    comment: str | None = None
    create_system: CreateSystem | None = None
    windows_attrs: int | None = None

    # Integrity - excluded from __eq__
    hashes: Mapping[str, int | bytes] = field(default_factory=dict, compare=False)

    # Format-specific - excluded from __eq__
    extra: dict[str, Any] = field(default_factory=dict, compare=False)

    # Private internal fields (not part of the public contract)
    _member_id: int | None = field(default=None, repr=False, compare=False)
    _archive_id: str | None = field(default=None, repr=False, compare=False)

    def __hash__(self) -> None:  # type: ignore[override]
        raise TypeError(f"unhashable type: '{type(self).__name__}'")

    @property
    def member_id(self) -> int:
        if self._member_id is None:
            raise AttributeError("member_id not set; member not yet registered")
        return self._member_id

    @property
    def archive_id(self) -> str:
        if self._archive_id is None:
            raise AttributeError("archive_id not set; member not yet registered")
        return self._archive_id

    @property
    def is_file(self) -> bool:
        return self.type == MemberType.FILE

    @property
    def is_dir(self) -> bool:
        return self.type == MemberType.DIRECTORY

    @property
    def is_link(self) -> bool:
        return self.type in (MemberType.SYMLINK, MemberType.HARDLINK)

    @property
    def is_other(self) -> bool:
        return self.type == MemberType.OTHER

    @property
    def is_junction(self) -> bool:
        return self.type == MemberType.SYMLINK and bool(self.extra.get("zip.is_junction"))

    def replace(self, **kwargs: Any) -> "ArchiveMember":
        """Return a copy with the given fields changed; never mutates self."""
        from dataclasses import replace as dc_replace

        return dc_replace(self, **kwargs)


@dataclass(frozen=True)
class ArchiveInfo:
    """Archive-level metadata."""

    format: ArchiveFormat
    format_version: str | None
    is_solid: bool
    member_count: int | None
    comment: str | None
    is_encrypted: bool
    is_multivolume: bool
    cost: "CostReceipt"
