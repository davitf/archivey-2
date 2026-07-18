"""Native 7z header parser (structure only — no codecs/crypto).

Reads the signature/end header and turns header blocks into small data objects.
Encoded headers are returned as descriptors; the reader/pipeline materializes them.
"""

from __future__ import annotations

import io
import struct
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from enum import IntEnum
from typing import BinaryIO

from archivey.exceptions import CorruptionError, UnsupportedFeatureError
from archivey.internal.backends.sevenzip_methods import (
    METHOD_AES,
    METHOD_COPY,
    lookup,
)
from archivey.internal.streams.streamtools import read_exact
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
class SignatureInfo:
    """Result of reading the 32-byte signature + locating the next-header bytes."""

    major_version: int
    minor_version: int
    header_data: bytes  # empty when nextHeaderSize == 0


@dataclass
class EncodedHeader:
    """ENCODED_HEADER streams info; packed data still lives on the archive file."""

    streams: _StreamsInfo


@dataclass
class PlainHeader:
    streams: _StreamsInfo
    files: list[SevenZipFileRecord]
    comment: str | None


HeaderBlock = EncodedHeader | PlainHeader


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


def read_signature_and_next_header(fp: BinaryIO) -> SignatureInfo:
    """Read signature header, verify CRCs, return next-header bytes (possibly empty)."""
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

    if next_header_size == 0:
        if next_header_crc != 0 and next_header_crc != _crc32(b""):
            raise CorruptionError("7z empty next-header CRC mismatch")
        return SignatureInfo(major_version, minor_version, b"")

    try:
        fp.seek(_SIGNATURE_HEADER_SIZE + next_header_offset)
    except (OSError, OverflowError) as exc:
        raise CorruptionError(
            f"7z next-header seek failed at offset {next_header_offset}"
        ) from exc
    header_data = _read_exact(fp, next_header_size, "7z next header")
    if _crc32(header_data) != next_header_crc:
        raise CorruptionError("7z next header CRC mismatch")
    return SignatureInfo(major_version, minor_version, header_data)


def parse_header_block(header_data: bytes) -> HeaderBlock:
    """Parse one header block into a plain HEADER or an ENCODED_HEADER descriptor."""
    if not header_data:
        return PlainHeader(_StreamsInfo(), [], None)

    buffer = io.BytesIO(header_data)
    prop = _read_property(buffer, "7z header")
    if prop == _Property.END:
        return PlainHeader(_StreamsInfo(), [], None)
    if prop == _Property.HEADER:
        return _parse_plain_header(buffer)
    if prop != _Property.ENCODED_HEADER:
        raise CorruptionError(f"Expected 7z HEADER or ENCODED_HEADER, got 0x{prop:02x}")
    return EncodedHeader(_read_streams_info(buffer))


def materialize_archive(
    signature: SignatureInfo,
    plain: PlainHeader,
    *,
    is_header_encrypted: bool = False,
) -> SevenZipArchive:
    """Build the final archive object from a fully decoded plain header."""
    streams = plain.streams
    pack_sizes = streams.pack_sizes or []
    pack_positions = streams.pack_positions or _pack_positions(pack_sizes)
    folders = streams.folders or []
    num_unpackstreams_folders = streams.num_unpackstreams_folders or []
    unpack_sizes = streams.unpack_sizes or []
    digests = streams.digests or []
    pack_pos = _SIGNATURE_HEADER_SIZE + streams.pack_pos

    files = _map_files_to_folders(
        plain.files,
        folders=folders,
        pack_sizes=pack_sizes,
        num_unpackstreams_folders=num_unpackstreams_folders,
        unpack_sizes=unpack_sizes,
        digests=digests,
    )
    return SevenZipArchive(
        major_version=signature.major_version,
        minor_version=signature.minor_version,
        pack_pos=pack_pos,
        pack_sizes=pack_sizes,
        pack_positions=pack_positions,
        folders=folders,
        num_unpackstreams_folders=num_unpackstreams_folders,
        unpack_sizes=unpack_sizes,
        digests=digests,
        files=files,
        comment=plain.comment,
        is_solid=any(n > 1 for n in num_unpackstreams_folders),
        is_header_encrypted=is_header_encrypted,
        has_encrypted_folders=any(folder_is_encrypted(folder) for folder in folders),
    )


def empty_archive(signature: SignatureInfo) -> SevenZipArchive:
    """Zero-member archive (nextHeaderSize == 0)."""
    return SevenZipArchive(
        major_version=signature.major_version,
        minor_version=signature.minor_version,
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


def encoded_folder_slices(
    encoded: EncodedHeader,
) -> list[tuple[SevenZipFolder, int, int, int]]:
    """Return ``(folder, absolute_offset, compressed_size, uncompressed_size)`` slices.

    Offsets are absolute from the start of the archive file.
    """
    streams = encoded.streams
    folders = streams.folders or []
    pack_sizes = streams.pack_sizes or []
    pack_positions = streams.pack_positions or _pack_positions(pack_sizes)
    pack_stream_index = 0
    slices: list[tuple[SevenZipFolder, int, int, int]] = []

    for folder in folders:
        pack_count = len(folder.packed_indices)
        if pack_count != 1:
            raise UnsupportedFeatureError(
                "Encoded 7z headers with multi-pack folders are not supported"
            )
        if pack_stream_index >= len(pack_sizes):
            raise CorruptionError("Encoded 7z header references a missing pack stream")

        compressed_size = pack_sizes[pack_stream_index]
        uncompressed_size = folder_unpack_size(folder)
        absolute_offset = (
            _SIGNATURE_HEADER_SIZE
            + streams.pack_pos
            + pack_positions[pack_stream_index]
        )
        slices.append((folder, absolute_offset, compressed_size, uncompressed_size))
        pack_stream_index += pack_count
    return slices


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


def folder_unpack_size(folder: SevenZipFolder) -> int:
    bound_out_streams = {out_stream for _, out_stream in folder.bind_pairs}
    for index in range(len(folder.unpack_sizes) - 1, -1, -1):
        if index not in bound_out_streams:
            return folder.unpack_sizes[index]
    if folder.unpack_sizes:
        return folder.unpack_sizes[-1]
    return 0


def folder_is_encrypted(folder: SevenZipFolder) -> bool:
    """Whether any coder in the folder is 7z AES.

    Lives here (not in ``sevenzip_methods``) so it can be typed against the folder
    dataclass: the registry module is a pure leaf and must not import these types.
    """
    return any(lookup(coder.method) is METHOD_AES for coder in folder.coders)


def compression_method_for_coder(coder: SevenZipCoder) -> CompressionMethod:
    """archivey's compression descriptor for a single 7z coder."""
    method = lookup(coder.method)
    algo = method.algorithm if method is not None else CompressionAlgorithm.UNKNOWN
    return CompressionMethod(algo, properties=coder.properties)


# Re-export for callers that historically imported these from the parser.
__all__ = [
    "MAGIC_7Z",
    "EncodedHeader",
    "HeaderBlock",
    "PlainHeader",
    "SevenZipArchive",
    "SevenZipCoder",
    "SevenZipFileRecord",
    "SevenZipFolder",
    "SignatureInfo",
    "compression_method_for_coder",
    "compute_is_current",
    "empty_archive",
    "encoded_folder_slices",
    "folder_is_encrypted",
    "folder_unpack_size",
    "materialize_archive",
    "parse_header_block",
    "read_signature_and_next_header",
]


def _parse_plain_header(buffer: BinaryIO) -> PlainHeader:
    streams = _StreamsInfo()
    files: list[SevenZipFileRecord] = []
    comment: str | None = None

    while True:
        prop = _read_property(buffer, "7z plain header")
        if prop == _Property.END:
            return PlainHeader(streams, files, comment)
        if prop == _Property.ARCHIVE_PROPERTIES:
            _skip_archive_properties(buffer)
        elif prop == _Property.ADDITIONAL_STREAMS_INFO:
            _read_streams_info(buffer)  # skip by consuming
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
        pack_sizes = pack_sizes or []
        streams.pack_pos = pack_pos
        streams.pack_sizes = pack_sizes
        streams.pack_positions = _pack_positions(pack_sizes)
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
            folder_unpack_size(folder) for folder in streams.folders
        ]
        streams.digests = [
            folder.crc if folder.digest_defined else None for folder in streams.folders
        ]

    if prop != _Property.END:
        raise CorruptionError(f"Expected END in 7z streams info, got 0x{prop:02x}")
    return streams


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
            method = METHOD_COPY.method_id

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
                last_size = folder_unpack_size(folder) - total
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
                unpack_sizes.append(folder_unpack_size(folder))

    digest_slots = 0
    for folder, count in zip(folders, num_unpackstreams_folders, strict=True):
        if count != 1 or not folder.digest_defined:
            digest_slots += count

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
    # Header payloads are BytesIO — ``getbuffer().nbytes`` is O(1). The seek/tell
    # triple is only for real file objects (signature / next-header reads).
    if isinstance(buffer, io.BytesIO):
        return buffer.getbuffer().nbytes
    pos = buffer.tell()
    end = buffer.seek(0, io.SEEK_END)
    buffer.seek(pos)
    return end


def _buffer_remaining(buffer: BinaryIO) -> int | None:
    """Bytes left in ``buffer``, or ``None`` when length cannot be determined."""
    try:
        if isinstance(buffer, io.BytesIO):
            return buffer.getbuffer().nbytes - buffer.tell()
        return _buffer_len(buffer) - buffer.tell()
    except (OSError, OverflowError, ValueError):
        return None


def _read_files_info(buffer: BinaryIO) -> tuple[list[SevenZipFileRecord], str | None]:
    num_files = _read_uint64(buffer)
    # Bound the file count against the header size before pre-allocating one object per
    # claimed file. See threat-model O1 / review L1. CRC does NOT make the header
    # trustworthy: an attacker crafting the archive computes a matching CRC.
    header_size = _buffer_len(buffer)
    if num_files > header_size:
        raise CorruptionError(
            f"7z file count {num_files} exceeds the {header_size}-byte header "
            f"(each file needs at least one byte of metadata)"
        )
    files = [_FileProps() for _ in range(num_files)]
    num_empty_streams = 0
    comment: str | None = None
    handlers = _FILES_INFO_HANDLERS

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
            continue
        if prop == _Property.COMMENT:
            comment = _read_comment(payload)
            continue
        handler = handlers.get(prop)
        if handler is None:
            raise UnsupportedFeatureError(
                f"Unsupported 7z FILES_INFO property 0x{prop:02x}"
            )
        handler(payload, files, num_empty_streams, num_files)


def _apply_empty_stream_bool(
    files: list[_FileProps], values: list[bool], field_name: str
) -> None:
    value_index = 0
    for file_props in files:
        if not file_props.emptystream:
            continue
        if value_index >= len(values):
            raise CorruptionError("7z empty-stream property is truncated")
        setattr(file_props, field_name, values[value_index])
        value_index += 1


def _handle_empty_file(
    payload: BinaryIO, files: list[_FileProps], num_empty: int, _n: int
) -> None:
    _apply_empty_stream_bool(files, _read_boolean(payload, num_empty), "is_empty_file")


def _handle_anti(
    payload: BinaryIO, files: list[_FileProps], num_empty: int, _n: int
) -> None:
    _apply_empty_stream_bool(files, _read_boolean(payload, num_empty), "is_anti")


def _handle_name(
    payload: BinaryIO, files: list[_FileProps], _num_empty: int, _n: int
) -> None:
    external = _read_byte(payload)
    if external != 0:
        raise UnsupportedFeatureError("External 7z filename data is not supported")
    # Bulk-decode the whole names blob (known size, null-terminated UTF-16LE
    # strings). Per-character ``_read_exact`` was ~22 calls/member and dominated
    # listing (perf review L1 / listing-attribution.md).
    blob = payload.read()
    names = _decode_utf16_names(blob, expected_count=len(files))
    for file_props, name in zip(files, names, strict=True):
        file_props.filename = name.replace("\\", "/")


def _decode_utf16_names(blob: bytes, *, expected_count: int) -> list[str]:
    """Decode a 7z ``kName`` property payload into ``expected_count`` filenames."""
    # Zero names: the payload is empty (no terminators). Match the old per-file
    # loop's no-op; ``endswith(b"\\x00\\x00")`` below would mis-reject ``b""``.
    if expected_count == 0:
        if blob:
            raise CorruptionError("7z name payload is non-empty for zero files")
        return []
    if len(blob) % 2 != 0:
        raise CorruptionError("7z UTF-16 name payload has an odd byte length")
    if not blob.endswith(b"\x00\x00"):
        raise CorruptionError("7z UTF-16 name list is not null-terminated")
    try:
        text = blob.decode("utf-16le")
    except UnicodeDecodeError as exc:
        raise CorruptionError(f"Could not decode 7z UTF-16 names: {exc!r}") from exc
    # Final NUL from the last terminator → trailing empty from split; drop it.
    if not text.endswith("\x00"):
        raise CorruptionError("7z UTF-16 name list is not null-terminated")
    names = text[:-1].split("\x00")
    if len(names) != expected_count:
        raise CorruptionError(
            f"7z name count {len(names)} does not match file count {expected_count}"
        )
    for name in names:
        if len(name) > _MAX_UTF16_CHARS:
            raise CorruptionError(
                f"7z UTF-16 name exceeds {_MAX_UTF16_CHARS} characters"
            )
    return names


def _handle_time(
    field_name: str,
) -> Callable[[BinaryIO, list[_FileProps], int, int], None]:
    def _handler(
        payload: BinaryIO, files: list[_FileProps], _num_empty: int, _n: int
    ) -> None:
        defined = _read_boolean(payload, len(files), check_all=True)
        external = _read_byte(payload)
        if external != 0:
            raise UnsupportedFeatureError("External 7z timestamp data is not supported")
        for file_props, is_defined in zip(files, defined, strict=True):
            if is_defined:
                setattr(file_props, field_name, _read_real_uint64(payload))

    return _handler


def _handle_attributes(
    payload: BinaryIO, files: list[_FileProps], _num_empty: int, _n: int
) -> None:
    defined = _read_boolean(payload, len(files), check_all=True)
    external = _read_byte(payload)
    if external != 0:
        raise UnsupportedFeatureError("External 7z attribute data is not supported")
    for file_props, is_defined in zip(files, defined, strict=True):
        if is_defined:
            file_props.attributes = _read_uint32(payload)


def _handle_start_pos(
    payload: BinaryIO, _files: list[_FileProps], _num_empty: int, num_files: int
) -> None:
    defined = _read_boolean(payload, num_files, check_all=True)
    external = _read_byte(payload)
    if external != 0:
        raise UnsupportedFeatureError(
            "External 7z start-position data is not supported"
        )
    for is_defined in defined:
        if is_defined:
            _read_real_uint64(payload)


_FILES_INFO_HANDLERS: dict[
    _Property, Callable[[BinaryIO, list[_FileProps], int, int], None]
] = {
    _Property.EMPTY_FILE: _handle_empty_file,
    _Property.ANTI: _handle_anti,
    _Property.NAME: _handle_name,
    _Property.CREATION_TIME: _handle_time("creation_time"),
    _Property.LAST_ACCESS_TIME: _handle_time("last_access_time"),
    _Property.LAST_WRITE_TIME: _handle_time("last_write_time"),
    _Property.ATTRIBUTES: _handle_attributes,
    _Property.START_POS: _handle_start_pos,
}


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


def _skip_archive_properties(buffer: BinaryIO) -> None:
    while True:
        prop = _read_property(buffer, "7z archive properties")
        if prop == _Property.END:
            return
        size = _read_uint64(buffer)
        _read_exact(buffer, size, "7z archive property payload")


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


def _read_digests(buffer: BinaryIO, count: int) -> tuple[list[bool], list[int | None]]:
    defined = _read_boolean(buffer, count, check_all=True)
    crcs: list[int | None] = []
    for is_defined in defined:
        crcs.append(_read_uint32(buffer) if is_defined else None)
    return defined, crcs


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
    """Read one null-terminated UTF-16LE string (legacy / single-name helper).

    Prefer :func:`_decode_utf16_names` for the ``kName`` property (bulk path).
    """
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
    """Read ``length`` bytes, or raise a typed error for hostile/truncated claims."""
    if length < 0:
        raise CorruptionError(f"Negative length for {context}: {length}")
    if length > _MAX_NEXT_HEADER_SIZE:
        raise CorruptionError(
            f"Claimed {context} length {length} exceeds the "
            f"{_MAX_NEXT_HEADER_SIZE}-byte parser limit"
        )
    remaining = _buffer_remaining(buffer)
    if remaining is not None and length > remaining:
        raise CorruptionError(
            f"Truncated {context}: claimed {length} bytes, only {remaining} remain"
        )
    try:
        data = read_exact(buffer, length)
    except OverflowError as exc:
        raise CorruptionError(
            f"Claimed {context} length {length} is not representable as a read size"
        ) from exc
    if len(data) != length:
        raise CorruptionError(f"Truncated {context}: expected {length} bytes")
    return data


def _crc32(data: bytes) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF
