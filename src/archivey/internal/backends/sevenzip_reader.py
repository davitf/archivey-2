"""Native 7z reader backend."""

from __future__ import annotations

import io
import lzma
import stat
import zlib
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO

from archivey.config import ArchiveyConfig
from archivey.cost import AccessCost, CostReceipt, ListingCost, StreamCapability
from archivey.diagnostics import DiagnosticCode, MemberTimestampContext
from archivey.exceptions import (
    ArchiveyError,
    CorruptionError,
    EncryptionError,
    PackageNotInstalledError,
    StreamNotSeekableError,
    TruncatedError,
    UnsupportedFeatureError,
)
from archivey.internal.backends.sevenzip_parser import (
    _METHOD_AES,
    _METHOD_BCJ2,
    _METHOD_BROTLI,
    _METHOD_BZIP2,
    _METHOD_COPY,
    _METHOD_DEFLATE,
    _METHOD_DEFLATE64,
    _METHOD_DELTA,
    _METHOD_LZMA,
    _METHOD_LZMA2,
    _METHOD_PPMD,
    _METHOD_ZSTD,
    SevenZipArchive,
    SevenZipCoder,
    SevenZipFileRecord,
    SevenZipFolder,
    _method_hex,
    compression_method_for_coder,
    compute_is_current,
    folder_is_encrypted,
    parse_sevenzip_archive,
)
from archivey.internal.base_reader import BaseArchiveReader, ReadBackend
from archivey.internal.config import (
    DEFAULT_STREAM_CONFIG,
    StreamConfig,
    stream_config_from_archivey,
)
from archivey.internal.diagnostics_collector import DiagnosticCollector
from archivey.internal.logs import backends as logger
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
from archivey.internal.streams.decompress import BcjFilterStream
from archivey.internal.streams.streamtools import (
    ReadOnlyIOStream,
    SharedSource,
    SlicingStream,
    SolidBlockReader,
    is_seekable,
    is_stream,
    read_exact,
    skip_forward,
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

# pybcj (import name ``bcj``) decoder class attribute per BCJ method ID. Used only for
# LZMA1+BCJ staging — LZMA2+BCJ stays on stdlib liblzma filters.
_BCJ_PYBCJ_DECODERS: dict[bytes, str] = {
    b"\x04": "BCJDecoder",
    b"\x03\x03\x01\x03": "BCJDecoder",
    b"\x05": "PPCDecoder",
    b"\x03\x03\x02\x05": "PPCDecoder",
    b"\x06": "IA64Decoder",
    b"\x03\x03\x04\x01": "IA64Decoder",
    b"\x07": "ARMDecoder",
    b"\x03\x03\x05\x01": "ARMDecoder",
    b"\x08": "ARMTDecoder",
    b"\x03\x03\x07\x01": "ARMTDecoder",
    b"\x09": "SparcDecoder",
    b"\x03\x03\x08\x05": "SparcDecoder",
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


def _password_to_kdf_bytes(password: bytes) -> bytes:
    try:
        return password.decode("utf-8").encode("utf-16le")
    except UnicodeDecodeError:
        return password


@dataclass(frozen=True)
class _TimestampIssue:
    field: str
    value_repr: str
    message: str


def _filetime_to_datetime(
    value: int | None, filename: str, *, field: str
) -> tuple[datetime | None, _TimestampIssue | None]:
    """An NTFS FILETIME (100 ns ticks since 1601 UTC) as a datetime; 0/None means "unset"."""
    if value is None or value == 0:
        return None, None
    try:
        return (
            datetime.fromtimestamp(
                value / 10_000_000 - _NTFS_EPOCH_OFFSET, tz=timezone.utc
            ),
            None,
        )
    except (ValueError, OverflowError, OSError):
        # fromtimestamp rejects out-of-range values with ValueError/OverflowError, and on
        # some platforms (notably Windows) with OSError for negative/huge inputs.
        return None, _TimestampIssue(
            field=field,
            value_repr=repr(value),
            message=f"Invalid NTFS timestamp for {filename!r}: {value!r}",
        )


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


def _check_linear_coder_chain(folder: SevenZipFolder) -> None:
    """Verify the folder's coders form a single linear chain in list order.

    ``open_folder_pipeline`` decodes coders in list order, assuming coder 0 consumes the
    single packed stream and each coder's output feeds the next coder's input. 7z encoders
    emit coders in exactly that order (verified against py7zr fixtures: LZMA2, Delta+LZMA2,
    BCJ+LZMA2, AES+LZMA2 all yield ``packed_indices == [0]`` and ``bind_pairs`` of the form
    ``(i+1, i)``). That wiring is only *implied* by the bind pairs, though, so we validate
    it here and raise rather than silently emit wrong bytes for an out-of-order or branching
    coder graph.
    """
    expected_bind = {(i + 1, i) for i in range(len(folder.coders) - 1)}
    if folder.packed_indices != [0] or set(folder.bind_pairs) != expected_bind:
        raise UnsupportedFeatureError(
            "7z folders with non-linear coder wiring are not supported"
        )


def _open_aes_stage(
    source: BinaryIO,
    coder: SevenZipCoder,
    *,
    password: bytes | None,
    key_cache: SevenZipKeyCache,
) -> BinaryIO:
    if password is None:
        raise EncryptionError("Password required to decrypt this 7z folder")
    if coder.properties is None:
        raise CorruptionError("7z AES coder is missing properties")
    try:
        params = key_cache.aes_params_from_properties(password, coder.properties)
    except ValueError as exc:
        raise CorruptionError(f"Malformed 7z AES properties: {exc}") from exc
    return open_aes_decrypt_stream(source, params)


def _open_lzma_combined(
    source: BinaryIO,
    run: list[SevenZipCoder],
    *,
    stream_config: StreamConfig,
    collector: DiagnosticCollector | None,
    seekable: bool,
) -> BinaryIO:
    has_lzma1 = any(coder.method == _METHOD_LZMA for coder in run)
    has_lzma2 = any(coder.method == _METHOD_LZMA2 for coder in run)
    # A 7z filter run lists coders in decode order (outer filter first); liblzma wants
    # them in the reverse (encode) order, hence ``reversed(run)``.
    filters = [_lzma_filter(coder) for coder in reversed(run)]
    codec = Codec.LZMA if has_lzma1 and not has_lzma2 else Codec.LZMA2
    return open_codec_stream(
        codec,
        source,
        config=stream_config,
        params=CodecParams(filters=filters),
        collector=collector,
        seekable=seekable,
    )


def _require_pybcj() -> None:
    try:
        import bcj  # noqa: F401
    except ImportError as exc:
        raise PackageNotInstalledError(
            "The 'pybcj' package is required for LZMA1+BCJ 7z folders "
            "(install the '7z' extra)."
        ) from exc


class _BoundedReadStream(ReadOnlyIOStream):
    """Return at most ``size`` bytes from ``inner``, then EOF without further reads.

    LZMA1 streams from the 7-Zip CLI often lack an end-of-stream marker. ``lzma.LZMAFile``
    then raises ``EOFError`` on any read past the known unpack size; capping reads at that
    size lets the staged BCJ filter finish cleanly.
    """

    def __init__(self, inner: BinaryIO, size: int) -> None:
        super().__init__()
        self._inner = inner
        self._remaining = size

    def read(self, n: int = -1, /) -> bytes:
        if self._remaining <= 0:
            return b""
        if n is None or n < 0:
            n = self._remaining
        else:
            n = min(n, self._remaining)
        data = self._inner.read(n)
        self._remaining -= len(data)
        return data

    def close(self) -> None:
        self._inner.close()
        super().close()


def _open_bcj_stage(
    source: BinaryIO,
    coder: SevenZipCoder,
    *,
    unpack_size: int,
    seekable: bool,
) -> BinaryIO:
    decoder_attr = _BCJ_PYBCJ_DECODERS.get(coder.method)
    if decoder_attr is None:
        raise UnsupportedFeatureError(
            f"Unsupported 7z BCJ coder {_method_hex(coder.method)}"
        )
    return BcjFilterStream(
        source,
        decoder_attr=decoder_attr,
        unpack_size=unpack_size,
        seekable=seekable,
    )


def _open_lzma_run(
    source: BinaryIO,
    run: list[SevenZipCoder],
    unpack_sizes: list[int],
    *,
    stream_config: StreamConfig,
    collector: DiagnosticCollector | None,
    seekable: bool,
) -> BinaryIO:
    if len(run) != len(unpack_sizes):
        raise CorruptionError("7z LZMA-family run length does not match unpack sizes")
    has_lzma1 = any(coder.method == _METHOD_LZMA for coder in run)
    has_lzma2 = any(coder.method == _METHOD_LZMA2 for coder in run)
    has_bcj = any(coder.method in _BCJ_METHODS for coder in run)
    if has_lzma1 and has_lzma2:
        raise UnsupportedFeatureError(
            "Mixed LZMA1+LZMA2 7z coder chains are unsupported"
        )
    if has_lzma1 and has_bcj:
        # liblzma can silently truncate BCJ look-ahead when LZMA1 lacks EOS (BPO-21872 /
        # xz-devel guidance). Stage like py7zr: stdlib LZMA1 (+ Delta, etc.), then pybcj.
        _require_pybcj()
        stream: BinaryIO = source
        index = 0
        while index < len(run):
            coder = run[index]
            if coder.method in _BCJ_METHODS:
                stream = _open_bcj_stage(
                    stream,
                    coder,
                    unpack_size=unpack_sizes[index],
                    seekable=seekable,
                )
                index += 1
                continue
            sub_run: list[SevenZipCoder] = []
            while index < len(run) and run[index].method not in _BCJ_METHODS:
                sub_run.append(run[index])
                index += 1
            stream = _open_lzma_combined(
                stream,
                sub_run,
                stream_config=stream_config,
                collector=collector,
                seekable=seekable,
            )
            # Cap at the sub-run output size so LZMA1-without-EOS does not raise on
            # a trailing read when the BCJ stage asks for a large chunk.
            stream = _BoundedReadStream(stream, unpack_sizes[index - 1])
            continue
        return stream
    return _open_lzma_combined(
        source,
        run,
        stream_config=stream_config,
        collector=collector,
        seekable=seekable,
    )


def open_folder_pipeline(
    source: BinaryIO,
    folder: SevenZipFolder,
    *,
    password: bytes | None,
    key_cache: SevenZipKeyCache,
    stream_config: StreamConfig | None = None,
    collector: DiagnosticCollector | None = None,
    seekable: bool = False,
) -> BinaryIO:
    """Compose a folder's coder chain into a single pull stream.

    Only linear 1-in/1-out coder chains are supported; BCJ2 and multi-stream coder
    graphs raise ``UnsupportedFeatureError``. Consecutive LZMA-family coders (LZMA,
    LZMA2, Delta, BCJ) collapse into one liblzma filter chain; other codecs and the AES
    stage each wrap the stream once.

    ``seekable`` requests a seekable decode (backward seeks re-decode from the folder
    start, O(n)); it is the ArchiveStream seekability hint for the codec streams. Note an
    AES stage yields a non-seekable stream regardless, so encrypted folders stay
    forward-only — the caller checks ``is_seekable`` on the result.
    """
    if any(
        coder.num_in_streams != 1 or coder.num_out_streams != 1
        for coder in folder.coders
    ):
        raise UnsupportedFeatureError(
            "7z folders with complex coder graphs are not supported"
        )
    _check_linear_coder_chain(folder)
    config = stream_config if stream_config is not None else DEFAULT_STREAM_CONFIG
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
            stream = _open_aes_stage(
                stream, coder, password=password, key_cache=key_cache
            )
            index += 1
            continue
        if _is_lzma_family(coder):
            run: list[SevenZipCoder] = []
            run_sizes: list[int] = []
            while index < len(folder.coders) and _is_lzma_family(folder.coders[index]):
                run.append(folder.coders[index])
                run_sizes.append(folder.unpack_sizes[index])
                index += 1
            stream = _open_lzma_run(
                stream,
                run,
                run_sizes,
                stream_config=config,
                collector=collector,
                seekable=seekable,
            )
            continue
        codec = _SINGLE_STAGE_CODECS.get(coder.method)
        if codec is None:
            raise UnsupportedFeatureError(
                f"Unsupported 7z coder method {_method_hex(coder.method)}"
            )
        stream = open_codec_stream(
            codec,
            stream,
            config=config,
            params=CodecParams(properties=coder.properties),
            collector=collector,
            seekable=seekable,
        )
        index += 1
    return stream


def decode_folder_to_bytes(
    source: BinaryIO,
    folder: SevenZipFolder,
    *,
    compressed_size: int,
    uncompressed_size: int,
    password: bytes | None,
    key_cache: SevenZipKeyCache,
    stream_config: StreamConfig | None = None,
    collector: DiagnosticCollector | None = None,
) -> bytes:
    """Fully decode one folder's packed stream into memory and CRC-check it.

    Used to materialize the (encoded) 7z header. ``source`` must be positioned at the
    start of the folder's packed data.
    """
    stream = open_folder_pipeline(
        SlicingStream(source, 0, compressed_size),
        folder,
        password=password,
        key_cache=key_cache,
        stream_config=stream_config,
        collector=collector,
    )
    try:
        decoded = read_exact(stream, uncompressed_size)
        if len(decoded) != uncompressed_size:
            raise TruncatedError("7z folder is truncated after decoding")
        if folder.digest_defined and folder.crc is not None:
            if zlib.crc32(decoded) & 0xFFFFFFFF != folder.crc & 0xFFFFFFFF:
                raise CorruptionError("Decoded 7z folder CRC mismatch")
        return decoded
    finally:
        stream.close()


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
    ) -> bytes:
        def decode(password: bytes | None) -> bytes:
            source.seek(0)
            return decode_folder_to_bytes(
                source,
                folder,
                compressed_size=compressed_size,
                uncompressed_size=uncompressed_size,
                password=password,
                key_cache=self._key_cache,
                stream_config=self._stream_config,
                collector=self._diagnostics_collector,
            )

        try:
            if folder_is_encrypted(folder):
                return self._passwords.attempt(
                    None, lambda password: decode(_password_to_kdf_bytes(password))
                )
            return decode(None)
        except _PasswordCandidatesExhausted as exc:
            raise EncryptionError("Password required to decrypt the 7z header") from exc

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
        # Solid folders decode once into a single forward-only stream; SolidBlockReader
        # slices it into consecutive members and skips a prior member's unread tail
        # lazily when the next member is opened (see streamtools/solid.py).
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
                    solid = SolidBlockReader(
                        self._open_folder_stream(raw.folder_index, member)
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
        ts_issues: list[_TimestampIssue] = []
        timestamps: dict[str, datetime | None] = {}
        for field, value in (
            ("modified", record.last_write_time),
            ("accessed", record.last_access_time),
            ("created", record.creation_time),
        ):
            timestamps[field], issue = _filetime_to_datetime(
                value, record.filename, field=field
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
            presented_name=record.filename,
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
    ) -> BinaryIO:
        folder = self._archive.folders[folder_index]
        password = self._password_for_folder(folder_index, member)
        return self._open_folder_pipeline(
            self._folder_pack_view(folder_index),
            folder,
            password=password,
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
                # LZMA can occasionally emit the expected length for a wrong key;
                # require a CRC match before accepting the candidate. Prefer the
                # folder digest when present; otherwise check per-member digests.
                self._verify_folder_plaintext(folder_index, decoded)
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

    def _verify_folder_plaintext(self, folder_index: int, decoded: bytes) -> None:
        """Raise ``EncryptionError`` when decoded folder bytes fail CRC checks."""
        folder = self._archive.folders[folder_index]
        if folder.digest_defined:
            expected = (folder.crc if folder.crc is not None else 0) & 0xFFFFFFFF
            if zlib.crc32(decoded) & 0xFFFFFFFF != expected:
                raise EncryptionError("Wrong password or corrupt 7z folder")
            return

        offset = 0
        for folder_member in self._folder_members.get(folder_index, []):
            size = _member_stream_size(folder_member)
            chunk = decoded[offset : offset + size]
            offset += size
            raw_expected = (
                folder_member.hashes.get("crc32") if folder_member.hashes else None
            )
            if raw_expected is None:
                continue
            if isinstance(raw_expected, bytes):
                expected = int.from_bytes(raw_expected, "big") & 0xFFFFFFFF
            else:
                expected = raw_expected & 0xFFFFFFFF
            if zlib.crc32(chunk) & 0xFFFFFFFF != expected:
                raise EncryptionError("Wrong password or corrupt 7z folder")
        # When no digests are present we can only rely on the decompressor rejecting
        # wrong-key ciphertext (same as before); do not treat that as confirmation
        # failure on its own.

    def _open_folder_pipeline(
        self,
        source: BinaryIO,
        folder: SevenZipFolder,
        *,
        password: bytes | None,
        seekable: bool = False,
    ) -> BinaryIO:
        return open_folder_pipeline(
            source,
            folder,
            password=password,
            key_cache=self._key_cache,
            stream_config=self._stream_config,
            collector=self._diagnostics_collector,
            seekable=seekable,
        )

    def _member_prefix(self, member: ArchiveMember) -> int:
        """Cumulative decoded size of the members preceding ``member`` in its folder."""
        raw = member._raw
        assert isinstance(raw, _MemberRaw)
        if raw.folder_index is None or raw.file_in_folder is None:
            return 0
        prior = self._folder_members.get(raw.folder_index, [])[: raw.file_in_folder]
        return sum(_member_stream_size(p) for p in prior)

    def _wrap_folder_member(
        self, inner: BinaryIO, member: ArchiveMember
    ) -> ArchiveStream:
        if member.hashes:
            inner = VerifyingStream(
                inner,
                member.hashes,
                collector=self._diagnostics_collector,
                member=member,
                archive_name=self._archive_name,
            )
        return self._wrap_member_stream(inner, member.name, size=member.size)

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
        # Directories/anti/other never reach here: BaseArchiveReader rejects them.
        # Empty FILE members (no folder stream) still present an empty payload.
        if raw.folder_index is None:
            return self._wrap_member_stream(
                io.BytesIO(b""), member.name, size=member.size
            )
        # Random access re-decodes the folder from its start and slices out the member. The
        # SlicingStream owns its private decoder (``own_source``) so closing the member stream
        # closes the decoder. With MemberStreams.SEEKABLE and a seekable codec chain, hand back
        # a seekable slice positioned at the member (backward seeks re-decode from the folder
        # start, O(n)); otherwise skip forward and return a forward-only slice. VerifyingStream
        # still checks the CRC on a pure forward read and disables itself once the caller seeks.
        want_seekable = self._stream_config.seekable
        prefix = self._member_prefix(member)
        size = _member_stream_size(member)
        folder_stream = self._open_folder_stream(
            raw.folder_index, member, seekable=want_seekable
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
