"""Core data types for the Archivey public API."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, ClassVar, Mapping

if TYPE_CHECKING:
    from archivey.internal.cost import CostReceipt


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
    LZIP = "lz"
    ZLIB = "zz"
    BROTLI = "br"
    UNIX_COMPRESS = "Z"


@dataclass(frozen=True)
class ArchiveFormat:
    container: ContainerFormat
    stream: StreamFormat

    # Named instances, populated just after the class definition. Declared here as
    # ClassVars so both type checkers know they exist (no per-use `type: ignore`).
    ZIP: ClassVar[ArchiveFormat]
    TAR: ClassVar[ArchiveFormat]
    TAR_GZ: ClassVar[ArchiveFormat]
    TAR_BZ2: ClassVar[ArchiveFormat]
    TAR_XZ: ClassVar[ArchiveFormat]
    TAR_ZST: ClassVar[ArchiveFormat]
    TAR_LZ4: ClassVar[ArchiveFormat]
    GZ: ClassVar[ArchiveFormat]
    BZ2: ClassVar[ArchiveFormat]
    XZ: ClassVar[ArchiveFormat]
    ZST: ClassVar[ArchiveFormat]
    LZIP: ClassVar[ArchiveFormat]
    ZLIB: ClassVar[ArchiveFormat]
    BROTLI: ClassVar[ArchiveFormat]
    Z: ClassVar[ArchiveFormat]
    SEVEN_Z: ClassVar[ArchiveFormat]
    RAR: ClassVar[ArchiveFormat]
    ISO: ClassVar[ArchiveFormat]
    DIRECTORY: ClassVar[ArchiveFormat]
    UNKNOWN: ClassVar[ArchiveFormat]

    def file_extension(self) -> str:
        """The on-disk file extension for this format, without a leading dot.

        Used for extension-based naming and detection — e.g. choosing the output
        filename when converting between formats, or matching by extension in the
        detector. Examples: ``ZIP`` -> ``"zip"``, ``TAR_GZ`` -> ``"tar.gz"``,
        ``GZ`` -> ``"gz"``. Formats with no on-disk file representation
        (``DIRECTORY``, ``UNKNOWN``) return ``""``.
        """
        if self.container in (ContainerFormat.DIRECTORY, ContainerFormat.UNKNOWN):
            return ""
        if self.container == ContainerFormat.RAW_STREAM:
            # A bare single-file compressed stream (no container): the extension is
            # just the codec's own — GZ -> "gz", not "raw_stream.gz".
            return self.stream.value
        if self.stream == StreamFormat.UNCOMPRESSED:
            return self.container.value
        return f"{self.container.value}.{self.stream.value}"

    def __repr__(self) -> str:
        name = _FORMAT_NAMES.get(self)
        if name is not None:
            return f"ArchiveFormat.{name}"
        return f"ArchiveFormat({self.container!r}, {self.stream!r})"


# Predefined named instances, assigned as class attributes.
ArchiveFormat.ZIP = ArchiveFormat(ContainerFormat.ZIP, StreamFormat.UNCOMPRESSED)
ArchiveFormat.TAR = ArchiveFormat(ContainerFormat.TAR, StreamFormat.UNCOMPRESSED)
ArchiveFormat.TAR_GZ = ArchiveFormat(ContainerFormat.TAR, StreamFormat.GZIP)
ArchiveFormat.TAR_BZ2 = ArchiveFormat(ContainerFormat.TAR, StreamFormat.BZIP2)
ArchiveFormat.TAR_XZ = ArchiveFormat(ContainerFormat.TAR, StreamFormat.XZ)
ArchiveFormat.TAR_ZST = ArchiveFormat(ContainerFormat.TAR, StreamFormat.ZSTD)
ArchiveFormat.TAR_LZ4 = ArchiveFormat(ContainerFormat.TAR, StreamFormat.LZ4)
ArchiveFormat.GZ = ArchiveFormat(ContainerFormat.RAW_STREAM, StreamFormat.GZIP)
ArchiveFormat.BZ2 = ArchiveFormat(ContainerFormat.RAW_STREAM, StreamFormat.BZIP2)
ArchiveFormat.XZ = ArchiveFormat(ContainerFormat.RAW_STREAM, StreamFormat.XZ)
ArchiveFormat.ZST = ArchiveFormat(ContainerFormat.RAW_STREAM, StreamFormat.ZSTD)
ArchiveFormat.LZIP = ArchiveFormat(ContainerFormat.RAW_STREAM, StreamFormat.LZIP)
ArchiveFormat.ZLIB = ArchiveFormat(ContainerFormat.RAW_STREAM, StreamFormat.ZLIB)
ArchiveFormat.BROTLI = ArchiveFormat(ContainerFormat.RAW_STREAM, StreamFormat.BROTLI)
ArchiveFormat.Z = ArchiveFormat(ContainerFormat.RAW_STREAM, StreamFormat.UNIX_COMPRESS)
ArchiveFormat.SEVEN_Z = ArchiveFormat(ContainerFormat.SEVEN_Z, StreamFormat.UNCOMPRESSED)
ArchiveFormat.RAR = ArchiveFormat(ContainerFormat.RAR, StreamFormat.UNCOMPRESSED)
ArchiveFormat.ISO = ArchiveFormat(ContainerFormat.ISO, StreamFormat.UNCOMPRESSED)
ArchiveFormat.DIRECTORY = ArchiveFormat(ContainerFormat.DIRECTORY, StreamFormat.UNCOMPRESSED)
ArchiveFormat.UNKNOWN = ArchiveFormat(ContainerFormat.UNKNOWN, StreamFormat.UNCOMPRESSED)

# Reverse map (instance -> attribute name) for __repr__, derived by introspecting the
# class attributes above so the names live in exactly one place.
_FORMAT_NAMES: dict[ArchiveFormat, str] = {
    value: name
    for name, value in vars(ArchiveFormat).items()
    if isinstance(value, ArchiveFormat)
}


class MemberType(Enum):
    FILE = "file"
    DIRECTORY = "directory"
    SYMLINK = "symlink"
    HARDLINK = "hardlink"
    OTHER = "other"


class CompressionAlgorithm(Enum):
    """A compression/filter codec. Extensible: codecs Archivey does not recognize
    map to ``UNKNOWN`` rather than raising, so callers should treat the set as
    open-ended."""

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
    BCJ = "bcj"  # x86 executable filter
    BCJ2 = "bcj2"
    DELTA = "delta"
    UNKNOWN = "unknown"  # unrecognized codec ID


@dataclass(frozen=True)
class CompressionMethod:
    """A single codec in a member's compression chain. Members store a
    ``tuple[CompressionMethod, ...]`` to model multi-codec filter chains (e.g. a 7z
    ``(BCJ2, LZMA2)`` chain)."""

    algo: CompressionAlgorithm
    level: int | None = None  # compression level, if the format records it
    properties: bytes | None = None  # raw codec properties blob, if any


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


# Key in ArchiveMember.extra marking a member as a Windows NTFS junction. Junctions
# are a cross-format concept (ZIP, 7z and RAR can all carry them), so this key is
# deliberately NOT namespaced under a single format like "zip.".
EXTRA_IS_JUNCTION = "is_junction"


@dataclass
class ArchiveMember:
    """Represents a single archive entry. Mutable; callers must treat as read-only."""

    type: MemberType
    """What kind of entry this is (file, directory, symlink, …)."""

    name: str
    """Normalized member path, ``/``-separated, decoded for display and lookup."""

    raw_name: bytes | None = None
    """The member name exactly as stored in the archive, undecoded."""

    size: int | None = None
    """Uncompressed size in bytes, or ``None`` if unknown (e.g. a streaming entry)."""

    compressed_size: int | None = None
    """Compressed size in bytes, or ``None`` if unknown."""

    modified: datetime | None = None
    """Last-modified time, if recorded."""

    accessed: datetime | None = None
    """Last-access time, if recorded."""

    created: datetime | None = None
    """Creation time, if recorded (rare; most formats only store modification time)."""

    mode: int | None = None
    """Unix permission bits, or ``None`` if the format/entry carries no mode."""

    uid: int | None = None
    """Owner user id, if recorded."""

    gid: int | None = None
    """Owner group id, if recorded."""

    uname: str | None = None
    """Owner user name, if recorded."""

    gname: str | None = None
    """Owner group name, if recorded."""

    link_target: str | None = None
    """For a symlink/hardlink, the raw target path string as stored."""

    link_target_member: "ArchiveMember | None" = field(default=None, compare=False)
    """For a link, the resolved target member within this archive, if found."""

    compression: tuple[CompressionMethod, ...] = field(default_factory=tuple)
    """The codec chain applied to this member (outermost last)."""

    is_encrypted: bool = False
    """Whether this member's data is encrypted."""

    is_sparse: bool = False
    """Whether this member is stored as a sparse file."""

    comment: str | None = None
    """Per-member comment, if the format records one."""

    create_system: CreateSystem | None = None
    """The OS that created the entry (drives mode/attribute interpretation)."""

    windows_attrs: int | None = None
    """Raw Windows file-attribute bitmask, if recorded."""

    hashes: Mapping[str, int | bytes] = field(default_factory=dict, compare=False)
    """Stored content digests keyed by algorithm name (e.g. ``"crc32"``). Excluded from equality."""

    extra: dict[str, Any] = field(default_factory=dict, compare=False)
    """Format-specific extra fields (e.g. ``extra["is_junction"]``). Excluded from equality."""

    # Private internal fields (not part of the public contract)
    _member_id: int | None = field(default=None, repr=False, compare=False)
    _archive_id: str | None = field(default=None, repr=False, compare=False)

    # Mutable members are intentionally unhashable. Annotated `-> int` (the call
    # always raises) so the override stays compatible with object.__hash__.
    def __hash__(self) -> int:
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
        return self.type == MemberType.SYMLINK and bool(self.extra.get(EXTRA_IS_JUNCTION))

    def replace(self, **kwargs: Any) -> "ArchiveMember":
        """Return a copy with the given fields changed; never mutates self."""
        return replace(self, **kwargs)


@dataclass(frozen=True)
class ArchiveInfo:
    """Archive-level metadata, available immediately after ``open_archive()`` without
    a full member scan."""

    format: ArchiveFormat
    """The detected ``(container, stream)`` format of the archive."""

    format_version: str | None
    """Format version string, e.g. ``"4.5"`` for ZIP or ``"5"`` for RAR5; ``None`` if unknown."""

    is_solid: bool
    """Whether decompressing one member may require decompressing earlier ones."""

    member_count: int | None
    """Number of members, or ``None`` when a count would require scanning the whole archive."""

    comment: str | None
    """Archive-level comment, if the format records one."""

    is_encrypted: bool
    """Header-level encryption (7z, RAR5) — not per-member encryption (see ``ArchiveMember.is_encrypted``)."""

    is_multivolume: bool
    """Whether the archive spans multiple volumes."""

    cost: "CostReceipt"
    """Listing/access cost receipt for the archive (see the ``access-mode-and-cost`` capability)."""
