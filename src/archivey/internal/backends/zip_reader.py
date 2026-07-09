"""ZIP backend on the v2 ABC, backed by the stdlib ``zipfile`` module.

The central directory is read on open, giving O(1) member listing (``INDEXED``) and
direct random access (``DIRECT``) to any member. A non-seekable source fails fast (the
central directory lives at EOF), and split/spanned multi-volume sets are rejected with a
clear "rejoin first" error (see ``format-zip``).
"""

from __future__ import annotations

import io
import lzma
import re
import stat
import struct
import zipfile
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Iterator, Mapping, cast

from archivey.config import ArchiveyConfig
from archivey.cost import (
    AccessCost,
    CostReceipt,
    ListingCost,
    StreamCapability,
)
from archivey.exceptions import (
    ArchiveyError,
    CorruptionError,
    EncryptionError,
    StreamNotSeekableError,
    UnsupportedFeatureError,
)
from archivey.internal.base_reader import BaseArchiveReader, ReadBackend
from archivey.internal.logs import backends as logger
from archivey.internal.naming import normalize_member_name
from archivey.internal.password import _PasswordCandidates
from archivey.internal.registry import register_reader
from archivey.internal.streams.streamtools import is_seekable, is_stream
from archivey.types import (
    ArchiveFormat,
    ArchiveInfo,
    ArchiveMember,
    CompressionAlgorithm,
    CompressionMethod,
    CreateSystem,
    MagicSignature,
    MemberType,
)

# Comment decoding: try UTF-8 first, else fall back to cp437 (the ZIP appnote default,
# which maps every byte and therefore never fails — no further fallbacks are reachable).
_ZIP_ENCODINGS = ("utf-8", "cp437")

# A ".z01"/".z42"/… segment name — the obvious signal of a split (multi-volume) ZIP set.
_SPLIT_SEGMENT_RE = re.compile(r"\.z\d{2}$", re.IGNORECASE)

# ZIP compression-method id -> our codec algorithm. Unknown ids map to UNKNOWN rather than
# raising, matching the open-ended CompressionAlgorithm contract.
_ZIP_COMPRESSION_ALGOS: dict[int, CompressionAlgorithm] = {
    0: CompressionAlgorithm.STORED,
    8: CompressionAlgorithm.DEFLATE,
    9: CompressionAlgorithm.DEFLATE64,
    12: CompressionAlgorithm.BZIP2,
    14: CompressionAlgorithm.LZMA,
    93: CompressionAlgorithm.ZSTD,
    98: CompressionAlgorithm.PPMD,
}

# ZIP create-system values whose entries use "\" as a path separator (DOS/Windows family).
# For these, a stored backslash is a separator; for Unix/other entries it is a literal
# filename character (see the minimal-name-normalization change / archive-data-model spec).
_BACKSLASH_SEPARATOR_SYSTEMS: frozenset[CreateSystem] = frozenset(
    {
        CreateSystem.FAT,
        CreateSystem.OS2_HPFS,
        CreateSystem.WINDOWS_NTFS,
        CreateSystem.VFAT,
    }
)


def _decode_with_fallback(data: bytes) -> str:
    for encoding in _ZIP_ENCODINGS:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise AssertionError("unreachable: cp437 decodes every byte")


# Seconds between the NTFS FILETIME epoch (1601-01-01) and the Unix epoch (1970-01-01).
_NTFS_EPOCH_OFFSET = 11_644_473_600


def _filetime_to_datetime(value: int, filename: str) -> datetime | None:
    """An NTFS FILETIME (100 ns ticks since 1601, UTC) as a datetime; 0 means "not set"."""
    if value == 0:
        return None
    try:
        return datetime.fromtimestamp(
            value / 10_000_000 - _NTFS_EPOCH_OFFSET, tz=timezone.utc
        )
    except (ValueError, OverflowError, OSError):
        logger.warning("Invalid NTFS timestamp for %r: %r", filename, value)
        return None


def _zip_timestamps(
    info: zipfile.ZipInfo,
) -> tuple[datetime | None, datetime | None, datetime | None]:
    """Return ``(modified, accessed, created)`` for a member.

    Sources, lowest to highest precedence (each layer overrides only the times it
    actually carries):

    1. The DOS ``date_time``: a 2-second-granularity local wall-clock modification time
       (naive ``datetime``; ``None`` for the "no timestamp" sentinel year 1980).
    2. An NTFS extra field (0x000A): three 64-bit FILETIMEs (modification, access,
       creation) in 100 ns UTC ticks since 1601; zero means "not set". Written by
       Windows tools (e.g. 7-Zip).
    3. An Extended Timestamp extra field (0x5455): real Unix timestamps, its flags byte
       signaling which of modification/access/creation follow (in that order), each a
       signed 32-bit Unix time interpreted as UTC. The central directory typically
       carries only the modification time even when the flags advertise more.
    """
    if info.date_time == (1980, 0, 0, 0, 0, 0):
        modified: datetime | None = None
    else:
        try:
            modified = datetime(*info.date_time)
        except ValueError:
            logger.warning("Invalid ZIP date_time for %r: %r", info.filename, info.date_time)
            modified = None
    accessed: datetime | None = None
    created: datetime | None = None

    # One scan collecting both timestamp extra fields, applied afterwards in precedence
    # order (NTFS below Extended Timestamp) regardless of their order in the blob.
    ntfs_field: bytes | None = None
    ut_field: bytes | None = None
    extra = info.extra or b""
    pos = 0
    while pos + 4 <= len(extra):
        tag, length = struct.unpack("<HH", extra[pos : pos + 4])
        field = extra[pos + 4 : pos + 4 + length]
        if tag == 0x000A and ntfs_field is None:
            ntfs_field = field
        elif tag == 0x5455 and field and ut_field is None:
            ut_field = field
        pos += 4 + length

    if ntfs_field is not None:
        # Layout: 4 reserved bytes, then (tag, size) attributes; tag 1 carries the three
        # FILETIMEs. Malformed/short fields are skipped rather than failing the listing.
        cursor = 4
        while cursor + 4 <= len(ntfs_field):
            attr_tag, attr_size = struct.unpack_from("<HH", ntfs_field, cursor)
            cursor += 4
            if attr_tag == 0x0001 and attr_size >= 24 and cursor + 24 <= len(ntfs_field):
                mtime, atime, ctime = struct.unpack_from("<QQQ", ntfs_field, cursor)
                modified = _filetime_to_datetime(mtime, info.filename) or modified
                accessed = _filetime_to_datetime(atime, info.filename) or accessed
                created = _filetime_to_datetime(ctime, info.filename) or created
                break
            cursor += attr_size

    if ut_field is not None:
        flags = ut_field[0]
        cursor = 1
        for bit in (0x01, 0x02, 0x04):  # mtime, atime, ctime, in order
            if flags & bit and cursor + 4 <= len(ut_field):
                ts = int.from_bytes(ut_field[cursor : cursor + 4], "little", signed=True)
                when = datetime.fromtimestamp(ts, tz=timezone.utc)
                if bit == 0x01:
                    modified = when
                elif bit == 0x02:
                    accessed = when
                else:
                    created = when
                cursor += 4

    return modified, accessed, created


class ZipReader(BaseArchiveReader):
    """Reads a ZIP archive via stdlib ``zipfile``."""

    _SUPPORTS_RANDOM_ACCESS = True
    _MEMBER_LIST_UPFRONT = True

    def __init__(
        self,
        source: Path | BinaryIO,
        streaming: bool,
        passwords: _PasswordCandidates | None,
        encoding: str | None,
        archive_name: str | None,
        config: ArchiveyConfig,
    ) -> None:
        super().__init__(ArchiveFormat.ZIP, streaming, archive_name, config)
        self._source = source
        self._passwords = passwords or _PasswordCandidates()
        self._encoding = encoding

        if is_stream(source) and not is_seekable(source):
            raise StreamNotSeekableError(
                "ZIP archives cannot be read from a non-seekable source: the central "
                "directory lives at the end of the file.",
                archive_name=archive_name,
            )

        # A split-set segment (name.z01, name.z42, …) cannot be read by stdlib zipfile.
        if archive_name and _SPLIT_SEGMENT_RE.search(archive_name):
            raise UnsupportedFeatureError(
                "Multi-volume (split/spanned) ZIP archives are not supported.",
                archive_name=archive_name,
                source_format=ArchiveFormat.ZIP,
            )

        try:
            # `metadata_encoding` (3.11+) decodes names stored without the UTF-8 flag with
            # the caller's encoding instead of the cp437 default (UTF-8-flagged names are
            # unaffected). Reading the central directory here decodes every member name.
            # typeshed types ZipFile too narrowly; a binary stream is valid here.
            self._archive: zipfile.ZipFile = zipfile.ZipFile(  # type: ignore[arg-type]
                source, "r", metadata_encoding=encoding
            )
        except zipfile.BadZipFile as exc:
            if _looks_like_multivolume(exc):
                raise UnsupportedFeatureError(
                    "This ZIP spans multiple disks/volumes, which is not supported.",
                    archive_name=archive_name,
                    source_format=ArchiveFormat.ZIP,
                ) from exc
            raise CorruptionError(
                f"Could not open ZIP archive: {exc!r}",
                archive_name=archive_name,
                source_format=ArchiveFormat.ZIP,
            ) from exc
        except UnicodeDecodeError as exc:
            # A member name failed to decode while reading the central directory: either a
            # UTF-8-flagged entry whose stored bytes are corrupt, or a wrong explicit
            # `encoding=`. Both are surfaced as a typed error (never a raw UnicodeDecodeError);
            # the message points at the encoding when the caller supplied one.
            hint = (
                f" (with encoding={encoding!r}; the stored bytes may use a different encoding)"
                if encoding is not None
                else ""
            )
            raise CorruptionError(
                f"Could not decode a ZIP member name{hint}: {exc!r}",
                archive_name=archive_name,
                source_format=ArchiveFormat.ZIP,
            ) from exc
        except NotImplementedError as exc:
            # zipfile rejects an unsupported "version needed to extract" (e.g. a mutated
            # "zip file version 8.4") while reading the central directory. Recognized but
            # unhandled -> UnsupportedFeatureError, not a raw NotImplementedError.
            raise UnsupportedFeatureError(
                f"Unsupported ZIP version or feature: {exc!r}",
                archive_name=archive_name,
                source_format=ArchiveFormat.ZIP,
            ) from exc

    def _translate_exception(self, exc: Exception) -> ArchiveyError | None:
        if isinstance(exc, zipfile.BadZipFile):
            return CorruptionError(f"Error reading ZIP archive: {exc!r}")
        if isinstance(exc, RuntimeError):
            text = str(exc).lower()
            if "password required" in text:
                return EncryptionError("Password required to read this ZIP member")
            if "bad password" in text:
                return EncryptionError("Wrong password for this ZIP member")
        if isinstance(exc, io.UnsupportedOperation) and "seek" in str(exc):
            return StreamNotSeekableError("ZIP archives require a seekable source")
        if isinstance(exc, NotImplementedError):
            # zipfile raises NotImplementedError for a compress_type / flag combination it
            # cannot decode — an unsupported *method* ("compression method 99"), but also a
            # corrupt entry whose mutated flags select an unimplemented mode ("compressed
            # patched data (flag bit 5)"). Either way the member is unreadable here.
            return UnsupportedFeatureError(f"Unsupported ZIP entry feature: {exc!r}")
        if isinstance(exc, (zlib.error, lzma.LZMAError)):
            # Corruption inside a member body: stdlib zipfile surfaces the codec's own error
            # (zlib.error "invalid distance too far back", lzma.LZMAError "Corrupt input
            # data") rather than BadZipFile for a deflate/bzip2/LZMA member.
            return CorruptionError(f"Error decompressing ZIP member: {exc!r}")
        if isinstance(exc, ValueError):
            # A corrupt local-header offset makes stdlib zipfile seek to a bad position
            # ("negative seek value -N") before reading the member. That is archive
            # corruption, surfaced as a typed error rather than a raw ValueError.
            return CorruptionError(f"Corrupt ZIP member offset/structure: {exc!r}")
        if isinstance(exc, OSError) and "Invalid data stream" in str(exc):
            # The stdlib bz2 decompressor signals a corrupt bzip2 member body as
            # OSError("Invalid data stream") (a bz2 quirk). Message-scoped so a genuine I/O
            # OSError still propagates unchanged (error-handling: I/O is not reclassified).
            return CorruptionError(f"Corrupt bzip2 ZIP member: {exc!r}")
        if isinstance(exc, UnicodeDecodeError):
            # zipfile re-reads and re-decodes the member name from the local file header
            # when opening a member; a corrupt local header with non-UTF-8 name bytes
            # raises this. It is a bad-archive signal, not a caller/runtime error.
            return CorruptionError(f"Corrupt ZIP entry name in local header: {exc!r}")
        return None

    def _iter_members(self) -> Iterator[ArchiveMember]:
        for info in self._archive.infolist():
            yield self._to_member(info)

    def _to_member(self, info: zipfile.ZipInfo) -> ArchiveMember:
        full_mode = info.external_attr >> 16
        is_unix = info.create_system == 3
        # Permission bits only; None when no usable Unix mode was stored.
        mode = stat.S_IMODE(full_mode) if (info.external_attr != 0 and is_unix) else None

        if info.is_dir():
            member_type = MemberType.DIRECTORY
        elif is_unix and stat.S_ISLNK(full_mode):
            member_type = MemberType.SYMLINK
        else:
            member_type = MemberType.FILE

        try:
            create_system = CreateSystem(info.create_system)
        except ValueError:
            create_system = CreateSystem.UNKNOWN
        # Convert "\" to "/" only for DOS/Windows-origin entries (where it is a separator);
        # a Unix (or other) entry keeps a backslash as a literal filename character.
        backslash_is_separator = create_system in _BACKSLASH_SEPARATOR_SYSTEMS

        # Use orig_filename, not filename: stdlib zipfile rewrites filename in a
        # platform-dependent way (it replaces os.sep -> "/" on Windows and truncates at a
        # null byte), whereas orig_filename is the raw decoded name, identical on every OS.
        # Archivey's own backslash_is_separator / extraction checks are the single authority.
        decoded = info.orig_filename
        name = normalize_member_name(
            decoded, member_type, backslash_is_separator=backslash_is_separator
        )
        # raw_name recovers the stored bytes by re-encoding the SAME source as name
        # (decoded == orig_filename) with the codec zipfile decoded with: UTF-8 when the
        # entry's UTF-8 flag is set, else the caller's metadata encoding (when given) or
        # zipfile's cp437 default. Using orig_filename keeps name and raw_name consistent.
        raw_name = decoded.encode(
            "utf-8" if info.flag_bits & 0x800 else (self._encoding or "cp437"),
            errors="surrogateescape",
        )

        algo = _ZIP_COMPRESSION_ALGOS.get(info.compress_type, CompressionAlgorithm.UNKNOWN)

        modified, accessed, created = _zip_timestamps(info)
        return ArchiveMember(
            type=member_type,
            name=name,
            raw_name=raw_name,
            size=info.file_size,
            compressed_size=info.compress_size,
            modified=modified,
            accessed=accessed,
            created=created,
            mode=mode,
            compression=(CompressionMethod(algo=algo),),
            is_encrypted=bool(info.flag_bits & 0x1),
            comment=_decode_with_fallback(info.comment) if info.comment else None,
            create_system=create_system,
            extra={"zip.compress_type": info.compress_type},
            _raw=info,  # carry the ZipInfo so _open_member needs no name/id lookup table
        )

    def _open_zip_entry(
        self,
        info: zipfile.ZipInfo,
        member: ArchiveMember | None,
        *,
        member_name: str,
    ) -> BinaryIO:
        encrypted = bool(info.flag_bits & 0x1)

        def decrypt(password: bytes) -> BinaryIO:
            try:
                return cast(
                    "BinaryIO",
                    self._archive.open(info, pwd=password if encrypted else None),
                )
            except (
                zipfile.BadZipFile,
                RuntimeError,
                io.UnsupportedOperation,
                NotImplementedError,
                zlib.error,
                lzma.LZMAError,
                UnicodeDecodeError,
                ValueError,
            ) as exc:
                translated = self._translate_exception(exc)
                if translated is not None:
                    if isinstance(translated, EncryptionError):
                        raise translated
                    self._stamp_error_context(translated, member_name)
                    raise translated from exc
                raise

        if encrypted:
            try:
                return self._passwords.attempt(member, decrypt)
            except EncryptionError as exc:
                self._stamp_error_context(exc, member_name)
                raise
        return decrypt(b"")

    def _ensure_link_target(self, member: ArchiveMember) -> None:
        if member.type != MemberType.SYMLINK or member.link_target is not None:
            return
        info = member._raw
        assert isinstance(info, zipfile.ZipInfo), "ZIP member is missing its ZipInfo handle"
        # A symlink's target is its (possibly encrypted) file data. Listing must stay
        # usable without a password, so a missing/wrong password leaves link_target
        # unset (following the link later fails with LinkTargetNotFoundError); other
        # errors surface translated like any member-read error.
        try:
            with self._open_zip_entry(info, member, member_name=member.name) as f:
                member.link_target = f.read().decode("utf-8", errors="surrogateescape")
        except EncryptionError:
            logger.warning(
                "Cannot read the symlink target of %r without the correct "
                "password; leaving link_target unset.",
                info.filename,
            )
        except (
            zipfile.BadZipFile,
            RuntimeError,
            zlib.error,
            lzma.LZMAError,
            OSError,
            ValueError,
            NotImplementedError,
            UnicodeDecodeError,
        ) as exc:
            # Reading the symlink's target data (raw zipfile stream, not ArchiveStream-wrapped)
            # can raise any of the member-read errors on a corrupt entry; translate them the
            # same way rather than letting a raw codec exception escape the listing.
            translated = self._translate_exception(exc)
            if translated is not None:
                self._stamp_error_context(translated, info.filename)
                raise translated from exc
            raise

    def _open_member(self, member: ArchiveMember) -> BinaryIO:
        # The member carries its own ZipInfo (`_raw`), so data access needs no name/id map
        # — and a duplicate member name can't resolve to the wrong entry.
        info = member._raw
        assert isinstance(info, zipfile.ZipInfo), "ZIP member is missing its ZipInfo handle"
        raw = self._open_zip_entry(info, member, member_name=member.name)
        return self._wrap_member_stream(raw, member.name, size=member.size)

    def _get_archive_info(self) -> ArchiveInfo:
        comment = self._archive.comment
        cost = CostReceipt(
            listing_cost=ListingCost.INDEXED,
            access_cost=AccessCost.DIRECT,
            stream_capability=StreamCapability.SEEKABLE,
            solid_block_count=None,
        )
        return ArchiveInfo(
            format=ArchiveFormat.ZIP,
            format_version=None,
            is_solid=False,  # ZIP is never solid: each member has an independent offset
            member_count=len(self._archive.infolist()),
            comment=_decode_with_fallback(comment) if comment else None,
            is_encrypted=False,  # ZIP has per-member encryption, not header-level
            is_multivolume=False,
            cost=cost,
        )

    def _close_archive(self) -> None:
        self._archive.close()


def _looks_like_multivolume(exc: zipfile.BadZipFile) -> bool:
    text = str(exc).lower()
    return "multi" in text or "disk" in text or "spanned" in text or "split" in text


class ZipReadBackend(ReadBackend):
    """Backend factory for ZIP archives."""

    FORMATS: tuple[ArchiveFormat, ...] = (ArchiveFormat.ZIP,)
    EXTENSIONS: Mapping[str, ArchiveFormat] = {".zip": ArchiveFormat.ZIP}
    MAGIC: tuple[MagicSignature, ...] = (
        MagicSignature(0, b"\x50\x4b\x03\x04", ArchiveFormat.ZIP),  # standard local header
        MagicSignature(0, b"\x50\x4b\x05\x06", ArchiveFormat.ZIP),  # empty archive (EOCD)
        MagicSignature(0, b"\x50\x4b\x07\x08", ArchiveFormat.ZIP),  # spanned marker
    )
    # SUPPORTS_STREAMING_NON_SEEKABLE stays False: the central directory lives at EOF,
    # so even a forward-only pass needs a seekable source.
    SUPPORTS_PASSWORD = True  # per-member ZipCrypto/AES encryption

    def open_read(
        self,
        source: Path | BinaryIO,
        format: ArchiveFormat,
        streaming: bool,
        passwords: _PasswordCandidates | None,
        encoding: str | None,
        archive_name: str | None,
        config: ArchiveyConfig,
    ) -> ZipReader:
        # `format` is always ZIP here (single-format backend); accepted for the uniform
        # ReadBackend signature.
        return ZipReader(source, streaming, passwords, encoding, archive_name, config)


register_reader(ZipReadBackend)
