"""Native 7z reader backend."""

from __future__ import annotations

import io
import lzma
import stat
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO

from archivey.config import ArchiveyConfig
from archivey.cost import AccessCost, CostReceipt, ListingCost, StreamCapability
from archivey.exceptions import (
    ArchiveyError,
    CorruptionError,
    EncryptionError,
    StreamNotSeekableError,
    TruncatedError,
    UnsupportedFeatureError,
)
from archivey.internal.backends.sevenzip_parser import (
    SevenZipArchive,
    SevenZipCoder,
    SevenZipFileRecord,
    SevenZipFolder,
    compression_method_for_coder,
    compute_is_current,
    folder_is_encrypted,
    parse_sevenzip_archive,
)
from archivey.internal.base_reader import BaseArchiveReader, ReadBackend
from archivey.internal.config import stream_config_from_archivey
from archivey.internal.diagnostics_collector import DiagnosticCollector
from archivey.internal.naming import emit_member_name_normalized, normalize_member_name
from archivey.internal.open_site import OpenSite
from archivey.internal.password import (
    _PasswordCandidates,
    _PasswordCandidatesExhausted,
)
from archivey.internal.registry import register_reader
from archivey.internal.streams.archive_stream import ArchiveStream
from archivey.internal.streams.codecs import (
    LZMA_FILTER_IDS,
    Codec,
    CodecParams,
    open_codec_stream,
)
from archivey.internal.streams.crypto import (
    SevenZipKeyCache,
    open_aes_decrypt_stream,
)
from archivey.internal.streams.streamtools import (
    ReadOnlyIOStream,
    SharedSource,
    SlicingStream,
    is_seekable,
    is_stream,
    read_exact,
)
from archivey.internal.streams.verify import VerifyingStream
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

_METHOD_COPY = b"\x00"
_METHOD_LZMA = b"\x03\x01\x01"
_METHOD_LZMA2 = b"\x21"
_METHOD_AES = b"\x06\xf1\x07\x01"
_METHOD_BCJ2 = b"\x03\x03\x01\x1b"
_METHOD_DELTA = b"\x03"
_METHOD_DEFLATE = b"\x04\x01\x08"
_METHOD_DEFLATE64 = b"\x04\x01\x09"
_METHOD_BZIP2 = b"\x04\x02\x02"
_METHOD_ZSTD = b"\x04\xf7\x11\x01"
_METHOD_BROTLI = b"\x04\xf7\x11\x02"
_METHOD_PPMD = b"\x03\x04\x01"

_BCJ_METHODS: dict[bytes, Codec] = {
    b"\x04": Codec.BCJ_X86,
    b"\x05": Codec.BCJ_PPC,
    b"\x06": Codec.BCJ_IA64,
    b"\x07": Codec.BCJ_ARM,
    b"\x08": Codec.BCJ_ARMT,
    b"\x09": Codec.BCJ_SPARC,
    b"\x03\x03\x01\x03": Codec.BCJ_X86,
    b"\x03\x03\x02\x05": Codec.BCJ_PPC,
    b"\x03\x03\x04\x01": Codec.BCJ_IA64,
    b"\x03\x03\x05\x01": Codec.BCJ_ARM,
    b"\x03\x03\x07\x01": Codec.BCJ_ARMT,
    b"\x03\x03\x08\x05": Codec.BCJ_SPARC,
}

_SINGLE_STAGE_CODECS: dict[bytes, Codec] = {
    _METHOD_DEFLATE: Codec.DEFLATE,
    _METHOD_DEFLATE64: Codec.DEFLATE64,
    _METHOD_BZIP2: Codec.BZIP2,
    _METHOD_ZSTD: Codec.ZSTD,
    _METHOD_BROTLI: Codec.BROTLI,
    _METHOD_PPMD: Codec.PPMD,
}

_WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_NTFS_EPOCH_OFFSET = 11_644_473_600


@dataclass(frozen=True)
class _MemberRaw:
    record: SevenZipFileRecord
    folder_index: int | None
    file_in_folder: int | None


class _LimitedFolderReader(ReadOnlyIOStream):
    """A non-owning limited view over a decoded folder stream."""

    def __init__(
        self, source: BinaryIO, length: int, *, close_source: bool = False
    ) -> None:
        super().__init__()
        self._source = source
        self._length = length
        self._remaining = length
        self._close_source = close_source

    def read(self, n: int = -1, /) -> bytes:
        if self._remaining <= 0:
            return b""
        if n < 0 or n > self._remaining:
            n = self._remaining
        data = self._source.read(n)
        self._remaining -= len(data)
        return data

    def tell(self) -> int:
        return self._length - self._remaining

    def close(self) -> None:
        if self.closed:
            return
        try:
            self.drain()
        finally:
            if self._close_source:
                self._source.close()
            super().close()

    def drain(self) -> None:
        while self._remaining > 0:
            chunk = self.read(min(self._remaining, 1024 * 1024))
            if not chunk:
                break


def _method_hex(method: bytes) -> str:
    return "0x" + method.hex()


def _password_to_kdf_bytes(password: bytes) -> bytes:
    try:
        return password.decode("utf-8").encode("utf-16le")
    except UnicodeDecodeError:
        return password


def _filetime_to_datetime(value: int | None) -> datetime | None:
    if value is None or value == 0:
        return None
    try:
        return datetime.fromtimestamp(
            value / 10_000_000 - _NTFS_EPOCH_OFFSET,
            tz=timezone.utc,
        )
    except (ValueError, OverflowError, OSError):
        return None


def _is_lzma_family(coder: SevenZipCoder) -> bool:
    return (
        coder.method in (_METHOD_LZMA, _METHOD_LZMA2, _METHOD_DELTA)
        or coder.method in _BCJ_METHODS
    )


def _decode_lzma_properties(coder: SevenZipCoder, filter_id: int) -> dict:
    if coder.properties is None:
        return {"id": filter_id}
    try:
        decode_properties = getattr(lzma, "_decode_filter_properties")
        return decode_properties(filter_id, coder.properties)
    except (AttributeError, lzma.LZMAError, ValueError) as exc:
        raise CorruptionError(
            f"Malformed 7z LZMA coder properties for {_method_hex(coder.method)}"
        ) from exc


def _lzma_filter(coder: SevenZipCoder) -> dict:
    if coder.method == _METHOD_LZMA:
        return _decode_lzma_properties(coder, lzma.FILTER_LZMA1)
    if coder.method == _METHOD_LZMA2:
        return _decode_lzma_properties(coder, lzma.FILTER_LZMA2)
    if coder.method == _METHOD_DELTA:
        if coder.properties is None:
            return {"id": lzma.FILTER_DELTA}
        if len(coder.properties) != 1:
            raise CorruptionError("Malformed 7z Delta coder properties")
        return {"id": lzma.FILTER_DELTA, "dist": coder.properties[0] + 1}
    bcj_codec = _BCJ_METHODS.get(coder.method)
    if bcj_codec is not None:
        return {"id": LZMA_FILTER_IDS[bcj_codec]}
    raise UnsupportedFeatureError(
        f"Unsupported 7z LZMA-family coder {_method_hex(coder.method)}"
    )


def _member_stream_size(member: ArchiveMember) -> int:
    return member.size if member.size is not None else 0


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
        self._shared = SharedSource(source)
        self._volume_count = getattr(source, "volume_count", 1)
        self._archive = parse_sevenzip_archive(
            self._shared.view(0),
            passwords=tuple(
                _password_to_kdf_bytes(password)
                for password in self._passwords.iter_candidates()
            ),
            key_cache=self._key_cache,
            decode_folder=self._decode_header_folder,
        )
        self._folder_pack_starts = self._folder_pack_start_indices(self._archive)
        self._members = self._build_members()
        self._folder_members = self._members_by_folder()

    @staticmethod
    def _folder_pack_start_indices(archive: SevenZipArchive) -> list[int]:
        starts: list[int] = []
        index = 0
        for folder in archive.folders:
            starts.append(index)
            index += len(folder.packed_indices)
        return starts

    def _decode_header_folder(
        self,
        source: BinaryIO,
        folder: SevenZipFolder,
        compressed_size: int,
        uncompressed_size: int,
        passwords: tuple[bytes, ...],
        key_cache: SevenZipKeyCache,
    ) -> bytes:
        del passwords, key_cache

        def decrypt(password: bytes) -> bytes:
            source.seek(0)
            kdf_password = _password_to_kdf_bytes(password)
            return self._decode_folder_to_bytes(
                source,
                folder,
                compressed_size=compressed_size,
                uncompressed_size=uncompressed_size,
                password=kdf_password,
            )

        try:
            if folder_is_encrypted(folder):
                return self._passwords.attempt(None, decrypt)
            source.seek(0)
            return self._decode_folder_to_bytes(
                source,
                folder,
                compressed_size=compressed_size,
                uncompressed_size=uncompressed_size,
                password=None,
            )
        except _PasswordCandidatesExhausted as exc:
            raise EncryptionError("Password required to decrypt the 7z header") from exc

    def _decode_folder_to_bytes(
        self,
        source: BinaryIO,
        folder: SevenZipFolder,
        *,
        compressed_size: int,
        uncompressed_size: int,
        password: bytes | None,
    ) -> bytes:
        stream = self._open_folder_pipeline(
            SlicingStream(source, 0, compressed_size),
            folder,
            password=password,
        )
        try:
            decoded = read_exact(stream, uncompressed_size)
            if len(decoded) != uncompressed_size:
                raise TruncatedError("7z folder is truncated after decoding")
            if folder.digest_defined:
                verifier = VerifyingStream(
                    io.BytesIO(decoded),
                    {"crc32": folder.crc if folder.crc is not None else 0},
                    collector=self._diagnostics_collector,
                    archive_name=self._archive_name,
                )
                verifier.read()
                verifier.read()
            return decoded
        finally:
            stream.close()

    def _build_members(self) -> list[ArchiveMember]:
        current_flags = compute_is_current(self._archive.files)
        members: list[ArchiveMember] = []
        for record, is_current in zip(self._archive.files, current_flags, strict=True):
            members.append(self._to_member(record, is_current=is_current))
        return members

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
        folder_stream: BinaryIO | None = None
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
                    if folder_stream is not None:
                        folder_stream.close()
                    current_folder = raw.folder_index
                    folder_stream = self._open_folder_stream(raw.folder_index, member)
                assert folder_stream is not None
                member_stream = self._member_stream_from_folder(folder_stream, member)
                previous = member_stream
                yield member, member_stream
        finally:
            if previous is not None:
                previous.close()
            if folder_stream is not None:
                folder_stream.close()

    def _to_member(
        self, record: SevenZipFileRecord, *, is_current: bool
    ) -> ArchiveMember:
        member_type = self._member_type(record)
        name = normalize_member_name(
            record.filename,
            member_type,
            backslash_is_separator=True,
        )
        raw_name = record.filename.encode("utf-16le", errors="surrogateescape")
        compression = tuple(
            method
            for method in (
                compression_method_for_coder(coder)
                for coder in self._folder_coders(record)
                if coder.method != _METHOD_AES
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
        member = ArchiveMember(
            type=member_type,
            name=name,
            raw_name=raw_name,
            size=record.uncompressed_size,
            compressed_size=record.compressed_size,
            modified=_filetime_to_datetime(record.last_write_time),
            accessed=_filetime_to_datetime(record.last_access_time),
            created=_filetime_to_datetime(record.creation_time),
            mode=mode,
            compression=compression,
            is_encrypted=record.is_encrypted,
            is_anti=record.is_anti,
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
            presented_name=record.filename,
            archive_name=self._archive_name,
        )
        return member

    def _member_type(self, record: SevenZipFileRecord) -> MemberType:
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
        self, folder_index: int, member: ArchiveMember | None
    ) -> BinaryIO:
        folder = self._archive.folders[folder_index]
        password = self._password_for_folder(folder_index, member)
        return self._open_folder_pipeline(
            self._folder_pack_view(folder_index),
            folder,
            password=password,
        )

    def _password_for_folder(
        self, folder_index: int, member: ArchiveMember | None
    ) -> bytes | None:
        folder = self._archive.folders[folder_index]
        if not folder_is_encrypted(folder):
            return None
        if folder_index in self._folder_passwords:
            return self._folder_passwords[folder_index]

        def confirm(password: bytes) -> bytes:
            kdf_password = _password_to_kdf_bytes(password)
            stream = self._open_folder_pipeline(
                self._folder_pack_view(folder_index),
                folder,
                password=kdf_password,
            )
            try:
                total = self._folder_unpack_size(folder_index)
                decoded = read_exact(stream, total)
                if len(decoded) != total:
                    raise EncryptionError("Wrong password or corrupt 7z folder")
                if folder.digest_defined:
                    verifier = VerifyingStream(
                        io.BytesIO(decoded),
                        {"crc32": folder.crc if folder.crc is not None else 0},
                        collector=self._diagnostics_collector,
                        archive_name=self._archive_name,
                    )
                    verifier.read()
                    verifier.read()
                return kdf_password
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

    def _open_folder_pipeline(
        self,
        source: BinaryIO,
        folder: SevenZipFolder,
        *,
        password: bytes | None,
    ) -> BinaryIO:
        if any(
            coder.num_in_streams != 1 or coder.num_out_streams != 1
            for coder in folder.coders
        ):
            raise UnsupportedFeatureError(
                "7z folders with complex coder graphs are not supported"
            )
        stream: BinaryIO = source
        index = 0
        while index < len(folder.coders):
            coder = folder.coders[index]
            if coder.method == _METHOD_BCJ2:
                raise UnsupportedFeatureError(
                    "BCJ2-compressed 7z folders are not supported"
                )
            if coder.method == _METHOD_COPY:
                index += 1
                continue
            if coder.method == _METHOD_AES:
                stream = self._open_aes_stage(stream, coder, password=password)
                index += 1
                continue
            if _is_lzma_family(coder):
                run: list[SevenZipCoder] = []
                while index < len(folder.coders) and _is_lzma_family(
                    folder.coders[index]
                ):
                    run.append(folder.coders[index])
                    index += 1
                stream = self._open_lzma_run(stream, run)
                continue
            codec = _SINGLE_STAGE_CODECS.get(coder.method)
            if codec is None:
                raise UnsupportedFeatureError(
                    f"Unsupported 7z coder method {_method_hex(coder.method)}"
                )
            stream = open_codec_stream(
                codec,
                stream,
                config=self._stream_config,
                params=CodecParams(properties=coder.properties),
                collector=self._diagnostics_collector,
                seekable=False,
            )
            index += 1
        return stream

    def _open_aes_stage(
        self,
        source: BinaryIO,
        coder: SevenZipCoder,
        *,
        password: bytes | None,
    ) -> BinaryIO:
        if password is None:
            raise EncryptionError("Password required to decrypt this 7z folder")
        if coder.properties is None:
            raise CorruptionError("7z AES coder is missing properties")
        try:
            params = self._key_cache.aes_params_from_properties(
                password, coder.properties
            )
        except ValueError as exc:
            raise CorruptionError(f"Malformed 7z AES properties: {exc}") from exc
        return open_aes_decrypt_stream(source, params)

    def _open_lzma_run(self, source: BinaryIO, run: list[SevenZipCoder]) -> BinaryIO:
        has_lzma1 = any(coder.method == _METHOD_LZMA for coder in run)
        has_lzma2 = any(coder.method == _METHOD_LZMA2 for coder in run)
        has_bcj = any(coder.method in _BCJ_METHODS for coder in run)
        if has_lzma1 and has_lzma2:
            raise UnsupportedFeatureError(
                "Mixed LZMA1+LZMA2 7z coder chains are unsupported"
            )
        if has_lzma1 and has_bcj:
            raise UnsupportedFeatureError("LZMA1+BCJ 7z coder chains are unsupported")
        filters = [_lzma_filter(coder) for coder in reversed(run)]
        codec = Codec.LZMA if has_lzma1 and not has_lzma2 else Codec.LZMA2
        return open_codec_stream(
            codec,
            source,
            config=self._stream_config,
            params=CodecParams(filters=filters),
            collector=self._diagnostics_collector,
            seekable=False,
        )

    def _member_stream_from_folder(
        self,
        folder_stream: BinaryIO,
        member: ArchiveMember,
        *,
        close_folder: bool = False,
    ) -> ArchiveStream:
        limited: BinaryIO = _LimitedFolderReader(
            folder_stream,
            _member_stream_size(member),
            close_source=close_folder,
        )
        if member.hashes:
            limited = VerifyingStream(
                limited,
                member.hashes,
                collector=self._diagnostics_collector,
                member=member,
                archive_name=self._archive_name,
            )
        return self._wrap_member_stream(limited, member.name, size=member.size)

    def _skip_folder_prefix(
        self, folder_stream: BinaryIO, member: ArchiveMember
    ) -> None:
        raw = member._raw
        assert isinstance(raw, _MemberRaw)
        if raw.folder_index is None or raw.file_in_folder is None:
            return
        offset = 0
        for prior in self._folder_members.get(raw.folder_index, [])[
            : raw.file_in_folder
        ]:
            offset += _member_stream_size(prior)
        while offset:
            chunk = folder_stream.read(min(offset, 1024 * 1024))
            if not chunk:
                raise TruncatedError("7z folder ended before the requested member")
            offset -= len(chunk)

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
        if member.is_anti or raw.folder_index is None:
            return self._wrap_member_stream(
                io.BytesIO(b""), member.name, size=member.size
            )
        folder_stream = self._open_folder_stream(raw.folder_index, member)
        try:
            self._skip_folder_prefix(folder_stream, member)
            return self._member_stream_from_folder(
                folder_stream, member, close_folder=True
            )
        except Exception:
            folder_stream.close()
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
