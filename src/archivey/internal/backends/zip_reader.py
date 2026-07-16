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
import threading
import zipfile
import zlib
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO, Iterator, Mapping, NoReturn, cast

from archivey.config import ArchiveyConfig
from archivey.cost import (
    AccessCost,
    CostReceipt,
    ListingCost,
    StreamCapability,
)
from archivey.diagnostics import (
    DiagnosticCode,
    MemberTimestampContext,
    NameEncodingContext,
    SymlinkTargetContext,
    raw_name_to_base64,
)
from archivey.exceptions import (
    ArchiveyError,
    CorruptionError,
    EncryptionError,
    PackageNotInstalledError,
    StreamNotSeekableError,
    TruncatedError,
    UnsupportedFeatureError,
)
from archivey.internal.base_reader import BaseArchiveReader, ReadBackend
from archivey.internal.config import stream_config_from_archivey
from archivey.internal.diagnostics_collector import DiagnosticCollector
from archivey.internal.logs import backends as logger
from archivey.internal.naming import emit_member_name_normalized, normalize_member_name
from archivey.internal.open_site import OpenSite
from archivey.internal.password import (
    _PasswordCandidates,
    _PasswordCandidatesExhausted,
)
from archivey.internal.password_confirm import (
    CONFIRM_PREFIX_BYTES,
    first_crc_match,
)
from archivey.internal.registry import register_reader
from archivey.internal.streams.archive_stream import ArchiveStream
from archivey.internal.streams.codecs import (
    Codec,
    CodecParams,
    open_codec_stream,
)
from archivey.internal.streams.streamtools import (
    CloseLockedStream,
    SlicingStream,
    is_seekable,
    is_stream,
    read_exact,
)
from archivey.internal.streams.verify import VerifyingStream
from archivey.internal.timestamps import TimestampIssue, filetime_to_datetime
from archivey.internal.zip_aes import (
    open_winzip_aes_member,
    parse_winzip_aes_extra,
)
from archivey.internal.zipcrypto import (
    parallel_plaintext_crc32,
    password_matches_check_byte,
)
from archivey.types import (
    ArchiveFormat,
    ArchiveInfo,
    ArchiveMember,
    CompressionAlgorithm,
    CompressionMethod,
    CreateSystem,
    MagicSignature,
    MemberStreams,
    MemberType,
)

# Comment decoding: try UTF-8 first, else fall back to cp437 (the ZIP appnote default,
# which maps every byte and therefore never fails — no further fallbacks are reachable).
_ZIP_ENCODINGS = ("utf-8", "cp437")

# bz2 uses OSError for this decoder-specific failure. Match the complete message so an
# unrelated filesystem/source OSError remains a genuine I/O error and propagates unchanged.
_BZIP2_INVALID_DATA = "Invalid data stream"

# ZIP general-purpose bit 3: data descriptor follows the member; verification byte is
# then the high byte of the DOS time rather than of the CRC-32.
_ZIP_MASK_USE_DATA_DESCRIPTOR = 0x8

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

# ZIP method id -> shared codec-layer Codec for unencrypted member decode.
_ZIP_METHOD_CODECS: dict[int, Codec] = {
    0: Codec.STORED,
    8: Codec.DEFLATE,
    9: Codec.DEFLATE64,
    12: Codec.BZIP2,
    14: Codec.LZMA,  # after peeling the ZIP LZMA header (see _open_codec_member)
    93: Codec.ZSTD,
    98: Codec.PPMD,  # after peeling the ZIP PPMd8 header
}

# Local-file-header name/extra lengths are uint16; reject values that would push the
# data region past a sane absolute offset (same absurd-length discipline as native parsers).
_MAX_LOCAL_NAME_EXTRA = 65_535
_MAX_DATA_OFFSET = 1 << 40

# stdlib exposes no public decoder for a raw LZMA1 property blob → filter dict; zipfile and
# the 7z reader rely on the same private helper.
_raw_decode_filter_properties = getattr(lzma, "_decode_filter_properties", None)
if _raw_decode_filter_properties is None:  # pragma: no cover
    raise ImportError(
        "This Python's `lzma` module no longer exposes `_decode_filter_properties`, which "
        "archivey needs to decode ZIP LZMA (method 14) member properties. "
        "Please report this to archivey (with your Python version)."
    )
_decode_filter_properties: Callable[[int, bytes], dict] = _raw_decode_filter_properties

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


# Raw exceptions a ZIP member open/read can raise that _translate_exception maps to typed
# ArchiveyErrors. Declared once so the catch sites (member open, compressed-confirm decrypt,
# symlink-target read) cannot drift apart — they previously did (one omitted
# io.UnsupportedOperation), exactly the bug this constant prevents.
_ZIP_MEMBER_READ_ERRORS: tuple[type[Exception], ...] = (
    zipfile.BadZipFile,
    RuntimeError,
    io.UnsupportedOperation,
    NotImplementedError,
    zlib.error,
    lzma.LZMAError,
    UnicodeDecodeError,
    ValueError,
    OSError,
    # stdlib zipfile raises bare EOFError when a member's local data is truncated
    # mid-read (e.g. a corrupt symlink target during listing). Must be translated
    # like the other member-read errors — otherwise it escapes as a raw exception.
    EOFError,
)


def _decode_with_fallback(data: bytes) -> str:
    for encoding in _ZIP_ENCODINGS:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise AssertionError("unreachable: cp437 decodes every byte")


def _is_candidate_integrity_failure(exc: Exception) -> bool:
    """Whether ``exc`` can be caused by a ZipCrypto verification-byte collision."""
    if isinstance(exc, zipfile.BadZipFile):
        # A wrong ZipCrypto candidate can produce a CRC mismatch after decrypting bytes,
        # but cannot alter the unencrypted local header or archive structure.
        return str(exc).startswith("Bad CRC-32 for file ")
    return isinstance(exc, (zlib.error, lzma.LZMAError)) or (
        isinstance(exc, OSError) and str(exc) == _BZIP2_INVALID_DATA
    )


# NTFS FILETIME conversion + the shared TimestampIssue type live in internal.timestamps
# (also used by the native 7z reader, and RAR later). Local aliases keep this module's
# call sites — including the DOS date_time issue below — unchanged.
_TimestampIssue = TimestampIssue
_filetime_to_datetime = filetime_to_datetime


def _zip_timestamps(
    info: zipfile.ZipInfo,
) -> tuple[datetime | None, datetime | None, datetime | None, list[_TimestampIssue]]:
    """Return ``(modified, accessed, created, issues)`` for a member.

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
    issues: list[_TimestampIssue] = []
    if info.date_time == (1980, 0, 0, 0, 0, 0):
        modified: datetime | None = None
    else:
        try:
            modified = datetime(*info.date_time)
        except ValueError:
            issues.append(
                _TimestampIssue(
                    field="date_time",
                    source="dos",
                    value_repr=repr(info.date_time),
                    message=(
                        f"Invalid ZIP date_time for {info.filename!r}: "
                        f"{info.date_time!r}"
                    ),
                )
            )
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
            if (
                attr_tag == 0x0001
                and attr_size >= 24
                and cursor + 24 <= len(ntfs_field)
            ):
                mtime, atime, ctime = struct.unpack_from("<QQQ", ntfs_field, cursor)
                for value, field_name in (
                    (mtime, "mtime"),
                    (atime, "atime"),
                    (ctime, "ctime"),
                ):
                    dt, issue = _filetime_to_datetime(
                        value, info.filename, field=field_name
                    )
                    if issue is not None:
                        issues.append(issue)
                    if dt is None:
                        continue
                    if field_name == "mtime":
                        modified = dt
                    elif field_name == "atime":
                        accessed = dt
                    else:
                        created = dt
                break
            cursor += attr_size

    if ut_field is not None:
        flags = ut_field[0]
        cursor = 1
        for bit, ut_name in ((0x01, "mtime"), (0x02, "atime"), (0x04, "ctime")):
            if flags & bit and cursor + 4 <= len(ut_field):
                ts = int.from_bytes(
                    ut_field[cursor : cursor + 4], "little", signed=True
                )
                cursor += 4
                try:
                    when = datetime.fromtimestamp(ts, tz=timezone.utc)
                except (ValueError, OverflowError, OSError):
                    # Same out-of-range guard as the DOS/NTFS fields above: on Windows
                    # even tz-aware fromtimestamp routes through gmtime(), which raises
                    # OSError for pre-1970 values — a signed field an archive (hostile
                    # or merely old) can legitimately carry. Degrade to an issue, never
                    # sink the listing with a raw platform error.
                    issues.append(
                        _TimestampIssue(
                            field=ut_name,
                            source="extended",
                            value_repr=repr(ts),
                            message=(
                                f"Invalid ZIP extended timestamp for "
                                f"{info.filename!r}: {ts!r}"
                            ),
                        )
                    )
                    continue
                if bit == 0x01:
                    modified = when
                elif bit == 0x02:
                    accessed = when
                else:
                    created = when

    return modified, accessed, created, issues


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
        collector: DiagnosticCollector | None = None,
        member_streams: MemberStreams = MemberStreams(0),
        open_site: OpenSite | None = None,
    ) -> None:
        super().__init__(
            ArchiveFormat.ZIP,
            streaming,
            archive_name,
            config,
            collector=collector,
            member_streams=member_streams,
            open_site=open_site,
        )
        self._source = source
        self._passwords = passwords or _PasswordCandidates()
        self._encoding = encoding
        self._stream_config = stream_config_from_archivey(
            self._config,
            streaming=streaming,
            seekable=MemberStreams.SEEKABLE in member_streams,
        )
        # Free-threaded ZIP: stdlib zipfile races on _fileRefCnt across concurrent
        # ZipFile.open / ZipExtFile.close / ZipFile.close. Serialize those under
        # CONCURRENT; leave reads to zipfile's own _SharedFile lock so independent
        # members can still decompress in parallel. The unencrypted codec path uses
        # the same ZipFile._lock via locked SlicingStream views.
        self._handle_lock: threading.Lock | None = (
            threading.Lock() if MemberStreams.CONCURRENT in member_streams else None
        )
        # When measurement is on we open a Path ourselves to install a seek counter;
        # ZipFile does not close a caller-supplied file object, so we own it.
        self._owned_fp: BinaryIO | None = None

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

        zip_source: Path | BinaryIO = source
        if self._measure:
            if isinstance(source, Path):
                self._owned_fp = open(source, "rb")
                zip_source = self._track_source_seeks(self._owned_fp)
            else:
                zip_source = self._track_source_seeks(source)

        try:
            # `metadata_encoding` (3.11+) decodes names stored without the UTF-8 flag with
            # the caller's encoding instead of the cp437 default (UTF-8-flagged names are
            # unaffected). Reading the central directory here decodes every member name.
            # typeshed types ZipFile too narrowly; a binary stream is valid here.
            self._archive: zipfile.ZipFile = zipfile.ZipFile(  # type: ignore[arg-type]
                zip_source, "r", metadata_encoding=encoding
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
        if isinstance(exc, OSError) and str(exc) == _BZIP2_INVALID_DATA:
            # The stdlib bz2 decompressor signals a corrupt bzip2 member body as
            # OSError("Invalid data stream") (a bz2 quirk). Message-scoped so a genuine I/O
            # OSError still propagates unchanged (error-handling: I/O is not reclassified).
            return CorruptionError(f"Corrupt bzip2 ZIP member: {exc!r}")
        if isinstance(exc, UnicodeDecodeError):
            # zipfile re-reads and re-decodes the member name from the local file header
            # when opening a member; a corrupt local header with non-UTF-8 name bytes
            # raises this. It is a bad-archive signal, not a caller/runtime error.
            return CorruptionError(f"Corrupt ZIP entry name in local header: {exc!r}")
        if isinstance(exc, EOFError):
            # Truncated member body (stdlib zipfile._ZipDecrypter / ZipExtFile._read2).
            return TruncatedError(f"Truncated ZIP member data: {exc!r}")
        return None

    def _iter_members(self) -> Iterator[ArchiveMember]:
        for info in self._archive.infolist():
            yield self._to_member(info)

    def _sniff_unflagged_name(
        self, raw_name: bytes, cp437_decoded: str
    ) -> tuple[str, str | None]:
        """Decode an unflagged ZIP name (no explicit ``encoding=``): prefer valid UTF-8, else
        the configured legacy fallback (default cp437).

        Returns ``(name, inferred_encoding)`` where ``inferred_encoding`` is the encoding used
        only when it overrode the cp437 APPNOTE default (for the diagnostic), else ``None``.
        UTF-8 is self-validating, so a clean decode is strong evidence the bytes are UTF-8;
        legacy bytes that are coincidentally valid UTF-8 are the documented residual risk.
        """
        try:
            return raw_name.decode("utf-8"), "utf-8"
        except UnicodeDecodeError:
            fallback = self._config.zip_unflagged_fallback_encoding
            if fallback.lower().replace("-", "").replace("_", "") in {
                "cp437",
                "437",
                "ibm437",
            }:
                return cp437_decoded, None
            try:
                return raw_name.decode(fallback, errors="surrogateescape"), fallback
            except LookupError:
                # An unknown fallback encoding name: keep the cp437 decode rather than fail.
                return cp437_decoded, None

    def _to_member(self, info: zipfile.ZipInfo) -> ArchiveMember:
        full_mode = info.external_attr >> 16
        is_unix = info.create_system == 3
        # Permission bits only; None when no usable Unix mode was stored.
        mode = (
            stat.S_IMODE(full_mode) if (info.external_attr != 0 and is_unix) else None
        )

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
        is_utf8_flagged = bool(info.flag_bits & 0x800)
        # raw_name recovers the stored bytes by re-encoding the SAME source as name
        # (decoded == orig_filename) with the codec zipfile decoded with: UTF-8 when the
        # entry's UTF-8 flag is set, else the caller's metadata encoding (when given) or
        # zipfile's cp437 default. Using orig_filename keeps name and raw_name consistent.
        raw_name = decoded.encode(
            "utf-8" if is_utf8_flagged else (self._encoding or "cp437"),
            errors="surrogateescape",
        )
        # Many tools write UTF-8 names without setting the UTF-8 flag (APPNOTE says cp437),
        # so cp437 would yield mojibake. With no authoritative signal (flag clear AND no
        # explicit encoding=), prefer UTF-8 when the stored bytes are valid UTF-8, else a
        # configurable legacy fallback. A set flag or explicit encoding= is honored as-is.
        name_source = decoded
        inferred_encoding: str | None = None
        if not is_utf8_flagged and self._encoding is None:
            name_source, inferred_encoding = self._sniff_unflagged_name(
                raw_name, decoded
            )
        name = normalize_member_name(
            name_source, member_type, backslash_is_separator=backslash_is_separator
        )

        algo = _ZIP_COMPRESSION_ALGOS.get(
            info.compress_type, CompressionAlgorithm.UNKNOWN
        )
        aes_info = (
            parse_winzip_aes_extra(info.extra) if info.compress_type == 99 else None
        )
        if aes_info is not None:
            # Method 99 is a wrapper; surface the underlying compression algorithm.
            algo = _ZIP_COMPRESSION_ALGOS.get(
                aes_info.actual_method, CompressionAlgorithm.UNKNOWN
            )

        modified, accessed, created, ts_issues = _zip_timestamps(info)
        # Surface the central-directory CRC-32 as a stored digest (archive-data-model spec:
        # "ZIP CRC32 … stored under the 'crc32' int key"), so a dedupe pass can key on it
        # without decompressing (VISION "hashes without decompression"). Only for FILE and
        # SYMLINK members, which have data: a directory's stored CRC is a meaningless 0.
        # AE-2 stores CRC as 0 and relies on the HMAC — do not surface a fake crc32.
        hashes: dict[str, int] = {}
        if member_type in (MemberType.FILE, MemberType.SYMLINK):
            if aes_info is None or not aes_info.is_ae2:
                hashes = {"crc32": info.CRC & 0xFFFFFFFF}
        extra: dict[str, object] = {"zip.compress_type": info.compress_type}
        if aes_info is not None:
            extra["zip.aes_vendor_version"] = aes_info.vendor_version
            extra["zip.aes_strength"] = aes_info.strength
            extra["zip.aes_actual_method"] = aes_info.actual_method
        member = ArchiveMember(
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
            hashes=hashes,
            extra=extra,
            _raw=info,  # carry the ZipInfo so _open_member needs no name/id lookup table
        )
        if inferred_encoding is not None:
            self._diagnostics_collector.emit(
                code=DiagnosticCode.MEMBER_NAME_ENCODING_INFERRED,
                message=(
                    f"ZIP member name decoded as {inferred_encoding!r} rather than the "
                    f"cp437 default (UTF-8 flag not set): {member.name!r}"
                ),
                context=NameEncodingContext(
                    archive_name=self._archive_name,
                    member_name=member.name,
                    member_id=member._member_id,
                    raw_name_base64=raw_name_to_base64(member.raw_name),
                    inferred_encoding=inferred_encoding,
                    declared_encoding="cp437",
                ),
                member=member,
                attach_to_member=True,
                logger=logger,
            )
        emit_member_name_normalized(
            self._diagnostics_collector,
            member=member,
            presented_name=decoded,
            archive_name=self._archive_name,
        )
        for issue in ts_issues:
            self._diagnostics_collector.emit(
                code=DiagnosticCode.MEMBER_TIMESTAMP_INVALID,
                message=issue.message,
                context=MemberTimestampContext(
                    archive_name=self._archive_name,
                    member_name=member.name,
                    member_id=member._member_id,
                    field=issue.field,
                    source=issue.source,
                    value_repr=issue.value_repr,
                ),
                member=member,
                attach_to_member=True,
                logger=logger,
            )
        return member

    def _zipcrypto_check_byte(self, info: zipfile.ZipInfo) -> int:
        if info.flag_bits & _ZIP_MASK_USE_DATA_DESCRIPTOR:
            # zipfile stores the DOS time in the private ``_raw_time`` attribute and uses
            # its high byte as the ZipCrypto check byte when a data descriptor is present.
            # Fail LOUD if a future Python drops the attribute: a silent 0 fallback would
            # make every candidate fail the 1-byte check, misreporting correct passwords
            # as wrong for data-descriptor members (same policy as the loud import-time
            # bind of lzma._decode_filter_properties in the 7z reader).
            raw_time = getattr(info, "_raw_time", None)
            if raw_time is None:
                raise RuntimeError(
                    "This Python's `zipfile` no longer exposes `ZipInfo._raw_time`, "
                    "which archivey needs to verify ZipCrypto passwords for "
                    "data-descriptor members. Please report this to archivey "
                    "(with your Python version)."
                )
            return (int(raw_time) >> 8) & 0xFF
        return (info.CRC >> 24) & 0xFF

    def _zipfile_lock(self) -> Any:
        # stdlib ZipFile serializes fp access via a private lock; typeshed omits it.
        return getattr(self._archive, "_lock")

    @contextmanager
    def _ciphertext_body_stream(
        self,
        info: zipfile.ZipInfo,
    ) -> Iterator[BinaryIO]:
        """Yield a :class:`SlicingStream` over the ZipCrypto ciphertext body.

        The view starts after the 12-byte encryption header and covers the rest of the
        member's compressed payload. Held under ``ZipFile``'s lock with the archive
        position restored on exit; never buffers the whole member.
        """
        zf = self._archive
        header_len = 12
        body_len = max(0, info.compress_size - header_len)

        with self._zipfile_lock():
            fp = zf.fp
            if fp is None:
                raise ValueError("Attempt to use ZIP archive that was already closed")
            saved = fp.tell()
            try:
                fp.seek(info.header_offset)
                fheader = fp.read(30)
                if len(fheader) != 30 or fheader[:4] != b"PK\x03\x04":
                    raise zipfile.BadZipFile("Bad magic number for file header")
                name_len, extra_len = struct.unpack_from("<HH", fheader, 26)
                body_start = info.header_offset + 30 + name_len + extra_len + header_len
                yield SlicingStream(
                    cast("BinaryIO", fp), start=body_start, length=body_len
                )
            finally:
                fp.seek(saved)

    def _read_zipcrypto_header(self, info: zipfile.ZipInfo) -> bytes:
        """Return the 12-byte ZipCrypto header ciphertext for ``info``."""
        zf = self._archive
        with self._zipfile_lock():
            fp = zf.fp
            if fp is None:
                raise ValueError("Attempt to use ZIP archive that was already closed")
            saved = fp.tell()
            try:
                fp.seek(info.header_offset)
                fheader = fp.read(30)
                if len(fheader) != 30 or fheader[:4] != b"PK\x03\x04":
                    raise zipfile.BadZipFile("Bad magic number for file header")
                name_len, extra_len = struct.unpack_from("<HH", fheader, 26)
                fp.read(name_len + extra_len)
                header = fp.read(12)
                if len(header) != 12:
                    raise zipfile.BadZipFile("Truncated ZipCrypto header")
                return header
            finally:
                fp.seek(saved)

    def _open_aes_member(
        self,
        info: zipfile.ZipInfo,
        member: ArchiveMember | None,
        *,
        member_name: str,
    ) -> ArchiveStream:
        """Decrypt a WinZip AE (method 99) member and decode via the codec layer."""
        aes = parse_winzip_aes_extra(info.extra)
        if aes is None:
            raise UnsupportedFeatureError(
                "ZIP compression method 99 without a valid WinZip AES (0x9901) extra field",
                archive_name=self._archive_name,
                member_name=member_name,
                source_format=ArchiveFormat.ZIP,
            )
        codec = _ZIP_METHOD_CODECS.get(aes.actual_method)
        if codec is None:
            raise UnsupportedFeatureError(
                f"Unsupported ZIP compression method {aes.actual_method} under WinZip AES",
                archive_name=self._archive_name,
                member_name=member_name,
                source_format=ArchiveFormat.ZIP,
            )

        def decrypt(password: bytes) -> BinaryIO:
            raw = self._raw_member_stream(info)
            decrypted = open_winzip_aes_member(
                raw,
                aes=aes,
                password=password,
                compress_size=info.compress_size,
            )
            params = CodecParams()
            body: BinaryIO = decrypted
            if aes.actual_method == 14:
                params = self._zip_lzma_params(body)
            elif aes.actual_method == 98:
                params = self._zip_ppmd_params(body)
            return open_codec_stream(
                codec,
                body,
                config=replace(
                    self._stream_config,
                    expected_decompressed_size=(
                        member.size if member is not None else info.file_size
                    ),
                ),
                params=params,
                seekable=self._stream_config.seekable,
                collector=self._diagnostics_collector,
            )

        try:
            decoded: BinaryIO = self._finish_password_attempt(
                member, member_name, decrypt, ambiguous_holder=None
            )
        except PackageNotInstalledError:
            raise
        except _ZIP_MEMBER_READ_ERRORS as exc:
            self._reraise_member_error(exc, member_name)

        hashes = member.hashes if member is not None else {}
        size = member.size if member is not None else info.file_size
        if hashes or size is not None:
            decoded = VerifyingStream(
                decoded,
                hashes,
                expected_size=size,
                collector=self._diagnostics_collector,
                member=member,
                archive_name=self._archive_name,
            )
        return self._wrap_member_stream(decoded, member_name, size=size)

    def _open_zip_entry(
        self,
        info: zipfile.ZipInfo,
        member: ArchiveMember | None,
        *,
        member_name: str,
    ) -> BinaryIO:
        if info.compress_type == 99:
            return self._open_aes_member(info, member, member_name=member_name)

        encrypted = bool(info.flag_bits & 0x1)
        if not encrypted:
            # Unencrypted members decode through the shared codec layer (not ZipExtFile).
            return self._open_codec_member(info, member, member_name=member_name)

        # ZipCrypto's one-byte open check admits ~1/256 of wrong passwords. With more
        # than one possible candidate (or a provider), confirm before accepting.
        # Confirmed winners are re-opened fresh — no plaintext retained.
        if not self._passwords.is_ambiguous():
            return self._open_encrypted_lazy(info, member, member_name=member_name)

        if info.compress_type == zipfile.ZIP_STORED:
            return self._open_stored_confirmed(info, member, member_name=member_name)
        return self._open_compressed_confirmed(info, member, member_name=member_name)

    def _local_data_region(self, info: zipfile.ZipInfo) -> tuple[int, int]:
        """Return ``(data_start, compress_size)`` for ``info`` from its local file header.

        Parses only the fixed 30-byte local header plus the local name/extra lengths
        (central-directory extra can differ). Rejects truncated/bad magic headers and
        absurd name/extra lengths that would push the data offset past a sane bound.
        """
        zf = self._archive
        with self._zipfile_lock():
            fp = zf.fp
            if fp is None:
                raise ValueError("Attempt to use ZIP archive that was already closed")
            saved = fp.tell()
            try:
                fp.seek(info.header_offset)
                fheader = fp.read(30)
                if len(fheader) != 30 or fheader[:4] != b"PK\x03\x04":
                    raise zipfile.BadZipFile("Bad magic number for file header")
                name_len, extra_len = struct.unpack_from("<HH", fheader, 26)
                if (
                    name_len > _MAX_LOCAL_NAME_EXTRA
                    or extra_len > _MAX_LOCAL_NAME_EXTRA
                ):
                    raise zipfile.BadZipFile(
                        f"Absurd local-header name/extra lengths: {name_len}/{extra_len}"
                    )
                # General-purpose flag bit 11: UTF-8 filename (APPNOTE).
                gp_flags = struct.unpack_from("<H", fheader, 6)[0]
                local_name = fp.read(name_len)
                if len(local_name) != name_len:
                    raise zipfile.BadZipFile("Truncated file header")
                if gp_flags & 0x800:
                    fname_str = local_name.decode("utf-8")
                else:
                    fname_str = local_name.decode(self._encoding or "cp437")
                if fname_str != info.orig_filename:
                    raise zipfile.BadZipFile(
                        "File name in directory %r and header %r differ."
                        % (info.orig_filename, local_name)
                    )
                data_start = info.header_offset + 30 + name_len + extra_len
                if data_start < 0 or data_start > _MAX_DATA_OFFSET:
                    raise zipfile.BadZipFile(
                        f"Absurd local-header data offset: {data_start}"
                    )
                # Mirror stdlib zipfile's overlap guard (ZipFile.open): a member whose
                # compressed payload extends past the next entry's start is a zip bomb.
                end_offset = getattr(info, "_end_offset", None)
                if (
                    end_offset is not None
                    and data_start + max(0, info.compress_size) > end_offset
                ):
                    raise zipfile.BadZipFile(
                        f"Overlapped entries: {info.orig_filename!r} (possible zip bomb)"
                    )
                return data_start, max(0, info.compress_size)
            finally:
                fp.seek(saved)

    def _raw_member_stream(self, info: zipfile.ZipInfo) -> BinaryIO:
        """Locked :class:`SlicingStream` over the member's raw compressed payload."""
        data_start, length = self._local_data_region(info)
        fp = self._archive.fp
        if fp is None:
            raise ValueError("Attempt to use ZIP archive that was already closed")

        def _check_open() -> None:
            if self._archive.fp is None:
                raise ValueError("Attempt to use ZIP archive that was already closed")

        return SlicingStream(
            cast("BinaryIO", fp),
            start=data_start,
            length=length,
            lock=self._zipfile_lock(),
            check_open=_check_open,
        )

    def _zip_lzma_params(self, raw: BinaryIO) -> CodecParams:
        """Peel the ZIP method-14 LZMA header and return RAW LZMA1 :class:`CodecParams`."""
        # version (2) + properties size (2) + properties
        header = read_exact(raw, 4)
        if len(header) != 4:
            raise TruncatedError(
                "Truncated ZIP LZMA header",
                archive_name=self._archive_name,
                source_format=ArchiveFormat.ZIP,
            )
        props_size = struct.unpack_from("<H", header, 2)[0]
        if props_size > 256:
            raise CorruptionError(
                f"Absurd ZIP LZMA properties size: {props_size}",
                archive_name=self._archive_name,
                source_format=ArchiveFormat.ZIP,
            )
        props = read_exact(raw, props_size)
        if len(props) != props_size:
            raise TruncatedError(
                "Truncated ZIP LZMA properties",
                archive_name=self._archive_name,
                source_format=ArchiveFormat.ZIP,
            )
        filters = [_decode_filter_properties(lzma.FILTER_LZMA1, props)]
        return CodecParams(filters=filters)

    def _zip_ppmd_params(self, raw: BinaryIO) -> CodecParams:
        """Peel the ZIP method-98 2-byte PPMd8 header into :class:`CodecParams`."""
        header = read_exact(raw, 2)
        if len(header) != 2:
            raise TruncatedError(
                "Truncated ZIP PPMd header",
                archive_name=self._archive_name,
                source_format=ArchiveFormat.ZIP,
            )
        word = struct.unpack("<H", header)[0]
        order = (word & 0xF) + 1
        mem_mb = ((word >> 4) & 0xFF) + 1
        restore = (word >> 12) & 0xF
        if order < 2 or order > 64 or mem_mb < 1:
            raise CorruptionError(
                f"Invalid ZIP PPMd header parameters: order={order} mem_mb={mem_mb}",
                archive_name=self._archive_name,
                source_format=ArchiveFormat.ZIP,
            )
        return CodecParams(
            ppmd_order=order,
            ppmd_mem_size=mem_mb * 1024 * 1024,
            ppmd_restore_method=restore,
        )

    def _open_codec_member(
        self,
        info: zipfile.ZipInfo,
        member: ArchiveMember | None,
        *,
        member_name: str,
    ) -> ArchiveStream:
        """Decode an unencrypted ZIP member through the shared codec layer."""
        codec = _ZIP_METHOD_CODECS.get(info.compress_type)
        if codec is None:
            raise UnsupportedFeatureError(
                f"Unsupported ZIP compression method {info.compress_type}",
                archive_name=self._archive_name,
                member_name=member_name,
                source_format=ArchiveFormat.ZIP,
            )

        try:
            raw = self._raw_member_stream(info)
            params = CodecParams()
            if info.compress_type == 14:  # ZIP LZMA
                params = self._zip_lzma_params(raw)
            elif info.compress_type == 98:  # ZIP PPMd8
                params = self._zip_ppmd_params(raw)
                # Bound PPMd decode to the member size when known (defensive; PPMd8
                # usually has an end mark, but max_length still matches py7zr practice).
                size = member.size if member is not None else info.file_size
                if size is not None and size >= 0:
                    params = replace(params, unpack_size=size)

            decoded: BinaryIO = open_codec_stream(
                codec,
                raw,
                config=replace(
                    self._stream_config,
                    expected_decompressed_size=(
                        member.size if member is not None else info.file_size
                    ),
                ),
                params=params,
                seekable=self._stream_config.seekable,
                collector=self._diagnostics_collector,
            )
        except PackageNotInstalledError:
            raise
        except _ZIP_MEMBER_READ_ERRORS as exc:
            self._reraise_member_error(exc, member_name)

        hashes = member.hashes if member is not None else {}
        size = member.size if member is not None else info.file_size
        if hashes or size is not None:
            decoded = VerifyingStream(
                decoded,
                hashes,
                expected_size=size,
                collector=self._diagnostics_collector,
                member=member,
                archive_name=self._archive_name,
            )
        return self._wrap_member_stream(decoded, member_name, size=size)

    def _open_zipfile_member(
        self,
        info: zipfile.ZipInfo,
        *,
        password: bytes | None,
        member_name: str,
    ) -> BinaryIO:
        """Open via ``zipfile`` and translate member-open failures."""
        try:
            raw = self._zip_open_raw(info, password=password)
            if self._handle_lock is not None:
                return CloseLockedStream(raw, self._handle_lock)
            return raw
        except _ZIP_MEMBER_READ_ERRORS as exc:
            self._reraise_member_error(exc, member_name)

    def _reraise_member_error(self, exc: Exception, member_name: str) -> NoReturn:
        """Translate a raw member-read error, stamp it with member context, and raise.

        Thin wrapper over the shared base boundary: an ``EncryptionError`` is raised
        without member stamping (it carries its own message and must not be
        reclassified). Shared by the member-open and compressed-confirm decrypt paths
        so their translate/stamp/raise tail stays identical.
        """
        self._raise_translated(exc, member_name, stamp_encryption=False)

    def _zip_open_raw(
        self, info: zipfile.ZipInfo, *, password: bytes | None
    ) -> BinaryIO:
        """``ZipFile.open`` under the CONCURRENT handle lock when present."""
        with self._handle_guard():
            return cast("BinaryIO", self._archive.open(info, pwd=password))

    def _zip_close_raw(self, stream: BinaryIO) -> None:
        """Close a raw zip member stream under the CONCURRENT handle lock when present."""
        with self._handle_guard():
            stream.close()

    def _open_encrypted_lazy(
        self,
        info: zipfile.ZipInfo,
        member: ArchiveMember | None,
        *,
        member_name: str,
    ) -> BinaryIO:
        def decrypt(password: bytes) -> BinaryIO:
            return self._open_zipfile_member(
                info, password=password, member_name=member_name
            )

        return self._finish_password_attempt(
            member, member_name, decrypt, ambiguous_holder=None
        )

    def _open_compressed_confirmed(
        self,
        info: zipfile.ZipInfo,
        member: ArchiveMember | None,
        *,
        member_name: str,
    ) -> BinaryIO:
        ambiguous_holder: list[EncryptionError] = []

        def decrypt(password: bytes) -> BinaryIO:
            stream: BinaryIO | None = None
            try:
                stream = self._zip_open_raw(info, password=password)
                read_exact(stream, CONFIRM_PREFIX_BYTES)
                self._zip_close_raw(stream)
                stream = None
                # Fresh stream for the caller; zipfile re-checks CRC at EOF.
                return self._open_zipfile_member(
                    info, password=password, member_name=member_name
                )
            except _ZIP_MEMBER_READ_ERRORS as exc:
                if _is_candidate_integrity_failure(exc):
                    failure = EncryptionError(
                        f"Password candidate failed integrity validation for ZIP "
                        f"member {member_name!r}"
                    )
                    if not ambiguous_holder:
                        ambiguous_holder.append(failure)
                    raise failure from exc
                self._reraise_member_error(exc, member_name)
            finally:
                if stream is not None:
                    self._zip_close_raw(stream)

        return self._finish_password_attempt(
            member,
            member_name,
            decrypt,
            ambiguous_holder=ambiguous_holder,
        )

    def _open_stored_confirmed(
        self,
        info: zipfile.ZipInfo,
        member: ArchiveMember | None,
        *,
        member_name: str,
    ) -> BinaryIO:
        """Resolve the password for a STORED ZipCrypto member, in four phases.

        A STORED member has no decompressor to reject a wrong key, and ZipCrypto's 1-byte
        header check admits ~1/256 of wrong passwords, so a full CRC pass over the member's
        plaintext is the only way to disambiguate. To keep that pass single and bounded:

        1. **Collect survivors** — run every static candidate through the cheap 1-byte
           check; keep the ~1/256 that pass.
        2. **Disambiguate** — one shared ciphertext pass computes every survivor's plaintext
           CRC-32 in constant memory; the first CRC match wins.
        3. **Provider fallback** — if no static candidate won, ask the provider one password
           at a time (cheap check, then a per-candidate CRC pass), until one wins or it stops.
        4. **Resolve outcome** — a winner is re-opened fresh; otherwise raise the most
           specific error (integrity-ambiguous / password-required / wrong-password).
        """
        ambiguous_failure: EncryptionError | None = None
        check_byte = self._zipcrypto_check_byte(info)
        expected_crc = info.CRC & 0xFFFFFFFF

        with self._translated_errors(member_name):
            header = self._read_zipcrypto_header(info)

        def weak_ok(password: bytes) -> bool:
            return password_matches_check_byte(password, header, check_byte)

        def disambiguate(survivors: list[bytes]) -> bytes | None:
            nonlocal ambiguous_failure
            if not survivors:
                return None
            # No decompressor to reject garbage: one shared ciphertext pass computes
            # every survivor's plaintext CRC-32 in constant memory.
            with self._ciphertext_body_stream(info) as body:
                crcs = parallel_plaintext_crc32(survivors, header, body)
            winner = first_crc_match(expected_crc, crcs)
            if winner is None:
                failure = EncryptionError(
                    f"Password candidate failed integrity validation for ZIP "
                    f"member {member_name!r}"
                )
                if ambiguous_failure is None:
                    ambiguous_failure = failure
            return winner

        # Phase 1 — collect the static candidates that pass the cheap 1-byte check.
        tried: set[bytes] = set()
        survivors: list[bytes] = []
        for password in self._passwords.iter_candidates():
            tried.add(password)
            if weak_ok(password):
                survivors.append(password)

        # Phase 2 — one shared CRC pass over the survivors.
        winner = disambiguate(survivors)

        # Phase 3 — provider fallback: ask, cheap-check, per-candidate CRC pass, repeat.
        attempt = 1
        while winner is None and self._passwords.has_provider():
            try:
                password = self._passwords.ask_provider(member, attempt)
            except EncryptionError as exc:
                self._stamp_error_context(exc, member_name)
                raise
            if password is None:
                break
            if password in tried:
                break
            tried.add(password)
            if weak_ok(password):
                winner = disambiguate([password])
            attempt += 1

        # Phase 4 — resolve the outcome (winner, else the most specific error).
        if winner is not None:
            self._passwords.record_success(winner)
            return self._open_zipfile_member(
                info, password=winner, member_name=member_name
            )

        if ambiguous_failure is not None:
            ambiguous = EncryptionError(
                f"No password candidate produced integrity-verified data for "
                f"ZIP member {member_name!r}; the password(s) may be wrong, or "
                "the encrypted member may be corrupt"
            )
            self._stamp_error_context(ambiguous, member_name)
            raise ambiguous from ambiguous_failure

        if not self._passwords.has_passwords():
            required = EncryptionError("Password required to read this ZIP member")
            self._stamp_error_context(required, member_name)
            raise required
        wrong = EncryptionError("Wrong password for this ZIP member")
        self._stamp_error_context(wrong, member_name)
        raise wrong

    def _finish_password_attempt(
        self,
        member: ArchiveMember | None,
        member_name: str,
        decrypt: Callable[[bytes], BinaryIO],
        *,
        ambiguous_holder: list[EncryptionError] | None,
    ) -> BinaryIO:
        try:
            return self._passwords.attempt(member, decrypt)
        except _PasswordCandidatesExhausted as exc:
            ambiguous_failure = ambiguous_holder[0] if ambiguous_holder else None
            if ambiguous_failure is not None:
                ambiguous = EncryptionError(
                    f"No password candidate produced integrity-verified data for "
                    f"ZIP member {member_name!r}; the password(s) may be wrong, or "
                    "the encrypted member may be corrupt"
                )
                self._stamp_error_context(ambiguous, member_name)
                raise ambiguous from ambiguous_failure
            if exc.last_error is not None:
                last_error = exc.last_error
                self._stamp_error_context(last_error, member_name)
                raise last_error from last_error.__cause__
            required = EncryptionError(exc.message)
            self._stamp_error_context(required, member_name)
            raise required from None
        except EncryptionError as exc:
            self._stamp_error_context(exc, member_name)
            raise

    def _ensure_link_target(self, member: ArchiveMember) -> None:
        if member.type != MemberType.SYMLINK or member.link_target is not None:
            return
        info = member._raw
        assert isinstance(info, zipfile.ZipInfo), (
            "ZIP member is missing its ZipInfo handle"
        )
        # A symlink's target is its (possibly encrypted) file data. Listing must stay
        # usable without a password, so a missing/wrong password leaves link_target
        # unset (following the link later fails with LinkTargetNotFoundError); other
        # errors surface translated like any member-read error.
        try:
            with self._open_member(member) as f:
                member.link_target = f.read().decode("utf-8", errors="surrogateescape")
        except EncryptionError:
            message = (
                f"Cannot read the symlink target of {info.filename!r} without the "
                f"correct password; leaving link_target unset."
            )
            self._diagnostics_collector.emit(
                code=DiagnosticCode.SYMLINK_TARGET_UNAVAILABLE,
                message=message,
                context=SymlinkTargetContext(
                    archive_name=self._archive_name,
                    member_name=member.name,
                    member_id=member._member_id,
                    reason="password_required",
                ),
                member=member,
                attach_to_member=True,
                logger=logger,
            )
        except _ZIP_MEMBER_READ_ERRORS as exc:
            # Reading the symlink's target data (raw zipfile stream, not ArchiveStream-wrapped)
            # can raise any of the member-read errors on a corrupt entry; translate them the
            # same way rather than letting a raw codec exception escape the listing. (An
            # EncryptionError is handled by the separate except above and never reaches here.)
            self._reraise_member_error(exc, info.filename)

    def _open_member(self, member: ArchiveMember) -> ArchiveStream:
        # The member carries its own ZipInfo (`_raw`), so data access needs no name/id map
        # — and a duplicate member name can't resolve to the wrong entry.
        info = member._raw
        assert isinstance(info, zipfile.ZipInfo), (
            "ZIP member is missing its ZipInfo handle"
        )
        if info.compress_type == 99:
            return self._open_aes_member(info, member, member_name=member.name)
        if bool(info.flag_bits & 0x1):
            # Traditional ZipCrypto stays on the stdlib zipfile decryption path.
            raw = self._open_zip_entry(info, member, member_name=member.name)
            return self._wrap_member_stream(raw, member.name, size=member.size)
        # Unencrypted: codec layer already wrapped (+ VerifyingStream) in _open_codec_member.
        return self._open_codec_member(info, member, member_name=member.name)

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
        with self._handle_guard():
            self._archive.close()
        if self._owned_fp is not None:
            self._owned_fp.close()
            self._owned_fp = None


def _looks_like_multivolume(exc: zipfile.BadZipFile) -> bool:
    text = str(exc).lower()
    return "multi" in text or "disk" in text or "spanned" in text or "split" in text


class ZipReadBackend(ReadBackend):
    """Backend factory for ZIP archives."""

    FORMATS: tuple[ArchiveFormat, ...] = (ArchiveFormat.ZIP,)
    EXTENSIONS: Mapping[str, ArchiveFormat] = {".zip": ArchiveFormat.ZIP}
    MAGIC: tuple[MagicSignature, ...] = (
        MagicSignature(
            0, b"\x50\x4b\x03\x04", ArchiveFormat.ZIP
        ),  # standard local header
        MagicSignature(
            0, b"\x50\x4b\x05\x06", ArchiveFormat.ZIP
        ),  # empty archive (EOCD)
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
        collector: DiagnosticCollector | None = None,
        member_streams: MemberStreams = MemberStreams(0),
        open_site: OpenSite | None = None,
    ) -> ZipReader:
        # `format` is always ZIP here (single-format backend); accepted for the uniform
        # ReadBackend signature.
        return ZipReader(
            source,
            streaming,
            passwords,
            encoding,
            archive_name,
            config,
            collector=collector,
            member_streams=member_streams,
            open_site=open_site,
        )


register_reader(ZipReadBackend)
