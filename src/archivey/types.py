"""Core data types for the Archivey public API."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone, tzinfo
from enum import Enum, Flag, auto
from typing import TYPE_CHECKING, Any, ClassVar, Mapping, NamedTuple

if TYPE_CHECKING:
    from archivey.cost import CostReceipt
    from archivey.diagnostics import Diagnostic


class MemberStreams(Flag):
    """Opt-in member-stream capabilities for :func:`archivey.open_archive`.

    Default (no bits set — ``MemberStreams(0)``) is the cheap contract:

    - at most one live member stream at a time
    - streams are forward-only (``seek()`` raises)

    Combine flags with ``|``::

        MemberStreams.CONCURRENT | MemberStreams.SEEKABLE

    ``CONCURRENT``
        Multiple overlapping ``open()`` calls are allowed. First-touch member
        materialization is coordinated (one build; waiters share the snapshot);
        ``close()`` drains in-flight worker calls. Callers still synchronize any
        *shared* stream objects they hand around. Reader-wide passes
        (``__iter__`` / ``stream_members`` / ``extract_all``) remain single-owner.
        Does **not** remove solid open-order cost — see :class:`~archivey.AccessCost`.

    ``SEEKABLE``
        Member streams support ``seek()`` where the backend can position
        (often via an index or accelerator). Without this flag, seek raises.
    """

    CONCURRENT = auto()
    SEEKABLE = auto()


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
    LZMA_ALONE = "lzma"  # legacy LZMA Alone file format (not raw FORMAT_RAW)
    ZLIB = "zz"
    BROTLI = "br"
    UNIX_COMPRESS = "Z"


@dataclass(frozen=True)
class ArchiveFormat:
    """A ``(container, stream)`` pair identifying how an archive is packaged.

    Prefer the named class attributes (``ArchiveFormat.ZIP``, ``ArchiveFormat.TAR_GZ``,
    …) over constructing pairs by hand. Those names are assigned immediately below
    the class body; the ``ClassVar`` declarations exist so type checkers see them
    without per-use suppressions. ``_FORMAT_NAMES`` is built from the same
    assignments so ``repr`` / ``display_name`` stay in sync automatically.
    """

    container: ContainerFormat
    stream: StreamFormat

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
    LZ4: ClassVar[ArchiveFormat]
    LZIP: ClassVar[ArchiveFormat]
    LZMA_ALONE: ClassVar[ArchiveFormat]
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

    @property
    def display_name(self) -> str:
        """Human-readable name for this format, e.g. ``"ZIP"``, ``"TAR_GZ"``.

        Uses the predefined named-instance attribute name (``ZIP``, ``TAR_GZ``, …);
        falls back to ``repr()`` for an ad-hoc combination not in the named set.
        ``_FORMAT_NAMES`` is populated just after the class definition — safe at
        runtime because this property is never called before the module is fully loaded.
        """
        name = _FORMAT_NAMES.get(self)
        return name if name is not None else repr(self)

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
ArchiveFormat.LZ4 = ArchiveFormat(ContainerFormat.RAW_STREAM, StreamFormat.LZ4)
ArchiveFormat.LZIP = ArchiveFormat(ContainerFormat.RAW_STREAM, StreamFormat.LZIP)
ArchiveFormat.LZMA_ALONE = ArchiveFormat(
    ContainerFormat.RAW_STREAM, StreamFormat.LZMA_ALONE
)
ArchiveFormat.ZLIB = ArchiveFormat(ContainerFormat.RAW_STREAM, StreamFormat.ZLIB)
ArchiveFormat.BROTLI = ArchiveFormat(ContainerFormat.RAW_STREAM, StreamFormat.BROTLI)
ArchiveFormat.Z = ArchiveFormat(ContainerFormat.RAW_STREAM, StreamFormat.UNIX_COMPRESS)
ArchiveFormat.SEVEN_Z = ArchiveFormat(
    ContainerFormat.SEVEN_Z, StreamFormat.UNCOMPRESSED
)
ArchiveFormat.RAR = ArchiveFormat(ContainerFormat.RAR, StreamFormat.UNCOMPRESSED)
ArchiveFormat.ISO = ArchiveFormat(ContainerFormat.ISO, StreamFormat.UNCOMPRESSED)
ArchiveFormat.DIRECTORY = ArchiveFormat(
    ContainerFormat.DIRECTORY, StreamFormat.UNCOMPRESSED
)
ArchiveFormat.UNKNOWN = ArchiveFormat(
    ContainerFormat.UNKNOWN, StreamFormat.UNCOMPRESSED
)

# Reverse map (instance -> attribute name) for __repr__, derived by introspecting the
# class attributes above so the names live in exactly one place.
_FORMAT_NAMES: dict[ArchiveFormat, str] = {
    value: name
    for name, value in vars(ArchiveFormat).items()
    if isinstance(value, ArchiveFormat)
}


@dataclass(frozen=True)
class MissingComponent:
    """A package, extra, or external tool required for a format (or a codec inside it).

    Appears on :class:`~archivey.FormatAvailability` when something is absent, and as
    the ``requirement`` on internal codec/backend descriptors. Defined in this leaf
    module (not the registry) so codec descriptors can reference it without a
    registry ↔ codecs import cycle.
    """

    name: str  # e.g. "pycdlib", "[7z]", "unrar"
    install_hint: str  # e.g. "pip install archivey[iso]"
    unlocks: tuple[
        str, ...
    ] = ()  # member-codecs/capabilities it enables, e.g. ("ppmd",)


class MagicSignature(NamedTuple):
    """Exact magic-byte match declared by a backend/codec descriptor (not end-user API).

    Detection accepts a match on the byte comparison alone. Formats too unspecific
    for an exact magic (zlib's 2-byte CMF/FLG header) or with no signature (Brotli)
    use a content probe instead — see codec ``content_probe`` and ``format-detection``.
    """

    offset: int
    magic: bytes
    format: "ArchiveFormat"


class MemberType(Enum):
    """Kind of archive entry.

    ``ANTI`` is a deletion/tombstone (solid 7z incremental updates), not a payload
    file — ``is_file`` is false and extraction skips it. ``OTHER`` covers device
    nodes, FIFOs, sockets, etc., and is always rejected by safe extraction.
    """

    FILE = "file"
    DIRECTORY = "directory"
    SYMLINK = "symlink"
    HARDLINK = "hardlink"
    OTHER = "other"
    ANTI = "anti"


class HashAlgorithm(str, Enum):
    """Digest algorithms that may appear as keys in :attr:`ArchiveMember.hashes`."""

    CRC32 = "crc32"
    BLAKE2SP = "blake2sp"
    ADLER32 = "adler32"


def crc32_digest(value: int) -> bytes:
    """Encode a CRC-32 as four big-endian bytes for :attr:`ArchiveMember.hashes`."""
    return (value & 0xFFFFFFFF).to_bytes(4, "big")


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
    """One codec in a member's filter chain.

    Members store ``tuple[CompressionMethod, ...]``. Order matches the compress /
    pack direction: pre-filters first, packing codec last (closest to the stored
    bytes). Example: 7z ``(BCJ2, LZMA2)`` — decompress by applying LZMA2, then BCJ2.
    """

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


@dataclass(slots=True)
class ArchiveMember:
    """One archive entry.

    Mutable on purpose: backends fill late-bound fields in place after the member
    is first constructed (``link_target_member``, digests, attached diagnostics).
    Callers must treat instances as read-only — use :meth:`replace` for edits.
    """

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

    # compare=False: identity is path/type/metadata, not the resolved peer object
    # (resolution is late-bound and would make equality order-dependent).
    link_target_member: "ArchiveMember | None" = field(default=None, compare=False)
    """For a link, the resolved target member within this archive, if found."""

    compression: tuple[CompressionMethod, ...] = field(default_factory=tuple)
    """Codec chain in compress order — pre-filters first, packing codec last."""

    is_encrypted: bool = False
    """Whether this member's data is encrypted."""

    is_current: bool = True
    """Last-entry-wins: ``True`` for the live final state of this path.

    Duplicate names keep earlier rows with ``is_current=False`` (history /
    superseded). :meth:`~archivey.ArchiveReader.get` returns the current one.
    """

    is_sparse: bool = False
    """Whether this member is stored as a sparse file."""

    comment: str | None = None
    """Per-member comment, if the format records one."""

    create_system: CreateSystem | None = None
    """The OS that created the entry (drives mode/attribute interpretation)."""

    windows_attrs: int | None = None
    """Raw Windows file-attribute bitmask, if recorded."""

    # compare=False: digests may be filled after first construction; equality is
    # about the entry identity, not verification state (see archive-data-model).
    hashes: Mapping[HashAlgorithm, bytes] = field(default_factory=dict, compare=False)
    """Stored content digests keyed by :class:`HashAlgorithm` (values always ``bytes``).

    CRC-32 is four big-endian bytes (:func:`crc32_digest`). Excluded from equality.
    """

    # compare=False: format-specific bags must not affect logical identity.
    extra: dict[str, Any] = field(default_factory=dict, compare=False)
    """Format-specific extra fields (e.g. ``extra["is_junction"]``). Excluded from equality."""

    # Private internal fields (not part of the public contract)
    _member_id: int | None = field(default=None, repr=False, compare=False)
    _archive_id: str | None = field(default=None, repr=False, compare=False)
    _raw: Any = field(default=None, repr=False, compare=False)
    """Opaque backend handle carried on the member (e.g. the stdlib ``ZipInfo`` /
    ``TarInfo``), so a backend can open the member's data straight from the member without
    a separate name/id lookup table. Not part of the public contract."""
    _diagnostics: tuple["Diagnostic", ...] = field(
        default=(), repr=False, compare=False
    )
    """Library-retained diagnostic attachments (bounded by the collector budget)."""

    # Mutable members are intentionally unhashable. Annotated `-> int` (the call
    # always raises) so the override stays compatible with object.__hash__.
    def __hash__(self) -> int:
        raise TypeError(f"unhashable type: '{type(self).__name__}'")

    @property
    def diagnostics(self) -> tuple["Diagnostic", ...]:
        """Read-only tuple of diagnostics attached to this member (may be empty)."""
        return self._diagnostics

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

    def modified_utc(self, tz_for_naive: tzinfo | None = None) -> datetime | None:
        """The modification time as a timezone-aware UTC ``datetime``, or ``None``.

        ``modified`` itself is faithful to what the archive stores: **naive** when the
        format records local wall-clock time (ZIP's DOS field, RAR4), **aware** when it
        records UTC or an offset — so naive and aware values from one archive cannot be
        compared or sorted directly. This helper makes that usable: an aware value is
        converted to UTC; a naive one first gets ``tz_for_naive`` attached (the caller's
        explicit assumption about where the archive was created), defaulting to the
        local timezone when not given. Whether the stored value was wall-clock remains
        visible on the field itself: ``member.modified.tzinfo is None``.
        """
        dt = self.modified
        if dt is None:
            return None
        if dt.tzinfo is None:
            if tz_for_naive is not None:
                dt = dt.replace(tzinfo=tz_for_naive)
            else:
                dt = dt.astimezone()  # naive -> assume local timezone
        return dt.astimezone(timezone.utc)

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
    def is_anti(self) -> bool:
        return self.type == MemberType.ANTI

    @property
    def is_junction(self) -> bool:
        return self.type == MemberType.SYMLINK and bool(
            self.extra.get(EXTRA_IS_JUNCTION)
        )

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

    extra: dict[str, Any] = field(default_factory=dict, compare=False)
    """Format-specific archive-level metadata, keyed by namespaced strings (mirrors
    ``ArchiveMember.extra``). For example the ISO backend records the auto-selected
    namespace as ``extra["iso.namespace"]``. Excluded from ``__eq__``."""
