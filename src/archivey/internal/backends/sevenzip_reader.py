"""Native 7z reader backend."""

from __future__ import annotations

import io
import re
import stat
import zlib
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import BinaryIO

from archivey.config import ArchiveyConfig
from archivey.cost import AccessCost, CostReceipt, ListingCost, StreamCapability
from archivey.diagnostics import DiagnosticCode, DigestContext, MemberTimestampContext
from archivey.exceptions import (
    ArchiveyError,
    CorruptionError,
    EncryptionError,
    PackageNotInstalledError,
    StreamNotSeekableError,
    TruncatedError,
    UnsupportedFeatureError,
)
from archivey.internal.backends.sevenzip_methods import METHOD_AES
from archivey.internal.backends.sevenzip_parser import (
    EncodedHeader,
    PlainHeader,
    SevenZipArchive,
    SevenZipCoder,
    SevenZipFileRecord,
    SevenZipFolder,
    compression_method_for_coder,
    compute_is_current,
    empty_archive,
    folder_is_encrypted,
    materialize_archive,
    parse_header_block,
    read_signature_and_next_header,
)
from archivey.internal.backends.sevenzip_pipeline import (
    decode_encoded_header,
    decode_folder_to_bytes,
    encoded_header_needs_password,
    open_folder_pipeline,
)
from archivey.internal.base_reader import BaseArchiveReader, ReadBackend
from archivey.internal.config import stream_config_from_archivey
from archivey.internal.diagnostics_collector import DiagnosticCollector
from archivey.internal.logs import backends as logger
from archivey.internal.logs import integrity as integrity_logger
from archivey.internal.naming import (
    emit_member_name_normalized,
    infer_member_name_from_archive,
    normalize_member_name,
)
from archivey.internal.open_site import OpenSite
from archivey.internal.password import (
    _PasswordCandidates,
    _PasswordCandidatesExhausted,
)
from archivey.internal.registry import register_reader
from archivey.internal.streams.archive_stream import ArchiveStream
from archivey.internal.streams.crypto import SevenZipKeyCache
from archivey.internal.streams.streamtools import (
    SharedSource,
    SlicingStream,
    SolidBlockReader,
    is_seekable,
    is_stream,
    read_exact,
    skip_forward,
)
from archivey.internal.streams.verify import VerifyingStream
from archivey.internal.timestamps import TimestampIssue, filetime_to_datetime
from archivey.types import (
    ArchiveFormat,
    ArchiveInfo,
    ArchiveMember,
    CompressionAlgorithm,
    CreateSystem,
    MagicSignature,
    MemberStreams,
    MemberType,
)

_WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_SEVENZIP_STEM_SUFFIX_RE = re.compile(r"\.7z(?:\.\d{3})?$", re.IGNORECASE)

# Local aliases keep test imports of these private names working.
_TimestampIssue = TimestampIssue
_filetime_to_datetime = filetime_to_datetime


@dataclass(frozen=True)
class _MemberRaw:
    record: SevenZipFileRecord
    folder_index: int | None
    file_in_folder: int | None


def _password_to_kdf_bytes(password: bytes) -> bytes:
    try:
        return password.decode("utf-8").encode("utf-16le")
    except UnicodeDecodeError:
        return password


def _infer_nameless_member_name(archive_name: str | None) -> str:
    return infer_member_name_from_archive(
        archive_name, strip_suffix_re=_SEVENZIP_STEM_SUFFIX_RE
    )


def _member_stream_size(member: ArchiveMember) -> int:
    return member.size if member.size is not None else 0


def _verify_decoded_folder(
    folder: SevenZipFolder,
    decoded: bytes,
    *,
    member_digests: list[tuple[int, int | None]] | None = None,
) -> None:
    """Raise ``EncryptionError`` when decoded folder bytes fail CRC checks."""
    if folder.digest_defined:
        expected = (folder.crc if folder.crc is not None else 0) & 0xFFFFFFFF
        if zlib.crc32(decoded) & 0xFFFFFFFF != expected:
            raise EncryptionError("Wrong password or corrupt 7z folder")
        return
    if not member_digests:
        return
    offset = 0
    for size, raw_expected in member_digests:
        chunk = decoded[offset : offset + size]
        offset += size
        if raw_expected is None:
            continue
        if zlib.crc32(chunk) & 0xFFFFFFFF != raw_expected & 0xFFFFFFFF:
            raise EncryptionError("Wrong password or corrupt 7z folder")


class SevenZipReader(BaseArchiveReader):
    """Reads 7z archives using the native parser and shared codec streams."""

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
            ArchiveFormat.SEVEN_Z,
            streaming,
            archive_name,
            config,
            collector=collector,
            member_streams=member_streams,
            open_site=open_site,
        )
        del encoding  # 7z stores names as UTF-16LE.
        self._source = source
        self._passwords = passwords or _PasswordCandidates()
        self._key_cache = SevenZipKeyCache()
        self._folder_passwords: dict[int, bytes | None] = {}
        self._stream_config = stream_config_from_archivey(
            self._config,
            streaming=streaming,
            seekable=MemberStreams.SEEKABLE in member_streams,
        )
        if is_stream(source) and not is_seekable(source):
            raise StreamNotSeekableError(
                "7z archives require a seekable source: the header and packed streams "
                "are addressed by offsets.",
                archive_name=archive_name,
                source_format=ArchiveFormat.SEVEN_Z,
            )
        self._shared = SharedSource(source, wrap_handle=self._seek_handle_wrapper())
        self._volume_count = getattr(source, "volume_count", 1)
        self._archive = self._load_archive()
        self._folder_pack_starts = self._folder_pack_start_indices(self._archive)
        self._members = self._build_members()
        self._folder_members = self._members_by_folder()

    def _load_archive(self) -> SevenZipArchive:
        """Two-phase header load: parse → decode encoded → re-parse → materialize."""
        fp = self._shared.view(0)
        signature = read_signature_and_next_header(fp)
        if not signature.header_data:
            return empty_archive(signature)

        block = parse_header_block(signature.header_data)
        header_encrypted = False
        while isinstance(block, EncodedHeader):
            header_encrypted = header_encrypted or encoded_header_needs_password(block)
            decoded = self._decode_encoded_header_block(fp, block)
            block = parse_header_block(decoded)
        assert isinstance(block, PlainHeader)
        return materialize_archive(
            signature, block, is_header_encrypted=header_encrypted
        )

    def _decode_encoded_header_block(
        self, fp: BinaryIO, encoded: EncodedHeader
    ) -> bytes:
        needs_password = encoded_header_needs_password(encoded)

        def decode(password: bytes | None) -> bytes:
            return decode_encoded_header(
                fp,
                encoded,
                password=password,
                key_cache=self._key_cache,
                stream_config=self._stream_config,
                collector=self._diagnostics_collector,
            )

        try:
            if needs_password:
                return self._passwords.attempt(
                    None, lambda password: decode(_password_to_kdf_bytes(password))
                )
            return decode(None)
        except _PasswordCandidatesExhausted as exc:
            raise EncryptionError("Password required to decrypt the 7z header") from exc

    @staticmethod
    def _folder_pack_start_indices(archive: SevenZipArchive) -> list[int]:
        starts: list[int] = []
        index = 0
        for folder in archive.folders:
            starts.append(index)
            index += len(folder.packed_indices)
        return starts

    def _build_members(self) -> list[ArchiveMember]:
        current_flags = compute_is_current(self._archive.files)
        return [
            self._to_member(record, is_current=is_current)
            for record, is_current in zip(
                self._archive.files, current_flags, strict=True
            )
        ]

    def _members_by_folder(self) -> dict[int, list[ArchiveMember]]:
        grouped: dict[int, list[ArchiveMember]] = {}
        for member in self._members:
            raw = member._raw
            assert isinstance(raw, _MemberRaw)
            if raw.folder_index is not None:
                grouped.setdefault(raw.folder_index, []).append(member)
        return grouped

    def _iter_members(self) -> Iterator[ArchiveMember]:
        yield from self._members

    def _iter_with_data(self) -> Iterator[tuple[ArchiveMember, ArchiveStream | None]]:
        current_folder: int | None = None
        solid: SolidBlockReader | None = None
        previous: ArchiveStream | None = None
        try:
            for member in self._members:
                if previous is not None:
                    previous.close()
                    previous = None
                if not member.is_file:
                    yield member, None
                    continue
                raw = member._raw
                assert isinstance(raw, _MemberRaw)
                if raw.folder_index is None:
                    stream = self._wrap_member_stream(
                        io.BytesIO(b""), member.name, size=member.size
                    )
                    previous = stream
                    yield member, stream
                    continue
                if raw.folder_index != current_folder:
                    if solid is not None:
                        solid.close()
                    current_folder = raw.folder_index
                    # Count at the folder decode layer (solid invariant); member wraps
                    # pass track_output=False so sequential reads are not double-counted.
                    solid = SolidBlockReader(
                        self._track_decompressed(
                            self._open_folder_stream(raw.folder_index, member)
                        )
                    )
                assert solid is not None
                member_stream = self._member_stream_from_solid(solid, member)
                previous = member_stream
                yield member, member_stream
        finally:
            if previous is not None:
                previous.close()
            if solid is not None:
                solid.close()

    def _to_member(
        self, record: SevenZipFileRecord, *, is_current: bool
    ) -> ArchiveMember:
        member_type = self._member_type(record)
        presented_name = record.filename
        if presented_name == "":
            presented_name = _infer_nameless_member_name(self._archive_name)
        name = normalize_member_name(
            presented_name,
            member_type,
            backslash_is_separator=True,
        )
        raw_name = record.filename.encode("utf-16le", errors="surrogateescape")
        compression = tuple(
            method
            for method in (
                compression_method_for_coder(coder)
                for coder in self._folder_coders(record)
                if coder.method != METHOD_AES.method_id
            )
            if method.algo is not CompressionAlgorithm.UNKNOWN
        )
        hashes: dict[str, int] = {}
        if record.crc32 is not None:
            hashes["crc32"] = record.crc32
        attrs = record.attributes
        unix_mode = (attrs >> 16) if attrs is not None and attrs >> 16 else None
        mode = stat.S_IMODE(unix_mode) if unix_mode is not None else None
        extra: dict[str, object] = {}
        if record.folder_index is not None:
            extra["7z.folder_index"] = record.folder_index
        if record.file_in_folder is not None:
            extra["7z.file_in_folder"] = record.file_in_folder
        ts_issues: list[_TimestampIssue] = []
        timestamps: dict[str, datetime | None] = {}
        for field, value in (
            ("modified", record.last_write_time),
            ("accessed", record.last_access_time),
            ("created", record.creation_time),
        ):
            timestamps[field], issue = _filetime_to_datetime(
                value, presented_name, field=field
            )
            if issue is not None:
                ts_issues.append(issue)
        member = ArchiveMember(
            type=member_type,
            name=name,
            raw_name=raw_name,
            size=record.uncompressed_size,
            compressed_size=record.compressed_size,
            modified=timestamps["modified"],
            accessed=timestamps["accessed"],
            created=timestamps["created"],
            mode=mode,
            compression=compression,
            is_encrypted=record.is_encrypted,
            is_current=is_current,
            create_system=CreateSystem.UNIX
            if unix_mode is not None
            else CreateSystem.WINDOWS_NTFS,
            windows_attrs=attrs & 0xFFFF if attrs is not None else None,
            hashes=hashes,
            extra=extra,
            _raw=_MemberRaw(record, record.folder_index, record.file_in_folder),
        )
        emit_member_name_normalized(
            self._diagnostics_collector,
            member=member,
            presented_name=presented_name,
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
                    source="ntfs",
                    value_repr=issue.value_repr,
                ),
                member=member,
                attach_to_member=True,
                logger=logger,
            )
        # Encrypted folder with no folder digest and no per-member CRC: 7zAES has no
        # password check of its own, so a wrong password cannot be detected (matches
        # 7-Zip). Surface that as DIGEST_UNVERIFIABLE rather than silently implying
        # the decryption was authenticated.
        if (
            record.is_encrypted
            and record.crc32 is None
            and record.folder_index is not None
            and not self._archive.folders[record.folder_index].digest_defined
        ):
            self._diagnostics_collector.emit(
                code=DiagnosticCode.DIGEST_UNVERIFIABLE,
                message=(
                    "Encrypted 7z member has no folder digest and no member CRC; "
                    "decryption cannot be authenticated (wrong passwords may go "
                    "undetected on store/copy streams)."
                ),
                context=DigestContext(
                    archive_name=self._archive_name,
                    member_name=member.name,
                    member_id=member._member_id,
                    algorithm="",
                    reason="no_integrity_anchor",
                ),
                member=member,
                attach_to_member=True,
                logger=integrity_logger,
            )
        return member

    def _member_type(self, record: SevenZipFileRecord) -> MemberType:
        if record.is_anti:
            return MemberType.ANTI
        attrs = record.attributes
        if attrs is not None:
            unix_mode = attrs >> 16
            if unix_mode:
                if stat.S_ISLNK(unix_mode):
                    return MemberType.SYMLINK
                if stat.S_ISDIR(unix_mode):
                    return MemberType.DIRECTORY
            if attrs & _WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT:
                return MemberType.SYMLINK
        if record.is_directory:
            return MemberType.DIRECTORY
        return MemberType.FILE

    def _folder_coders(self, record: SevenZipFileRecord) -> tuple[SevenZipCoder, ...]:
        if record.folder_index is None:
            return ()
        return tuple(self._archive.folders[record.folder_index].coders)

    def _folder_pack_view(self, folder_index: int) -> BinaryIO:
        folder = self._archive.folders[folder_index]
        pack_count = len(folder.packed_indices)
        if pack_count != 1:
            raise UnsupportedFeatureError(
                "7z folders with multiple packed streams are not supported"
            )
        pack_index = self._folder_pack_starts[folder_index]
        if pack_index >= len(self._archive.pack_sizes):
            raise CorruptionError("7z folder references a missing packed stream")
        pack_offset = self._archive.pack_pos + self._archive.pack_positions[pack_index]
        pack_size = self._archive.pack_sizes[pack_index]
        return self._shared.view(pack_offset, pack_size)

    def _folder_unpack_size(self, folder_index: int) -> int:
        members = self._folder_members.get(folder_index, [])
        return sum(_member_stream_size(member) for member in members)

    def _open_folder_stream(
        self,
        folder_index: int,
        member: ArchiveMember | None,
        *,
        seekable: bool = False,
        track_output: bool = False,
    ) -> BinaryIO:
        folder = self._archive.folders[folder_index]
        password = self._password_for_folder(folder_index, member)
        stream = open_folder_pipeline(
            self._folder_pack_view(folder_index),
            folder,
            password=password,
            key_cache=self._key_cache,
            stream_config=self._stream_config,
            collector=self._diagnostics_collector,
            seekable=seekable,
        )
        # Random ``open()`` passes track_output=True so each from-start folder decode
        # counts; sequential ``_iter_with_data`` already wraps once around SolidBlockReader.
        if track_output:
            return self._track_decompressed(stream)
        return stream

    def _open_folder_pipeline(
        self,
        source: BinaryIO,
        folder: SevenZipFolder,
        *,
        password: bytes | None,
        seekable: bool = False,
    ) -> BinaryIO:
        """Compatibility shim for tests that patch/call this method."""
        return open_folder_pipeline(
            source,
            folder,
            password=password,
            key_cache=self._key_cache,
            stream_config=self._stream_config,
            collector=self._diagnostics_collector,
            seekable=seekable,
        )

    def _password_for_folder(
        self, folder_index: int, member: ArchiveMember | None
    ) -> bytes | None:
        folder = self._archive.folders[folder_index]
        if not folder_is_encrypted(folder):
            return None
        if folder_index in self._folder_passwords:
            return self._folder_passwords[folder_index]

        member_digests: list[tuple[int, int | None]] = []
        for folder_member in self._folder_members.get(folder_index, []):
            size = _member_stream_size(folder_member)
            raw_expected = (
                folder_member.hashes.get("crc32") if folder_member.hashes else None
            )
            if isinstance(raw_expected, bytes):
                expected: int | None = int.from_bytes(raw_expected, "big") & 0xFFFFFFFF
            elif isinstance(raw_expected, int):
                expected = raw_expected & 0xFFFFFFFF
            else:
                expected = None
            member_digests.append((size, expected))

        def confirm(password: bytes) -> bytes:
            kdf_password = _password_to_kdf_bytes(password)
            stream = open_folder_pipeline(
                self._folder_pack_view(folder_index),
                folder,
                password=kdf_password,
                key_cache=self._key_cache,
                stream_config=self._stream_config,
                collector=self._diagnostics_collector,
            )
            try:
                total = self._folder_unpack_size(folder_index)
                decoded = read_exact(stream, total)
                if len(decoded) != total:
                    raise EncryptionError("Wrong password or corrupt 7z folder")
                _verify_decoded_folder(folder, decoded, member_digests=member_digests)
                return kdf_password
            except (UnsupportedFeatureError, PackageNotInstalledError):
                # Hostile NumCyclesPower / missing [crypto] must not look like a wrong password.
                raise
            except ArchiveyError as exc:
                raise EncryptionError("Wrong password or corrupt 7z folder") from exc
            finally:
                stream.close()

        try:
            password = self._passwords.attempt(member, confirm)
        except _PasswordCandidatesExhausted as exc:
            raise EncryptionError(
                "Password required to decrypt this 7z member"
            ) from exc
        self._folder_passwords[folder_index] = password
        return password

    def _member_prefix(self, member: ArchiveMember) -> int:
        raw = member._raw
        assert isinstance(raw, _MemberRaw)
        if raw.folder_index is None or raw.file_in_folder is None:
            return 0
        prior = self._folder_members.get(raw.folder_index, [])[: raw.file_in_folder]
        return sum(_member_stream_size(p) for p in prior)

    def _wrap_folder_member(
        self, inner: BinaryIO, member: ArchiveMember
    ) -> ArchiveStream:
        if member.size is not None or member.hashes:
            inner = VerifyingStream(
                inner,
                member.hashes,
                expected_size=member.size,
                collector=self._diagnostics_collector,
                member=member,
                archive_name=self._archive_name,
            )
        return self._wrap_member_stream(
            inner, member.name, size=member.size, track_output=False
        )

    def _member_stream_from_solid(
        self, solid: SolidBlockReader, member: ArchiveMember
    ) -> ArchiveStream:
        try:
            inner = solid.open_member(
                self._member_prefix(member), _member_stream_size(member)
            )
        except EOFError as exc:
            raise TruncatedError("7z folder ended before the requested member") from exc
        return self._wrap_folder_member(inner, member)

    def _ensure_link_target(self, member: ArchiveMember) -> None:
        if member.type != MemberType.SYMLINK or member.link_target is not None:
            return
        try:
            with self._open_member(member) as stream:
                member.link_target = stream.read().decode(
                    "utf-8", errors="surrogateescape"
                )
        except EncryptionError:
            return

    def _open_member(self, member: ArchiveMember) -> ArchiveStream:
        raw = member._raw
        assert isinstance(raw, _MemberRaw)
        if raw.folder_index is None:
            return self._wrap_member_stream(
                io.BytesIO(b""), member.name, size=member.size
            )
        want_seekable = self._stream_config.seekable
        prefix = self._member_prefix(member)
        size = _member_stream_size(member)
        folder_stream = self._open_folder_stream(
            raw.folder_index,
            member,
            seekable=want_seekable,
            track_output=True,
        )
        try:
            if want_seekable and is_seekable(folder_stream):
                inner: BinaryIO = SlicingStream(
                    folder_stream, start=prefix, length=size, own_source=True
                )
            else:
                skip_forward(folder_stream, prefix)
                inner = SlicingStream(folder_stream, length=size, own_source=True)
        except EOFError as exc:
            folder_stream.close()
            raise TruncatedError("7z folder ended before the requested member") from exc
        except BaseException:
            folder_stream.close()
            raise
        try:
            return self._wrap_folder_member(inner, member)
        except BaseException:
            inner.close()
            raise

    def _get_archive_info(self) -> ArchiveInfo:
        solid_blocks = sum(
            1 for members in self._folder_members.values() if len(members) > 1
        )
        cost = CostReceipt(
            listing_cost=ListingCost.INDEXED,
            access_cost=AccessCost.SOLID
            if self._archive.is_solid
            else AccessCost.DIRECT,
            stream_capability=StreamCapability.SEEKABLE,
            solid_block_count=solid_blocks if self._archive.is_solid else None,
        )
        return ArchiveInfo(
            format=ArchiveFormat.SEVEN_Z,
            format_version=f"{self._archive.major_version}.{self._archive.minor_version}",
            is_solid=self._archive.is_solid,
            member_count=len(self._members),
            comment=self._archive.comment,
            is_encrypted=self._archive.is_header_encrypted
            or self._archive.has_encrypted_folders,
            is_multivolume=self._volume_count > 1,
            cost=cost,
            extra={"7z.volume_count": self._volume_count},
        )

    def _close_archive(self) -> None:
        self._shared.close()


class SevenZipReadBackend(ReadBackend):
    """Backend factory for 7z archives."""

    FORMATS: tuple[ArchiveFormat, ...] = (ArchiveFormat.SEVEN_Z,)
    EXTENSIONS: Mapping[str, ArchiveFormat] = {".7z": ArchiveFormat.SEVEN_Z}
    MAGIC: tuple[MagicSignature, ...] = (
        MagicSignature(0, b"7z\xbc\xaf'\x1c", ArchiveFormat.SEVEN_Z),
    )
    SUPPORTS_PASSWORD = True
    SUPPORTS_STREAMING_NON_SEEKABLE = False
    OPTIONAL_DEPENDENCY = None

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
    ) -> SevenZipReader:
        del format
        return SevenZipReader(
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


register_reader(SevenZipReadBackend)

# Re-exports used by fuzz harnesses / older imports.
__all__ = [
    "SevenZipReadBackend",
    "SevenZipReader",
    "decode_folder_to_bytes",
    "open_folder_pipeline",
]
