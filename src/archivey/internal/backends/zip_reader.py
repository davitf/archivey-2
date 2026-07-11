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
from collections.abc import Callable
from collections.abc import Iterator as IterType
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO, Iterator, Mapping, cast

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
from archivey.internal.password import (
    _PasswordCandidates,
    _PasswordCandidatesExhausted,
)
from archivey.internal.password_confirm import (
    CONFIRM_PREFIX_BYTES,
    first_crc_match,
    read_and_discard,
)
from archivey.internal.registry import register_reader
from archivey.internal.streams.streamtools import is_seekable, is_stream
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


def _is_candidate_integrity_failure(exc: Exception) -> bool:
    """Whether ``exc`` can be caused by a ZipCrypto verification-byte collision."""
    if isinstance(exc, zipfile.BadZipFile):
        # A wrong ZipCrypto candidate can produce a CRC mismatch after decrypting bytes,
        # but cannot alter the unencrypted local header or archive structure.
        return str(exc).startswith("Bad CRC-32 for file ")
    return isinstance(exc, (zlib.error, lzma.LZMAError)) or (
        isinstance(exc, OSError) and str(exc) == _BZIP2_INVALID_DATA
    )


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
            logger.warning(
                "Invalid ZIP date_time for %r: %r", info.filename, info.date_time
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
                ts = int.from_bytes(
                    ut_field[cursor : cursor + 4], "little", signed=True
                )
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
        return None

    def _iter_members(self) -> Iterator[ArchiveMember]:
        for info in self._archive.infolist():
            yield self._to_member(info)

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

        algo = _ZIP_COMPRESSION_ALGOS.get(
            info.compress_type, CompressionAlgorithm.UNKNOWN
        )

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

    def _zipcrypto_check_byte(self, info: zipfile.ZipInfo) -> int:
        if info.flag_bits & _ZIP_MASK_USE_DATA_DESCRIPTOR:
            # zipfile stores the DOS time in the private ``_raw_time`` attribute and uses
            # its high byte as the ZipCrypto check byte when a data descriptor is present.
            raw_time = int(getattr(info, "_raw_time", 0))
            return (raw_time >> 8) & 0xFF
        return (info.CRC >> 24) & 0xFF

    def _zipfile_lock(self) -> Any:
        # stdlib ZipFile serializes fp access via a private lock; typeshed omits it.
        return getattr(self._archive, "_lock")

    @contextmanager
    def _ciphertext_chunks(
        self,
        info: zipfile.ZipInfo,
        *,
        body_start: int = 0,
        body_limit: int | None = None,
    ) -> IterType[IterType[bytes]]:
        """Yield ciphertext body chunks (after the 12-byte ZipCrypto header).

        ``body_start`` / ``body_limit`` are offsets into the encrypted *body*
        (excluding the header). Streams from the archive file under ``ZipFile``'s
        lock so position is restored; never buffers the whole member.
        """
        zf = self._archive
        header_len = 12
        body_total = max(0, info.compress_size - header_len)
        start = min(max(0, body_start), body_total)
        if body_limit is None:
            end = body_total
        else:
            end = min(body_total, start + max(0, body_limit))
        chunk_size = 64 * 1024

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
                # Skip encryption header + body_start.
                fp.read(header_len + start)

                def iterator() -> IterType[bytes]:
                    remaining = end - start
                    while remaining > 0:
                        piece = fp.read(min(chunk_size, remaining))
                        if not piece:
                            break
                        remaining -= len(piece)
                        yield piece

                yield iterator()
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

    def _open_zip_entry(
        self,
        info: zipfile.ZipInfo,
        member: ArchiveMember | None,
        *,
        member_name: str,
    ) -> BinaryIO:
        encrypted = bool(info.flag_bits & 0x1)
        if not encrypted:
            return self._open_zipfile_member(info, password=None, member_name=member_name)

        # ZipCrypto's one-byte open check admits ~1/256 of wrong passwords. With more
        # than one possible candidate (or a provider), confirm before accepting.
        # Confirmed winners are re-opened fresh — no plaintext retained.
        if not self._passwords.is_ambiguous():
            return self._open_encrypted_lazy(info, member, member_name=member_name)

        if info.compress_type == zipfile.ZIP_STORED:
            return self._open_stored_confirmed(info, member, member_name=member_name)
        return self._open_compressed_confirmed(info, member, member_name=member_name)

    def _open_zipfile_member(
        self,
        info: zipfile.ZipInfo,
        *,
        password: bytes | None,
        member_name: str,
    ) -> BinaryIO:
        """Open via ``zipfile`` and translate member-open failures."""
        try:
            return cast("BinaryIO", self._archive.open(info, pwd=password))
        except (
            zipfile.BadZipFile,
            RuntimeError,
            io.UnsupportedOperation,
            NotImplementedError,
            zlib.error,
            lzma.LZMAError,
            UnicodeDecodeError,
            ValueError,
            OSError,
        ) as exc:
            translated = self._translate_exception(exc)
            if translated is not None:
                if isinstance(translated, EncryptionError):
                    raise translated from exc
                self._stamp_error_context(translated, member_name)
                raise translated from exc
            raise

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
                stream = cast("BinaryIO", self._archive.open(info, pwd=password))
                read_and_discard(stream, CONFIRM_PREFIX_BYTES)
                stream.close()
                stream = None
                # Fresh stream for the caller; zipfile re-checks CRC at EOF.
                return self._open_zipfile_member(
                    info, password=password, member_name=member_name
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
                OSError,
            ) as exc:
                if _is_candidate_integrity_failure(exc):
                    failure = EncryptionError(
                        f"Password candidate failed integrity validation for ZIP "
                        f"member {member_name!r}"
                    )
                    if not ambiguous_holder:
                        ambiguous_holder.append(failure)
                    raise failure from exc
                translated = self._translate_exception(exc)
                if translated is not None:
                    if isinstance(translated, EncryptionError):
                        raise translated from exc
                    self._stamp_error_context(translated, member_name)
                    raise translated from exc
                raise
            finally:
                if stream is not None:
                    stream.close()

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
        """STORED ZipCrypto: one shared CRC pass over surviving weak-check candidates."""
        ambiguous_failure: EncryptionError | None = None
        check_byte = self._zipcrypto_check_byte(info)
        expected_crc = info.CRC & 0xFFFFFFFF

        try:
            header = self._read_zipcrypto_header(info)
        except zipfile.BadZipFile as exc:
            translated = self._translate_exception(exc)
            if translated is not None:
                self._stamp_error_context(translated, member_name)
                raise translated from exc
            raise

        def weak_ok(password: bytes) -> bool:
            return password_matches_check_byte(password, header, check_byte)

        def disambiguate(survivors: list[bytes]) -> bytes | None:
            nonlocal ambiguous_failure
            if not survivors:
                return None
            # No decompressor to reject garbage: one shared ciphertext pass computes
            # every survivor's plaintext CRC-32 in constant memory.
            with self._ciphertext_chunks(info) as chunks:
                crcs = parallel_plaintext_crc32(survivors, header, chunks)
            winner = first_crc_match(expected_crc, crcs)
            if winner is None:
                failure = EncryptionError(
                    f"Password candidate failed integrity validation for ZIP "
                    f"member {member_name!r}"
                )
                if ambiguous_failure is None:
                    ambiguous_failure = failure
            return winner

        tried: set[bytes] = set()
        survivors: list[bytes] = []
        for password in self._passwords.iter_candidates():
            tried.add(password)
            if weak_ok(password):
                survivors.append(password)

        winner = disambiguate(survivors)

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
        assert isinstance(info, zipfile.ZipInfo), (
            "ZIP member is missing its ZipInfo handle"
        )
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
    ) -> ZipReader:
        # `format` is always ZIP here (single-format backend); accepted for the uniform
        # ReadBackend signature.
        return ZipReader(source, streaming, passwords, encoding, archive_name, config)


register_reader(ZipReadBackend)
