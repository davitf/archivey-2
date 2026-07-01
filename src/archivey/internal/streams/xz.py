"""
Seekable XZ decompression over stdlib ``lzma``: backward index scan + streaming state
machine.

XZ binary format (summary):
  A file is a sequence of one or more XZ streams, optionally separated by 4-byte-aligned
  null padding.

  Per stream:
    Header  (12 bytes): magic(6) + stream flags(2) + CRC32(4)
    Blocks  (variable): one or more LZMA2 blocks
    Index   (variable): MBI-encoded list of (unpadded_size, uncompressed_size) per block;
                        preceded by a 0x00 indicator byte; padded to a 4-byte boundary;
                        followed by CRC32(4)
    Footer  (12 bytes): CRC32(4) + backward_size(4) + stream flags(2) + magic(2)

  backward_size encodes the index size: actual_bytes = (value + 1) * 4.

XZ spec: https://tukaani.org/xz/xz-file-format.txt
"""

from __future__ import annotations

import lzma
import struct
import zlib
from dataclasses import dataclass
from typing import BinaryIO

from archivey.exceptions import CorruptionError, TruncatedError
from archivey.internal.logs import streams as logger
from archivey.internal.streams.decompressor_stream import (
    SeekPoint,
    _SegmentedDecompressorStream,
)

_XZ_STREAM_MAGIC = b"\xfd7zXZ\x00"
_XZ_FOOTER_MAGIC = b"YZ"
_STREAM_HEADER_SIZE = 12
_STREAM_FOOTER_SIZE = 12


def _round_up_4(n: int) -> int:
    return (n + 3) & ~3


def _decode_mbi(data: bytes, offset: int) -> tuple[int, int]:
    """Decode a multi-byte integer at ``offset``; return ``(value, bytes_consumed)``."""
    value = 0
    shift = 0
    for i in range(9):  # XZ spec: max 9 bytes per MBI
        if offset + i >= len(data):
            raise CorruptionError("XZ index MBI truncated")
        byte = data[offset + i]
        value |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            return value, i + 1
        shift += 7
    raise CorruptionError("XZ index MBI exceeds 9 bytes")


def _encode_mbi(value: int) -> bytes:
    if value == 0:
        return b"\x00"
    result = bytearray()
    while value > 0:
        byte = value & 0x7F
        value >>= 7
        if value > 0:
            byte |= 0x80
        result.append(byte)
    return bytes(result)


@dataclass
class _XzBlockBounds:
    compressed_start: int
    decompressed_start: int
    unpadded_size: int
    uncompressed_size: int
    check: int

    @property
    def decompressed_end(self) -> int:
        return self.decompressed_start + self.uncompressed_size


def _parse_xz_header(data: bytes) -> int:
    if len(data) < _STREAM_HEADER_SIZE:
        raise CorruptionError(f"XZ stream header too short: {len(data)} bytes")
    if data[:6] != _XZ_STREAM_MAGIC:
        raise CorruptionError(f"XZ stream header magic not found (got {data[:6]!r})")
    stream_flags = data[6:8]
    stored_crc = struct.unpack_from("<I", data, 8)[0]
    computed_crc = zlib.crc32(stream_flags) & 0xFFFFFFFF
    if stored_crc != computed_crc:
        raise CorruptionError(
            f"XZ stream header CRC32 mismatch: stored {stored_crc:#010x}, "
            f"computed {computed_crc:#010x}"
        )
    if stream_flags[0] != 0:
        raise CorruptionError(
            f"XZ stream flags reserved byte is non-zero: {stream_flags[0]:#04x}"
        )
    return stream_flags[1] & 0x0F


def _parse_xz_footer(data: bytes) -> tuple[int, int]:
    if len(data) < _STREAM_FOOTER_SIZE:
        raise CorruptionError(f"XZ stream footer too short: {len(data)} bytes")
    if data[10:12] != _XZ_FOOTER_MAGIC:
        raise CorruptionError(f"XZ stream footer magic 'YZ' not found (got {data[10:12]!r})")
    stored_crc = struct.unpack_from("<I", data, 0)[0]
    backward_size_raw = struct.unpack_from("<I", data, 4)[0]
    stream_flags = data[8:10]
    computed_crc = zlib.crc32(data[4:10]) & 0xFFFFFFFF
    if stored_crc != computed_crc:
        raise CorruptionError(
            f"XZ stream footer CRC32 mismatch: stored {stored_crc:#010x}, "
            f"computed {computed_crc:#010x}"
        )
    if stream_flags[0] != 0:
        raise CorruptionError(
            f"XZ stream flags reserved byte is non-zero: {stream_flags[0]:#04x}"
        )
    check = stream_flags[1] & 0x0F
    backward_size_bytes = (backward_size_raw + 1) * 4
    return check, backward_size_bytes


def _parse_xz_index(data: bytes) -> list[tuple[int, int]]:
    if not data or data[0] != 0x00:
        raise CorruptionError(
            f"XZ index indicator byte expected 0x00, got {data[0]:#04x}"
            if data
            else "XZ index is empty"
        )
    offset = 1
    num_records, consumed = _decode_mbi(data, offset)
    offset += consumed
    records: list[tuple[int, int]] = []
    for _ in range(num_records):
        unpadded_size, consumed = _decode_mbi(data, offset)
        offset += consumed
        uncompressed_size, consumed = _decode_mbi(data, offset)
        offset += consumed
        if unpadded_size == 0:
            raise CorruptionError("XZ index: unpadded_size must be > 0")
        records.append((unpadded_size, uncompressed_size))
    padded_len = _round_up_4(offset)
    for i in range(offset, padded_len):
        if i < len(data) and data[i] != 0:
            raise CorruptionError(f"XZ index padding byte {i} is non-zero: {data[i]:#04x}")
    return records


def _read_xz_index_backwards(
    stream: BinaryIO,
    file_size: int,
    stop_at: int = 0,
    start_decompressed_offset: int = 0,
) -> list[_XzBlockBounds]:
    """Walk XZ streams from EOF toward ``stop_at``, building a block index.

    Reads only footers and indices — no decompression. Returns blocks in forward order
    with absolute compressed/decompressed offsets.
    """
    all_streams: list[list[_XzBlockBounds]] = []
    compressed_end = file_size

    while compressed_end > stop_at:
        while compressed_end > stop_at:
            if compressed_end < 4:
                raise CorruptionError("XZ file too small to contain a valid stream")
            stream.seek(compressed_end - 4)
            tail = stream.read(4)
            if len(tail) < 4:
                raise CorruptionError("XZ file truncated during backward scan")
            if tail != b"\x00\x00\x00\x00":
                break
            compressed_end -= 4

        if compressed_end <= stop_at:
            break

        if compressed_end < _STREAM_FOOTER_SIZE:
            raise CorruptionError(f"Not enough bytes for XZ footer at offset {compressed_end}")
        stream.seek(compressed_end - _STREAM_FOOTER_SIZE)
        footer_data = stream.read(_STREAM_FOOTER_SIZE)
        if len(footer_data) < _STREAM_FOOTER_SIZE:
            raise CorruptionError("XZ footer truncated")
        check, index_size_bytes = _parse_xz_footer(footer_data)

        index_end = compressed_end - _STREAM_FOOTER_SIZE
        index_with_crc_start = index_end - index_size_bytes
        if index_with_crc_start < 0:
            raise CorruptionError("XZ index extends before start of file")

        stream.seek(index_with_crc_start)
        index_with_crc = stream.read(index_size_bytes)
        if len(index_with_crc) < index_size_bytes:
            raise CorruptionError("XZ index truncated")

        raw_index = index_with_crc[:-4]
        stored_index_crc = struct.unpack_from("<I", index_with_crc, len(index_with_crc) - 4)[0]
        computed_index_crc = zlib.crc32(raw_index) & 0xFFFFFFFF
        if stored_index_crc != computed_index_crc:
            raise CorruptionError(
                f"XZ index CRC32 mismatch: stored {stored_index_crc:#010x}, "
                f"computed {computed_index_crc:#010x}"
            )

        records = _parse_xz_index(raw_index)
        blocks_compressed_total = sum(_round_up_4(r[0]) for r in records)
        stream_header_start = (
            index_with_crc_start - blocks_compressed_total - _STREAM_HEADER_SIZE
        )
        if stream_header_start < 0:
            raise CorruptionError("XZ stream header start computes to negative offset")

        stream.seek(stream_header_start)
        header_data = stream.read(_STREAM_HEADER_SIZE)
        if len(header_data) < _STREAM_HEADER_SIZE:
            raise CorruptionError("XZ stream header truncated")
        header_check = _parse_xz_header(header_data)
        if header_check != check:
            raise CorruptionError(
                f"XZ stream header check {header_check} != footer check {check}"
            )

        stream_entries: list[_XzBlockBounds] = []
        block_compressed_start = stream_header_start + _STREAM_HEADER_SIZE
        for unpadded_size, uncompressed_size in records:
            stream_entries.append(
                _XzBlockBounds(
                    compressed_start=block_compressed_start,
                    decompressed_start=0,  # filled in below
                    unpadded_size=unpadded_size,
                    uncompressed_size=uncompressed_size,
                    check=check,
                )
            )
            block_compressed_start += _round_up_4(unpadded_size)

        all_streams.append(stream_entries)
        compressed_end = stream_header_start

    flat: list[_XzBlockBounds] = []
    for stream_entries in reversed(all_streams):
        flat.extend(stream_entries)

    decomp_offset = start_decompressed_offset
    for block in flat:
        block.decompressed_start = decomp_offset
        decomp_offset += block.uncompressed_size

    return flat


class _XzState:
    """Streaming state machine for multi-stream XZ decompression."""

    _NEED_HEADER = 0
    _IN_STREAM = 1

    def __init__(self) -> None:
        self._state = self._NEED_HEADER
        self._buf = bytearray()
        self._dec: lzma.LZMADecompressor | None = None
        self._bytes_fed = 0
        self._streams_seen = 0
        self._finished = False
        self._stream_decomp_bytes = 0

    def feed(self, data: bytes) -> tuple[bytes, list[tuple[int, int]]]:
        self._buf.extend(data)
        return self._process()

    def flush(self) -> tuple[bytes, list[tuple[int, int]]]:
        if self._state == self._NEED_HEADER:
            if self._streams_seen == 0:
                raise CorruptionError("Not a valid XZ file: no streams found")
            if len(self._buf) >= 6 and bytes(self._buf[:6]) == _XZ_STREAM_MAGIC:
                raise TruncatedError("XZ file truncated mid-header")
            self._finished = True
            return b"", []
        raise TruncatedError("XZ file is truncated mid-stream")

    def is_finished(self) -> bool:
        return self._finished

    def _process(self) -> tuple[bytes, list[tuple[int, int]]]:
        output = bytearray()
        new_streams: list[tuple[int, int]] = []
        while True:
            if self._state == self._NEED_HEADER:
                # XZ spec §2.2 "Stream Padding": concatenated streams may be separated by
                # null bytes whose length is a multiple of four (to keep streams 4-byte
                # aligned). Strip all leading 4-byte runs in one delete.
                padding = 0
                while padding + 4 <= len(self._buf) and bytes(
                    self._buf[padding : padding + 4]
                ) == b"\x00\x00\x00\x00":
                    padding += 4
                if padding:
                    del self._buf[:padding]

                if len(self._buf) < _STREAM_HEADER_SIZE:
                    break
                header = bytes(self._buf[:_STREAM_HEADER_SIZE])
                if header[:6] != _XZ_STREAM_MAGIC:
                    if self._streams_seen == 0:
                        raise CorruptionError(
                            f"Not a valid XZ file: expected magic {_XZ_STREAM_MAGIC!r}, "
                            f"got {header[:6]!r}"
                        )
                    self._finished = True
                    self._buf.clear()
                    break
                del self._buf[:_STREAM_HEADER_SIZE]
                try:
                    self._dec = lzma.LZMADecompressor(format=lzma.FORMAT_XZ)
                    output.extend(self._dec.decompress(header))
                except lzma.LZMAError as e:
                    raise CorruptionError(f"XZ stream header error: {e}") from e
                self._bytes_fed = _STREAM_HEADER_SIZE
                self._stream_decomp_bytes = 0
                self._state = self._IN_STREAM

            elif self._state == self._IN_STREAM:
                if not self._buf:
                    break
                chunk = bytes(self._buf)
                self._buf.clear()
                try:
                    assert self._dec is not None
                    plain = self._dec.decompress(chunk)
                except lzma.LZMAError as e:
                    raise CorruptionError(f"XZ decompression error: {e}") from e
                self._bytes_fed += len(chunk)
                self._stream_decomp_bytes += len(plain)
                output.extend(plain)
                if self._dec.eof:
                    unused = self._dec.unused_data
                    compressed_size = self._bytes_fed - len(unused)
                    new_streams.append((self._stream_decomp_bytes, compressed_size))
                    self._streams_seen += 1
                    self._dec = None
                    self._buf[0:0] = unused
                    self._state = self._NEED_HEADER
        return bytes(output), new_streams


class _XzBlockChain:
    """Block-level decompressor that chains through known XZ block bounds.

    Used once the index is known. Each block is wrapped in a synthetic single-block XZ
    stream and fed to ``LZMADecompressor``. Exposes the same feed/flush/is_finished
    interface as ``_XzState``.
    """

    def __init__(self, blocks: list[_XzBlockBounds], inner: BinaryIO) -> None:
        self._blocks = blocks
        self._inner = inner
        self._block_idx = 0
        self._dec: lzma.LZMADecompressor | None = None
        self._block_bytes_fed = 0
        self._finished = len(blocks) == 0
        if not self._finished:
            self._start_block(0)

    def _start_block(self, idx: int) -> None:
        self._block_idx = idx
        block = self._blocks[idx]
        self._inner.seek(block.compressed_start)
        self._dec = lzma.LZMADecompressor(format=lzma.FORMAT_XZ)
        self._block_bytes_fed = 0
        stream_flags = bytes([0x00, block.check])
        header_crc = zlib.crc32(stream_flags) & 0xFFFFFFFF
        synthetic_header = _XZ_STREAM_MAGIC + stream_flags + struct.pack("<I", header_crc)
        try:
            self._dec.decompress(synthetic_header)
        except lzma.LZMAError as e:
            raise CorruptionError(f"XZ synthetic header error: {e}") from e

    def _build_synthetic_footer(self, block: _XzBlockBounds) -> bytes:
        indicator = b"\x00"
        num_records = _encode_mbi(1)
        unpadded_mbi = _encode_mbi(block.unpadded_size)
        uncompressed_mbi = _encode_mbi(block.uncompressed_size)
        index_body = indicator + num_records + unpadded_mbi + uncompressed_mbi
        padded_len = _round_up_4(len(index_body))
        index_body += b"\x00" * (padded_len - len(index_body))
        index_crc = zlib.crc32(index_body) & 0xFFFFFFFF
        index_bytes = index_body + struct.pack("<I", index_crc)
        backward_size_raw = (len(index_bytes) // 4) - 1
        stream_flags = bytes([0x00, block.check])
        footer_body = struct.pack("<I", backward_size_raw) + stream_flags
        footer_crc = zlib.crc32(footer_body) & 0xFFFFFFFF
        footer = struct.pack("<I", footer_crc) + footer_body + _XZ_FOOTER_MAGIC
        return index_bytes + footer

    def feed(self, data: bytes) -> tuple[bytes, list[tuple[int, int]]]:
        output = bytearray()
        new_streams: list[tuple[int, int]] = []
        pos = 0
        while pos < len(data) and not self._finished:
            block = self._blocks[self._block_idx]
            remaining = _round_up_4(block.unpadded_size) - self._block_bytes_fed
            chunk = data[pos : pos + remaining]
            pos += len(chunk)
            self._block_bytes_fed += len(chunk)
            try:
                assert self._dec is not None
                output.extend(self._dec.decompress(chunk))
            except lzma.LZMAError as e:
                raise CorruptionError(f"XZ block decompression error: {e}") from e
            if self._block_bytes_fed >= _round_up_4(block.unpadded_size):
                synthetic_footer = self._build_synthetic_footer(block)
                try:
                    output.extend(self._dec.decompress(synthetic_footer))
                except lzma.LZMAError as e:
                    raise CorruptionError(f"XZ synthetic footer error: {e}") from e
                new_streams.append(
                    (block.uncompressed_size, _round_up_4(block.unpadded_size))
                )
                self._dec = None
                next_idx = self._block_idx + 1
                if next_idx >= len(self._blocks):
                    self._finished = True
                else:
                    prev_end = block.compressed_start + _round_up_4(block.unpadded_size)
                    self._start_block(next_idx)
                    if self._blocks[next_idx].compressed_start != prev_end:
                        break
        return bytes(output), new_streams

    def flush(self) -> tuple[bytes, list[tuple[int, int]]]:
        if self._finished:
            return b"", []
        raise TruncatedError("XZ block chain is not exhausted at flush")

    def is_finished(self) -> bool:
        return self._finished


class XzDecompressorStream(_SegmentedDecompressorStream["_XzState | _XzBlockChain"]):
    """Seekable XZ decompressor backed by stdlib ``lzma``.

    Builds a block-level seek-point table progressively during forward reads and via a
    one-shot backward scan (on SEEK_END or a forward seek past the known frontier).
    """

    def _make_decompressor(self, point: SeekPoint) -> "_XzState | _XzBlockChain":
        if point.state is None:
            return _XzState()
        start_block: _XzBlockBounds = point.state
        subsequent = [
            sp.state
            for sp in self._seek_points
            if sp.decompressed_offset > point.decompressed_offset and sp.state is not None
        ]
        return _XzBlockChain([start_block, *subsequent], self._inner)

    def _on_completed_segments(self, units: list[tuple[int, int]]) -> None:
        if isinstance(self._decompressor, _XzState):
            self._update_index(units)
        else:
            for decomp_size, comp_size in units:
                self._comp_cursor += comp_size
                self._decomp_cursor += decomp_size

    def _update_index(self, new_streams: list[tuple[int, int]]) -> None:
        for decompressed_size, compressed_size in new_streams:
            stream_comp_start = self._comp_cursor
            stream_decomp_start = self._decomp_cursor
            if stream_decomp_start > 0:
                self.add_seek_points(
                    [SeekPoint(stream_decomp_start, stream_comp_start, state=None)]
                )
            stream_comp_end = stream_comp_start + compressed_size
            if not self._index_built and self._inner.seekable():
                saved_pos = self._inner.tell()
                try:
                    blocks = _read_xz_index_backwards(
                        self._inner,
                        stream_comp_end,
                        stop_at=stream_comp_start,
                        start_decompressed_offset=stream_decomp_start,
                    )
                    block_points = [
                        SeekPoint(b.decompressed_start, b.compressed_start, state=b)
                        for b in blocks
                        if b.decompressed_start > stream_decomp_start
                    ]
                    if block_points:
                        self.add_seek_points(block_points)
                except CorruptionError as e:
                    logger.warning(
                        "XZ per-stream backward scan failed; block-level seek points for "
                        "this stream will not be available: %s",
                        e,
                    )
                finally:
                    self._inner.seek(saved_pos)
            self._comp_cursor = stream_comp_end
            self._decomp_cursor += decompressed_size

    def _build_index(self, last_known: SeekPoint) -> tuple[list[SeekPoint], int | None]:
        return self._build_index_backwards(
            last_known,
            _read_xz_index_backwards,
            lambda b: SeekPoint(b.decompressed_start, b.compressed_start, state=b),
            "XZ backwards index scan failed; falling back to sequential decompression. "
            "Reason: %s",
        )
