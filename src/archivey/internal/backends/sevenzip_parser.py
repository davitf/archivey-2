"""Native 7z header parser.

This module reads the 7z signature/end header and turns the main header into small
data objects the future reader can use to build members and folder streams. It does
not import or delegate to ``py7zr``.
"""

from __future__ import annotations

import io
import struct
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from enum import IntEnum
from typing import BinaryIO

from archivey.exceptions import (
    CorruptionError,
    UnsupportedFeatureError,
)
from archivey.internal.streams.streamtools import SlicingStream, read_exact
from archivey.types import CompressionAlgorithm, CompressionMethod

MAGIC_7Z = b"7z\xbc\xaf'\x1c"

_SIGNATURE_HEADER_SIZE = 32
_MAX_UINT64_ENCODING = 8
_MAX_UTF16_CHARS = 65536
_MAX_NUM_STREAMS = 65536
# Hostile archives can claim a multi-EiB next-header offset/size. Cap before seek/read so we
# never OverflowError on C ssize_t conversion or allocate a multi-GiB header buffer. Real 7z
# headers are kilobytes; tens of MiB is already far past any legitimate archive.
_MAX_NEXT_HEADER_SIZE = 64 << 20
_MAX_SEEK_OFFSET = (1 << 63) - 1 - _SIGNATURE_HEADER_SIZE
# 7z coder method IDs. Single source of truth: the reader imports these.
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
_METHOD_LZ4 = b"\x04\xf7\x11\x04"
_METHOD_PPMD = b"\x03\x04\x01"


class _Property(IntEnum):
    END = 0x00
    HEADER = 0x01
    ARCHIVE_PROPERTIES = 0x02
    ADDITIONAL_STREAMS_INFO = 0x03
    MAIN_STREAMS_INFO = 0x04
    FILES_INFO = 0x05
    PACK_INFO = 0x06
    UNPACK_INFO = 0x07
    SUBSTREAMS_INFO = 0x08
    SIZE = 0x09
    CRC = 0x0A
    FOLDER = 0x0B
    CODERS_UNPACK_SIZE = 0x0C
    NUM_UNPACK_STREAM = 0x0D
    EMPTY_STREAM = 0x0E
    EMPTY_FILE = 0x0F
    ANTI = 0x10
    NAME = 0x11
    CREATION_TIME = 0x12
    LAST_ACCESS_TIME = 0x13
    LAST_WRITE_TIME = 0x14
    ATTRIBUTES = 0x15
    COMMENT = 0x16
    ENCODED_HEADER = 0x17
    START_POS = 0x18
    DUMMY = 0x19


@dataclass
class SevenZipCoder:
    method: bytes
    num_in_streams: int
    num_out_streams: int
    properties: bytes | None


@dataclass
class SevenZipFolder:
    coders: list[SevenZipCoder]
    bind_pairs: list[tuple[int, int]]
    packed_indices: list[int]
    unpack_sizes: list[int]
    crc: int | None
    digest_defined: bool


@dataclass
class SevenZipFileRecord:
    filename: str
    emptystream: bool
    is_anti: bool
    is_directory: bool
    is_empty_file: bool
    attributes: int | None
    creation_time: int | None
    last_access_time: int | None
    last_write_time: int | None
    folder_index: int | None
    file_in_folder: int | None
    uncompressed_size: int
    crc32: int | None
    compressed_size: int | None
    is_encrypted: bool
    is_solid: bool
    compression_methods: tuple[bytes | CompressionMethod, ...]


@dataclass
class SevenZipArchive:
    major_version: int
    minor_version: int
    pack_pos: int
    pack_sizes: list[int]
    pack_positions: list[int]
    folders: list[SevenZipFolder]
    num_unpackstreams_folders: list[int]
    unpack_sizes: list[int]
    digests: list[int | None]
    files: list[SevenZipFileRecord]
    comment: str | None
    is_solid: bool
    is_header_encrypted: bool
    has_encrypted_folders: bool


DecodeFolder = Callable[[BinaryIO, SevenZipFolder, int, int], bytes]
"""Materialize an (encoded) header folder to bytes.

``(source, folder, compressed_size, uncompressed_size) -> decoded``, where ``source`` is
positioned at the start of the folder's packed data. The reader supplies the shared
codec/crypto pipeline (``sevenzip_reader.decode_folder_to_bytes``); the parser never
decodes folders itself."""


@dataclass
class _PackInfo:
    pack_pos: int = 0
    pack_sizes: list[int] | None = None
    pack_positions: list[int] | None = None


@dataclass
class _StreamsInfo:
    pack_pos: int = 0
    pack_sizes: list[int] | None = None
    pack_positions: list[int] | None = None
    folders: list[SevenZipFolder] | None = None
    num_unpackstreams_folders: list[int] | None = None
    unpack_sizes: list[int] | None = None
    digests: list[int | None] | None = None


@dataclass
class _ParsedHeader:
    streams: _StreamsInfo
    files: list[SevenZipFileRecord]
    comment: str | None
    is_header_encrypted: bool


@dataclass
class _FileProps:
    filename: str = ""
    emptystream: bool = False
    is_anti: bool = False
    is_empty_file: bool = False
    attributes: int | None = None
    creation_time: int | None = None
    last_access_time: int | None = None
    last_write_time: int | None = None


def parse_sevenzip_archive(
    fp: BinaryIO,
    *,
    decode_folder: DecodeFolder,
) -> SevenZipArchive:
    """Parse a 7z signature header and main header.

    ``decode_folder`` materializes an encoded header's folder to bytes (including any
    password prompting/decryption); the parser only walks structure and never opens a
    codec or crypto stream itself.
    """

    fp.seek(0)
    signature = _read_exact(fp, _SIGNATURE_HEADER_SIZE, "7z signature header")
    if signature[: len(MAGIC_7Z)] != MAGIC_7Z:
        raise CorruptionError("Not a 7z archive: bad magic bytes")

    major_version = signature[6]
    minor_version = signature[7]
    start_header_crc = int.from_bytes(signature[8:12], "little")
    start_header = signature[12:32]
    if _crc32(start_header) != start_header_crc:
        raise CorruptionError("7z signature header CRC mismatch")

    # start_header is 20 bytes: nextHeaderOffset (u64), nextHeaderSize (u64), CRC (u32).
    next_header_offset, next_header_size, next_header_crc = struct.unpack(
        "<QQI", start_header
    )
    if next_header_offset > _MAX_SEEK_OFFSET:
        raise CorruptionError(
            f"7z next-header offset {next_header_offset} exceeds the seekable range"
        )
    if next_header_size > _MAX_NEXT_HEADER_SIZE:
        raise CorruptionError(
            f"7z next-header size {next_header_size} exceeds the "
            f"{_MAX_NEXT_HEADER_SIZE}-byte parser limit"
        )

    # Empty archive: nextHeaderSize == 0 (and typically offset/CRC are also 0).
    # py7zr and the 7z CLI open these as zero-member archives.
    if next_header_size == 0:
        if next_header_crc != 0 and next_header_crc != _crc32(b""):
            raise CorruptionError("7z empty next-header CRC mismatch")
        return SevenZipArchive(
            major_version=major_version,
            minor_version=minor_version,
            pack_pos=_SIGNATURE_HEADER_SIZE,
            pack_sizes=[],
            pack_positions=[],
            folders=[],
            num_unpackstreams_folders=[],
            unpack_sizes=[],
            digests=[],
            files=[],
            comment=None,
            is_solid=False,
            is_header_encrypted=False,
            has_encrypted_folders=False,
        )

    # Offsets in the header are relative to the end of the 32-byte signature header.
    try:
        fp.seek(_SIGNATURE_HEADER_SIZE + next_header_offset)
    except (OSError, OverflowError) as exc:
        raise CorruptionError(
            f"7z next-header seek failed at offset {next_header_offset}"
        ) from exc
    header_data = _read_exact(fp, next_header_size, "7z next header")
    if _crc32(header_data) != next_header_crc:
        raise CorruptionError("7z next header CRC mismatch")

    parsed = _parse_header(
        fp,
        io.BytesIO(header_data),
        decode_folder=decode_folder,
    )

    streams = parsed.streams
    pack_sizes = streams.pack_sizes or []
    pack_positions = streams.pack_positions or _pack_positions(pack_sizes)
    folders = streams.folders or []
    num_unpackstreams_folders = streams.num_unpackstreams_folders or []
    unpack_sizes = streams.unpack_sizes or []
    digests = streams.digests or []
    pack_pos = _SIGNATURE_HEADER_SIZE + streams.pack_pos

    files = _map_files_to_folders(
        parsed.files,
        folders=folders,
        pack_sizes=pack_sizes,
        num_unpackstreams_folders=num_unpackstreams_folders,
        unpack_sizes=unpack_sizes,
        digests=digests,
    )

    return SevenZipArchive(
        major_version=major_version,
        minor_version=minor_version,
        pack_pos=pack_pos,
        pack_sizes=pack_sizes,
        pack_positions=pack_positions,
        folders=folders,
        num_unpackstreams_folders=num_unpackstreams_folders,
        unpack_sizes=unpack_sizes,
        digests=digests,
        files=files,
        comment=parsed.comment,
        is_solid=any(n > 1 for n in num_unpackstreams_folders),
        is_header_encrypted=parsed.is_header_encrypted,
        has_encrypted_folders=any(folder_is_encrypted(folder) for folder in folders),
    )


def compute_is_current(files: list[SevenZipFileRecord]) -> list[bool]:
    """Last-entry-wins by filename."""

    current = [False] * len(files)
    seen: set[str] = set()
    for index in range(len(files) - 1, -1, -1):
        filename = files[index].filename
        if filename not in seen:
            current[index] = True
            seen.add(filename)
    return current


def folder_is_encrypted(folder: SevenZipFolder) -> bool:
    """Whether any coder in the folder is 7z AES."""

    return any(coder.method == _METHOD_AES for coder in folder.coders)


def compression_method_for_coder(
    coder: SevenZipCoder,
) -> CompressionMethod:
    """Return archivey's compression descriptor for a 7z coder method id."""

    return CompressionMethod(
        _METHOD_ALGORITHMS.get(coder.method, CompressionAlgorithm.UNKNOWN),
        properties=coder.properties,
    )


def _parse_header(
    archive_fp: BinaryIO,
    buffer: BinaryIO,
    *,
    decode_folder: DecodeFolder,
) -> _ParsedHeader:
    prop = _read_property(buffer, "7z header")
    if prop == _Property.END:
        return _ParsedHeader(_StreamsInfo(), [], None, False)
    if prop == _Property.HEADER:
        parsed = _parse_plain_header(buffer)
        parsed.is_header_encrypted = False
        return parsed
    if prop != _Property.ENCODED_HEADER:
        raise CorruptionError(f"Expected 7z HEADER or ENCODED_HEADER, got 0x{prop:02x}")

    encoded_streams = _read_streams_info(buffer)
    encoded_folders = encoded_streams.folders or []
    decoded = _decode_encoded_header(
        archive_fp,
        encoded_streams,
        decode_folder=decode_folder,
    )
    parsed = _parse_header(
        archive_fp,
        io.BytesIO(decoded),
        decode_folder=decode_folder,
    )
    parsed.is_header_encrypted = any(folder_is_encrypted(f) for f in encoded_folders)
    return parsed


def _parse_plain_header(buffer: BinaryIO) -> _ParsedHeader:
    streams = _StreamsInfo()
    files: list[SevenZipFileRecord] = []
    comment: str | None = None

    while True:
        prop = _read_property(buffer, "7z plain header")
        if prop == _Property.END:
            return _ParsedHeader(streams, files, comment, False)
        if prop == _Property.ARCHIVE_PROPERTIES:
            _skip_archive_properties(buffer)
        elif prop == _Property.ADDITIONAL_STREAMS_INFO:
            _skip_streams_info(buffer)
        elif prop == _Property.MAIN_STREAMS_INFO:
            streams = _read_streams_info(buffer)
        elif prop == _Property.FILES_INFO:
            files, file_comment = _read_files_info(buffer)
            if file_comment is not None:
                comment = file_comment
        else:
            raise UnsupportedFeatureError(
                f"Unsupported 7z header property 0x{prop:02x}"
            )


def _read_streams_info(buffer: BinaryIO) -> _StreamsInfo:
    streams = _StreamsInfo()
    prop = _read_property(buffer, "7z streams info")

    if prop == _Property.PACK_INFO:
        pack_info = _read_pack_info(buffer)
        streams.pack_pos = pack_info.pack_pos
        streams.pack_sizes = pack_info.pack_sizes
        streams.pack_positions = pack_info.pack_positions
        prop = _read_property(buffer, "7z streams info")

    if prop == _Property.UNPACK_INFO:
        streams.folders = _read_unpack_info(buffer)
        prop = _read_property(buffer, "7z streams info")

    if prop == _Property.SUBSTREAMS_INFO:
        if streams.folders is None:
            raise CorruptionError("7z SUBSTREAMS_INFO appeared before UNPACK_INFO")
        (
            streams.num_unpackstreams_folders,
            streams.unpack_sizes,
            streams.digests,
        ) = _read_substreams_info(buffer, streams.folders)
        prop = _read_property(buffer, "7z streams info")
    elif streams.folders is not None:
        streams.num_unpackstreams_folders = [1] * len(streams.folders)
        streams.unpack_sizes = [
            _folder_unpack_size(folder) for folder in streams.folders
        ]
        streams.digests = [
            folder.crc if folder.digest_defined else None for folder in streams.folders
        ]

    if prop != _Property.END:
        raise CorruptionError(f"Expected END in 7z streams info, got 0x{prop:02x}")
    return streams


def _read_pack_info(buffer: BinaryIO) -> _PackInfo:
    pack_pos = _read_uint64(buffer)
    num_streams = _read_uint64(buffer)
    if num_streams > _MAX_NUM_STREAMS:
        raise CorruptionError(f"7z pack stream count is too large: {num_streams}")

    pack_sizes: list[int] | None = None
    prop = _read_property(buffer, "7z PACK_INFO")
    if prop == _Property.SIZE:
        pack_sizes = [_read_uint64(buffer) for _ in range(num_streams)]
        prop = _read_property(buffer, "7z PACK_INFO")
    if prop == _Property.CRC:
        _read_digests(buffer, num_streams)
        prop = _read_property(buffer, "7z PACK_INFO")
    if prop != _Property.END:
        raise CorruptionError(f"Expected END in 7z PACK_INFO, got 0x{prop:02x}")

    if pack_sizes is None:
        pack_sizes = []
    return _PackInfo(
        pack_pos=pack_pos,
        pack_sizes=pack_sizes,
        pack_positions=_pack_positions(pack_sizes),
    )


def _read_unpack_info(buffer: BinaryIO) -> list[SevenZipFolder]:
    prop = _read_property(buffer, "7z UNPACK_INFO")
    if prop != _Property.FOLDER:
        raise CorruptionError(f"Expected FOLDER in 7z UNPACK_INFO, got 0x{prop:02x}")

    num_folders = _read_uint64(buffer)
    external = _read_byte(buffer)
    if external != 0:
        raise UnsupportedFeatureError(
            "External 7z folder definitions are not supported"
        )

    folders = [_read_folder(buffer) for _ in range(num_folders)]
    prop = _read_property(buffer, "7z UNPACK_INFO")
    if prop != _Property.CODERS_UNPACK_SIZE:
        raise CorruptionError(
            f"Expected CODERS_UNPACK_SIZE in 7z UNPACK_INFO, got 0x{prop:02x}"
        )

    for folder in folders:
        folder.unpack_sizes = [
            _read_uint64(buffer)
            for coder in folder.coders
            for _ in range(coder.num_out_streams)
        ]

    prop = _read_property(buffer, "7z UNPACK_INFO")
    if prop == _Property.CRC:
        defined, crcs = _read_digests(buffer, len(folders))
        for index, folder in enumerate(folders):
            folder.digest_defined = defined[index]
            folder.crc = crcs[index]
        prop = _read_property(buffer, "7z UNPACK_INFO")

    if prop != _Property.END:
        raise CorruptionError(f"Expected END in 7z UNPACK_INFO, got 0x{prop:02x}")
    return folders


def _read_folder(buffer: BinaryIO) -> SevenZipFolder:
    num_coders = _read_uint64(buffer)
    coders: list[SevenZipCoder] = []
    total_in = 0
    total_out = 0

    for _ in range(num_coders):
        flags = _read_byte(buffer)
        method_size = flags & 0x0F
        if flags & 0x80:
            raise UnsupportedFeatureError(
                "Alternative 7z coder methods are not supported"
            )
        method = _read_exact(buffer, method_size, "7z coder method id")
        if method_size == 0:
            method = _METHOD_COPY

        if flags & 0x10:
            num_in_streams = _read_uint64(buffer)
            num_out_streams = _read_uint64(buffer)
        else:
            num_in_streams = 1
            num_out_streams = 1
        properties = None
        if flags & 0x20:
            prop_size = _read_uint64(buffer)
            properties = _read_exact(buffer, prop_size, "7z coder properties")

        total_in += num_in_streams
        total_out += num_out_streams
        coders.append(
            SevenZipCoder(
                method=method,
                num_in_streams=num_in_streams,
                num_out_streams=num_out_streams,
                properties=properties,
            )
        )

    num_bind_pairs = total_out - 1
    bind_pairs = [
        (_read_uint64(buffer), _read_uint64(buffer)) for _ in range(num_bind_pairs)
    ]
    num_packed_streams = total_in - num_bind_pairs
    if num_packed_streams == 1:
        bound_in_streams = {in_stream for in_stream, _ in bind_pairs}
        packed_indices = [
            index for index in range(total_in) if index not in bound_in_streams
        ]
    else:
        packed_indices = [_read_uint64(buffer) for _ in range(num_packed_streams)]

    return SevenZipFolder(
        coders=coders,
        bind_pairs=bind_pairs,
        packed_indices=packed_indices,
        unpack_sizes=[],
        crc=None,
        digest_defined=False,
    )


def _read_substreams_info(
    buffer: BinaryIO, folders: list[SevenZipFolder]
) -> tuple[list[int], list[int], list[int | None]]:
    prop = _read_property(buffer, "7z SUBSTREAMS_INFO")
    if prop == _Property.NUM_UNPACK_STREAM:
        num_unpackstreams_folders = [_read_uint64(buffer) for _ in folders]
        prop = _read_property(buffer, "7z SUBSTREAMS_INFO")
    else:
        num_unpackstreams_folders = [1] * len(folders)

    unpack_sizes: list[int] = []
    if prop == _Property.SIZE:
        for folder_index, folder in enumerate(folders):
            total = 0
            count = num_unpackstreams_folders[folder_index]
            for _ in range(max(count - 1, 0)):
                size = _read_uint64(buffer)
                unpack_sizes.append(size)
                total += size
            if count:
                last_size = _folder_unpack_size(folder) - total
                if last_size < 0:
                    raise CorruptionError(
                        "7z substream sizes exceed folder unpack size"
                    )
                unpack_sizes.append(last_size)
        prop = _read_property(buffer, "7z SUBSTREAMS_INFO")
    else:
        for folder_index, folder in enumerate(folders):
            count = num_unpackstreams_folders[folder_index]
            if count == 1:
                unpack_sizes.append(_folder_unpack_size(folder))

    digest_slots = _substream_digest_slots(folders, num_unpackstreams_folders)
    digests: list[int | None] = []
    if prop == _Property.CRC:
        defined, crcs = _read_digests(buffer, digest_slots)
        digest_index = 0
        for folder_index, folder in enumerate(folders):
            count = num_unpackstreams_folders[folder_index]
            if count == 1 and folder.digest_defined:
                digests.append(folder.crc)
            else:
                for _ in range(count):
                    digests.append(
                        crcs[digest_index] if defined[digest_index] else None
                    )
                    digest_index += 1
        prop = _read_property(buffer, "7z SUBSTREAMS_INFO")
    else:
        for folder_index, folder in enumerate(folders):
            count = num_unpackstreams_folders[folder_index]
            if count == 1 and folder.digest_defined:
                digests.append(folder.crc)
            else:
                digests.extend([None] * count)

    if prop != _Property.END:
        raise CorruptionError(f"Expected END in 7z SUBSTREAMS_INFO, got 0x{prop:02x}")
    return num_unpackstreams_folders, unpack_sizes, digests


def _buffer_len(buffer: BinaryIO) -> int:
    """Total byte length of a seekable in-memory buffer (the parser always feeds BytesIO)."""
    pos = buffer.tell()
    end = buffer.seek(0, io.SEEK_END)
    buffer.seek(pos)
    return end


def _read_files_info(buffer: BinaryIO) -> tuple[list[SevenZipFileRecord], str | None]:
    num_files = _read_uint64(buffer)
    # Bound the file count against the header size before pre-allocating one object per
    # claimed file. Every real file must be described by at least one byte of header
    # somewhere (a name, an empty-stream/anti/empty-file bit, or a mapped substream), so a
    # count larger than the whole (already CRC-checked, size-bounded) header buffer is a
    # crafted metadata bomb — a 5-byte field could otherwise request 2**40 allocations and
    # OOM the process at open. See threat-model O1 / review L1. The CRC does NOT make the
    # header trustworthy: an attacker crafting the archive computes a matching CRC.
    header_size = _buffer_len(buffer)
    if num_files > header_size:
        raise CorruptionError(
            f"7z file count {num_files} exceeds the {header_size}-byte header "
            f"(each file needs at least one byte of metadata)"
        )
    files = [_FileProps() for _ in range(num_files)]
    num_empty_streams = 0
    comment: str | None = None

    while True:
        prop = _read_property(buffer, "7z FILES_INFO")
        if prop == _Property.END:
            return [_file_record_from_props(props) for props in files], comment

        size = _read_uint64(buffer)
        payload = io.BytesIO(_read_exact(buffer, size, "7z file property payload"))
        if prop == _Property.DUMMY:
            continue
        if prop == _Property.EMPTY_STREAM:
            empty_streams = _read_boolean(payload, num_files)
            for file_props, empty in zip(files, empty_streams, strict=True):
                file_props.emptystream = empty
            num_empty_streams = empty_streams.count(True)
        elif prop == _Property.EMPTY_FILE:
            _apply_empty_stream_property(
                files, _read_boolean(payload, num_empty_streams), "is_empty_file"
            )
        elif prop == _Property.ANTI:
            _apply_empty_stream_property(
                files, _read_boolean(payload, num_empty_streams), "is_anti"
            )
        elif prop == _Property.NAME:
            _read_names(payload, files)
        elif prop == _Property.CREATION_TIME:
            _read_times(payload, files, "creation_time")
        elif prop == _Property.LAST_ACCESS_TIME:
            _read_times(payload, files, "last_access_time")
        elif prop == _Property.LAST_WRITE_TIME:
            _read_times(payload, files, "last_write_time")
        elif prop == _Property.ATTRIBUTES:
            _read_attributes(payload, files)
        elif prop == _Property.COMMENT:
            comment = _read_comment(payload)
        elif prop == _Property.START_POS:
            _skip_start_positions(payload, num_files)
        else:
            raise UnsupportedFeatureError(
                f"Unsupported 7z FILES_INFO property 0x{prop:02x}"
            )


def _file_record_from_props(props: _FileProps) -> SevenZipFileRecord:
    return SevenZipFileRecord(
        filename=props.filename,
        emptystream=props.emptystream,
        is_anti=props.is_anti,
        is_directory=props.emptystream
        and not props.is_empty_file
        and not props.is_anti,
        is_empty_file=props.is_empty_file,
        attributes=props.attributes,
        creation_time=props.creation_time,
        last_access_time=props.last_access_time,
        last_write_time=props.last_write_time,
        folder_index=None,
        file_in_folder=None,
        uncompressed_size=0,
        crc32=None,
        compressed_size=None,
        is_encrypted=False,
        is_solid=False,
        compression_methods=(),
    )


def _map_files_to_folders(
    files: list[SevenZipFileRecord],
    *,
    folders: list[SevenZipFolder],
    pack_sizes: list[int],
    num_unpackstreams_folders: list[int],
    unpack_sizes: list[int],
    digests: list[int | None],
) -> list[SevenZipFileRecord]:
    folder_compressed_sizes = _folder_compressed_sizes(folders, pack_sizes)
    folder_index = 0
    file_in_folder = 0
    substream_index = 0

    for file_record in files:
        if file_record.emptystream:
            continue
        if folder_index >= len(folders):
            raise CorruptionError("7z file table references a missing folder")
        if substream_index >= len(unpack_sizes):
            raise CorruptionError("7z file table references a missing substream")

        folder = folders[folder_index]
        file_record.folder_index = folder_index
        file_record.file_in_folder = file_in_folder
        file_record.uncompressed_size = unpack_sizes[substream_index]
        file_record.crc32 = (
            digests[substream_index] if substream_index < len(digests) else None
        )
        file_record.compressed_size = folder_compressed_sizes[folder_index]
        file_record.is_encrypted = folder_is_encrypted(folder)
        file_record.is_solid = num_unpackstreams_folders[folder_index] > 1
        file_record.compression_methods = tuple(coder.method for coder in folder.coders)

        file_in_folder += 1
        substream_index += 1
        if file_in_folder >= num_unpackstreams_folders[folder_index]:
            folder_index += 1
            file_in_folder = 0

    return files


def _decode_encoded_header(
    archive_fp: BinaryIO,
    streams: _StreamsInfo,
    *,
    decode_folder: DecodeFolder,
) -> bytes:
    folders = streams.folders or []
    pack_sizes = streams.pack_sizes or []
    pack_positions = streams.pack_positions or _pack_positions(pack_sizes)
    pack_stream_index = 0
    decoded = bytearray()

    for folder in folders:
        pack_count = len(folder.packed_indices)
        if pack_count != 1:
            raise UnsupportedFeatureError(
                "Encoded 7z headers with multi-pack folders are not supported"
            )
        if pack_stream_index >= len(pack_sizes):
            raise CorruptionError("Encoded 7z header references a missing pack stream")

        compressed_size = pack_sizes[pack_stream_index]
        uncompressed_size = _folder_unpack_size(folder)
        absolute_offset = (
            _SIGNATURE_HEADER_SIZE
            + streams.pack_pos
            + pack_positions[pack_stream_index]
        )
        source = SlicingStream(archive_fp, absolute_offset, compressed_size)
        # ``decode_folder`` owns codec/crypto handling and CRC-checks its own output.
        decoded.extend(
            decode_folder(source, folder, compressed_size, uncompressed_size)
        )
        pack_stream_index += pack_count

    return bytes(decoded)


def _read_names(buffer: BinaryIO, files: list[_FileProps]) -> None:
    external = _read_byte(buffer)
    if external != 0:
        raise UnsupportedFeatureError("External 7z filename data is not supported")
    for file_props in files:
        file_props.filename = _read_utf16(buffer).replace("\\", "/")


def _read_times(buffer: BinaryIO, files: list[_FileProps], field: str) -> None:
    defined = _read_boolean(buffer, len(files), check_all=True)
    external = _read_byte(buffer)
    if external != 0:
        raise UnsupportedFeatureError("External 7z timestamp data is not supported")
    for file_props, is_defined in zip(files, defined, strict=True):
        if is_defined:
            setattr(file_props, field, _read_real_uint64(buffer))


def _read_attributes(buffer: BinaryIO, files: list[_FileProps]) -> None:
    defined = _read_boolean(buffer, len(files), check_all=True)
    external = _read_byte(buffer)
    if external != 0:
        raise UnsupportedFeatureError("External 7z attribute data is not supported")
    for file_props, is_defined in zip(files, defined, strict=True):
        if is_defined:
            file_props.attributes = _read_uint32(buffer)


def _read_comment(buffer: BinaryIO) -> str | None:
    data = buffer.read()
    if not data:
        return None
    if data[0] == 0:
        data = data[1:]
    data = data.rstrip(b"\x00")
    if not data:
        return None
    try:
        return data.decode("utf-16le")
    except UnicodeDecodeError as exc:
        raise CorruptionError(f"Could not decode 7z comment: {exc!r}") from exc


def _skip_start_positions(buffer: BinaryIO, num_files: int) -> None:
    defined = _read_boolean(buffer, num_files, check_all=True)
    external = _read_byte(buffer)
    if external != 0:
        raise UnsupportedFeatureError(
            "External 7z start-position data is not supported"
        )
    for is_defined in defined:
        if is_defined:
            _read_real_uint64(buffer)


def _skip_archive_properties(buffer: BinaryIO) -> None:
    while True:
        prop = _read_property(buffer, "7z archive properties")
        if prop == _Property.END:
            return
        size = _read_uint64(buffer)
        _read_exact(buffer, size, "7z archive property payload")


def _skip_streams_info(buffer: BinaryIO) -> None:
    _read_streams_info(buffer)


def _apply_empty_stream_property(
    files: list[_FileProps], values: list[bool], field: str
) -> None:
    value_index = 0
    for file_props in files:
        if not file_props.emptystream:
            continue
        if value_index >= len(values):
            raise CorruptionError("7z empty-stream property is truncated")
        setattr(file_props, field, values[value_index])
        value_index += 1


def _read_digests(buffer: BinaryIO, count: int) -> tuple[list[bool], list[int | None]]:
    defined = _read_boolean(buffer, count, check_all=True)
    crcs: list[int | None] = []
    for is_defined in defined:
        crcs.append(_read_uint32(buffer) if is_defined else None)
    return defined, crcs


def _substream_digest_slots(
    folders: list[SevenZipFolder], num_unpackstreams_folders: list[int]
) -> int:
    slots = 0
    for folder, count in zip(folders, num_unpackstreams_folders, strict=True):
        if count != 1 or not folder.digest_defined:
            slots += count
    return slots


def _folder_unpack_size(folder: SevenZipFolder) -> int:
    bound_out_streams = {out_stream for _, out_stream in folder.bind_pairs}
    for index in range(len(folder.unpack_sizes) - 1, -1, -1):
        if index not in bound_out_streams:
            return folder.unpack_sizes[index]
    if folder.unpack_sizes:
        return folder.unpack_sizes[-1]
    return 0


def _folder_compressed_sizes(
    folders: list[SevenZipFolder], pack_sizes: list[int]
) -> list[int | None]:
    sizes: list[int | None] = []
    pack_index = 0
    for folder in folders:
        pack_count = len(folder.packed_indices)
        if pack_index + pack_count > len(pack_sizes):
            sizes.append(None)
        else:
            sizes.append(sum(pack_sizes[pack_index : pack_index + pack_count]))
        pack_index += pack_count
    return sizes


def _pack_positions(pack_sizes: list[int]) -> list[int]:
    positions = [0]
    total = 0
    for size in pack_sizes:
        total += size
        positions.append(total)
    return positions


def _read_boolean(
    buffer: BinaryIO, count: int, *, check_all: bool = False
) -> list[bool]:
    if check_all:
        all_defined = _read_byte(buffer)
        if all_defined != 0:
            return [True] * count

    result: list[bool] = []
    current = 0
    mask = 0
    for _ in range(count):
        if mask == 0:
            current = _read_byte(buffer)
            mask = 0x80
        result.append((current & mask) != 0)
        mask >>= 1
    return result


def _read_utf16(buffer: BinaryIO) -> str:
    chunks = bytearray()
    for _ in range(_MAX_UTF16_CHARS):
        unit = _read_exact(buffer, 2, "7z UTF-16 name")
        if unit == b"\x00\x00":
            return bytes(chunks).decode("utf-16le")
        chunks.extend(unit)
    raise CorruptionError("7z UTF-16 string is not null-terminated")


def _read_uint64(buffer: BinaryIO) -> int:
    first = _read_byte(buffer)
    if first == 0xFF:
        return _read_real_uint64(buffer)

    mask = 0x80
    extra_bytes = _MAX_UINT64_ENCODING
    for limit, length in (
        (0b01111111, 0),
        (0b10111111, 1),
        (0b11011111, 2),
        (0b11101111, 3),
        (0b11110111, 4),
        (0b11111011, 5),
        (0b11111101, 6),
        (0b11111110, 7),
    ):
        if first <= limit:
            extra_bytes = length
            break
        mask >>= 1

    if extra_bytes == 0:
        return first & (mask - 1)
    data = _read_exact(buffer, extra_bytes, "7z UINT64")
    low = int.from_bytes(data, "little")
    high = first & (mask - 1)
    return low + (high << (extra_bytes * 8))


def _read_property(buffer: BinaryIO, context: str) -> _Property:
    value = _read_byte(buffer)
    try:
        return _Property(value)
    except ValueError:
        raise UnsupportedFeatureError(
            f"Unsupported 7z property 0x{value:02x} in {context}"
        ) from None


def _read_byte(buffer: BinaryIO) -> int:
    return _read_exact(buffer, 1, "7z byte")[0]


def _read_uint32(buffer: BinaryIO) -> int:
    return int.from_bytes(_read_exact(buffer, 4, "7z UINT32"), "little")


def _read_real_uint64(buffer: BinaryIO) -> int:
    return int.from_bytes(_read_exact(buffer, 8, "7z real UINT64"), "little")


def _read_exact(buffer: BinaryIO, length: int, context: str) -> bytes:
    """Read ``length`` bytes, or raise a typed error for hostile/truncated claims.

    Hostile headers often advertise a multi-EiB property payload via a 7z UINT64. Asking
    ``BytesIO.read`` for a value that does not fit in a C ``ssize_t`` raises a raw
    ``OverflowError``; even a "merely" multi-GiB claim would OOM. Bound against the
    remaining buffer (when seekable) and a hard ceiling before calling into the stream.
    """
    if length < 0:
        raise CorruptionError(f"Negative length for {context}: {length}")
    if length > _MAX_NEXT_HEADER_SIZE:
        raise CorruptionError(
            f"Claimed {context} length {length} exceeds the "
            f"{_MAX_NEXT_HEADER_SIZE}-byte parser limit"
        )
    try:
        remaining = _buffer_len(buffer) - buffer.tell()
    except (OSError, OverflowError, ValueError):
        remaining = None
    if remaining is not None and length > remaining:
        raise CorruptionError(
            f"Truncated {context}: claimed {length} bytes, only {remaining} remain"
        )
    try:
        data = read_exact(buffer, length)
    except OverflowError as exc:
        # Belt-and-suspenders: some stream types still reject large ``n`` as OverflowError.
        raise CorruptionError(
            f"Claimed {context} length {length} is not representable as a read size"
        ) from exc
    if len(data) != length:
        raise CorruptionError(f"Truncated {context}: expected {length} bytes")
    return data


def _crc32(data: bytes) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF


def _method_hex(method: bytes) -> str:
    return "0x" + method.hex()


_METHOD_ALGORITHMS: dict[bytes, CompressionAlgorithm] = {
    _METHOD_COPY: CompressionAlgorithm.STORED,
    _METHOD_LZMA: CompressionAlgorithm.LZMA,
    _METHOD_LZMA2: CompressionAlgorithm.LZMA2,
    _METHOD_DELTA: CompressionAlgorithm.DELTA,
    b"\x04": CompressionAlgorithm.BCJ,
    b"\x03\x03\x01\x03": CompressionAlgorithm.BCJ,
    b"\x03\x03\x02\x05": CompressionAlgorithm.BCJ,
    b"\x03\x03\x04\x01": CompressionAlgorithm.BCJ,
    b"\x03\x03\x05\x01": CompressionAlgorithm.BCJ,
    b"\x03\x03\x07\x01": CompressionAlgorithm.BCJ,
    b"\x03\x03\x08\x05": CompressionAlgorithm.BCJ,
    _METHOD_BCJ2: CompressionAlgorithm.BCJ2,
    _METHOD_DEFLATE: CompressionAlgorithm.DEFLATE,
    _METHOD_DEFLATE64: CompressionAlgorithm.DEFLATE64,
    _METHOD_BZIP2: CompressionAlgorithm.BZIP2,
    _METHOD_ZSTD: CompressionAlgorithm.ZSTD,
    _METHOD_BROTLI: CompressionAlgorithm.BROTLI,
    _METHOD_LZ4: CompressionAlgorithm.LZ4,
    _METHOD_PPMD: CompressionAlgorithm.PPMD,
    _METHOD_AES: CompressionAlgorithm.UNKNOWN,
}
