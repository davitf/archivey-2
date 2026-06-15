"""
XZ decompression utilities: backward index scan, streaming state machine.

XZ binary format (summary):
  A file is a sequence of one or more XZ streams, optionally separated by
  4-byte-aligned null padding.

  Per stream:
    Header  (12 bytes): magic(6) + stream flags(2) + CRC32(4)
    Blocks  (variable): one or more LZMA2 blocks
    Index   (variable): MBI-encoded list of (unpadded_size, uncompressed_size)
                        per block; preceded by a 0x00 indicator byte; followed
                        by padding to a 4-byte boundary; followed by CRC32(4)
    Footer  (12 bytes): CRC32(4) + backward_size(4) + stream flags(2) + magic(2)

  backward_size encodes the index size: actual_bytes = (value + 1) * 4.
  The index CRC32 covers from the indicator byte through the padding.

XZ spec: https://tukaani.org/xz/xz-file-format.txt
"""

import logging
import lzma
import struct
import zlib
from dataclasses import dataclass
from typing import BinaryIO, cast

from archivey.exceptions import ArchiveCorruptedError, ArchiveEOFError
from archivey.formats.decompressor_stream import (
    SeekPoint,
    _SegmentedDecompressorStream,
)

logger = logging.getLogger(__name__)

_XZ_STREAM_MAGIC = b"\xfd7zXZ\x00"
_XZ_FOOTER_MAGIC = b"YZ"
_STREAM_HEADER_SIZE = 12
_STREAM_FOOTER_SIZE = 12


def _round_up_4(n: int) -> int:
    """Round n up to the next multiple of 4."""
    return (n + 3) & ~3


def _decode_mbi(data: bytes, offset: int) -> tuple[int, int]:
    """Decode a multi-byte integer from data starting at offset.

    Returns (value, bytes_consumed).  Raises ArchiveCorruptedError if the
    encoding is invalid (too many bytes or premature end of data).
    """
    value = 0
    shift = 0
    for i in range(9):  # XZ spec: max 9 bytes per MBI
        if offset + i >= len(data):
            raise ArchiveCorruptedError("XZ index MBI truncated")
        byte = data[offset + i]
        value |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            return value, i + 1
        shift += 7
    raise ArchiveCorruptedError("XZ index MBI exceeds 9 bytes")


@dataclass
class _XzBlockBounds:
    """Compressed/decompressed extents for one XZ block."""

    compressed_start: int  # absolute byte offset in the compressed stream (after stream header)
    decompressed_start: int  # cumulative decompressed bytes before this block
    unpadded_size: int  # from the stream index record
    uncompressed_size: int  # from the stream index record
    check: int  # stream check type (from stream flags)

    @property
    def decompressed_end(self) -> int:
        return self.decompressed_start + self.uncompressed_size


def _parse_xz_header(data: bytes) -> int:
    """Parse a 12-byte XZ stream header, returning the check type.

    Raises ArchiveCorruptedError on any validation failure.
    """
    if len(data) < _STREAM_HEADER_SIZE:
        raise ArchiveCorruptedError(
            f"XZ stream header too short: {len(data)} bytes"
        )
    if data[:6] != _XZ_STREAM_MAGIC:
        raise ArchiveCorruptedError(
            f"XZ stream header magic not found (got {data[:6]!r})"
        )
    stream_flags = data[6:8]
    stored_crc = struct.unpack_from("<I", data, 8)[0]
    computed_crc = zlib.crc32(stream_flags) & 0xFFFFFFFF
    if stored_crc != computed_crc:
        raise ArchiveCorruptedError(
            f"XZ stream header CRC32 mismatch: stored {stored_crc:#010x}, "
            f"computed {computed_crc:#010x}"
        )
    # stream flags: byte 0 must be 0; byte 1 low nibble = check type
    if stream_flags[0] != 0:
        raise ArchiveCorruptedError(
            f"XZ stream flags reserved byte is non-zero: {stream_flags[0]:#04x}"
        )
    return stream_flags[1] & 0x0F


def _parse_xz_footer(data: bytes) -> tuple[int, int]:
    """Parse a 12-byte XZ stream footer, returning (check, backward_size_bytes).

    backward_size_bytes is the actual byte count of the index region
    (including padding and the 4-byte CRC at the end of the index).

    Raises ArchiveCorruptedError on any validation failure.
    """
    if len(data) < _STREAM_FOOTER_SIZE:
        raise ArchiveCorruptedError(
            f"XZ stream footer too short: {len(data)} bytes"
        )
    if data[10:12] != _XZ_FOOTER_MAGIC:
        raise ArchiveCorruptedError(
            f"XZ stream footer magic 'YZ' not found (got {data[10:12]!r})"
        )
    stored_crc = struct.unpack_from("<I", data, 0)[0]
    backward_size_raw = struct.unpack_from("<I", data, 4)[0]
    stream_flags = data[8:10]

    computed_crc = zlib.crc32(data[4:10]) & 0xFFFFFFFF
    if stored_crc != computed_crc:
        raise ArchiveCorruptedError(
            f"XZ stream footer CRC32 mismatch: stored {stored_crc:#010x}, "
            f"computed {computed_crc:#010x}"
        )
    if stream_flags[0] != 0:
        raise ArchiveCorruptedError(
            f"XZ stream flags reserved byte is non-zero: {stream_flags[0]:#04x}"
        )
    check = stream_flags[1] & 0x0F
    backward_size_bytes = (backward_size_raw + 1) * 4
    return check, backward_size_bytes


def _parse_xz_index(data: bytes) -> list[tuple[int, int]]:
    """Decode XZ index bytes into a list of (unpadded_size, uncompressed_size).

    data must be the raw index bytes starting from the 0x00 indicator byte,
    through the padding, up to but NOT including the 4-byte CRC32.

    Raises ArchiveCorruptedError on any validation failure.
    """
    if not data or data[0] != 0x00:
        raise ArchiveCorruptedError(
            f"XZ index indicator byte expected 0x00, got {data[0]:#04x}"
        )

    offset = 1  # skip indicator byte
    num_records, consumed = _decode_mbi(data, offset)
    offset += consumed

    records: list[tuple[int, int]] = []
    for _ in range(num_records):
        unpadded_size, consumed = _decode_mbi(data, offset)
        offset += consumed
        uncompressed_size, consumed = _decode_mbi(data, offset)
        offset += consumed
        if unpadded_size == 0:
            raise ArchiveCorruptedError("XZ index: unpadded_size must be > 0")
        records.append((unpadded_size, uncompressed_size))

    # Padding to 4-byte boundary (from start of index, including indicator byte)
    padded_len = _round_up_4(offset)
    for i in range(offset, padded_len):
        if i < len(data) and data[i] != 0:
            raise ArchiveCorruptedError(
                f"XZ index padding byte {i} is non-zero: {data[i]:#04x}"
            )

    return records


def _read_xz_index_backwards(
    stream: BinaryIO,
    file_size: int,
    stop_at: int = 0,
    start_decompressed_offset: int = 0,
) -> list[_XzBlockBounds]:
    """Walk all XZ streams from EOF toward stop_at, building a block index.

    Reads only stream footers and block indices — no decompression is performed.
    Returns all blocks in forward order with correct absolute compressed_start
    and decompressed_start values.

    Raises ArchiveCorruptedError on any structural failure.
    """
    all_entries: list[_XzBlockBounds] = []
    compressed_end = file_size

    while compressed_end > stop_at:
        # Skip 4-byte-aligned null padding before the footer
        while compressed_end > stop_at:
            if compressed_end < 4:
                raise ArchiveCorruptedError(
                    "XZ file too small to contain a valid stream"
                )
            stream.seek(compressed_end - 4)
            tail = stream.read(4)
            if len(tail) < 4:
                raise ArchiveCorruptedError("XZ file truncated during backward scan")
            if tail != b"\x00\x00\x00\x00":
                break
            compressed_end -= 4

        if compressed_end <= stop_at:
            break

        # Read and parse the 12-byte footer
        if compressed_end < _STREAM_FOOTER_SIZE:
            raise ArchiveCorruptedError(
                f"Not enough bytes for XZ footer at offset {compressed_end}"
            )
        stream.seek(compressed_end - _STREAM_FOOTER_SIZE)
        footer_data = stream.read(_STREAM_FOOTER_SIZE)
        if len(footer_data) < _STREAM_FOOTER_SIZE:
            raise ArchiveCorruptedError("XZ footer truncated")
        check, index_size_bytes = _parse_xz_footer(footer_data)

        # Read the index (includes indicator byte, MBIs, padding; excludes CRC32)
        # The full index region = index_bytes + 4-byte CRC, all = index_size_bytes
        # So raw index = index_size_bytes - 4
        index_end = compressed_end - _STREAM_FOOTER_SIZE
        index_with_crc_start = index_end - index_size_bytes
        if index_with_crc_start < 0:
            raise ArchiveCorruptedError("XZ index extends before start of file")

        stream.seek(index_with_crc_start)
        index_with_crc = stream.read(index_size_bytes)
        if len(index_with_crc) < index_size_bytes:
            raise ArchiveCorruptedError("XZ index truncated")

        raw_index = index_with_crc[:-4]
        stored_index_crc = struct.unpack_from("<I", index_with_crc, len(index_with_crc) - 4)[0]
        computed_index_crc = zlib.crc32(raw_index) & 0xFFFFFFFF
        if stored_index_crc != computed_index_crc:
            raise ArchiveCorruptedError(
                f"XZ index CRC32 mismatch: stored {stored_index_crc:#010x}, "
                f"computed {computed_index_crc:#010x}"
            )

        records = _parse_xz_index(raw_index)

        # Compute block layout within this stream
        blocks_compressed_total = sum(_round_up_4(r[0]) for r in records)
        stream_header_start = index_with_crc_start - blocks_compressed_total - _STREAM_HEADER_SIZE
        if stream_header_start < 0:
            raise ArchiveCorruptedError(
                "XZ stream header start computes to negative offset"
            )

        # Verify the stream header magic
        stream.seek(stream_header_start)
        header_data = stream.read(_STREAM_HEADER_SIZE)
        if len(header_data) < _STREAM_HEADER_SIZE:
            raise ArchiveCorruptedError("XZ stream header truncated")
        header_check = _parse_xz_header(header_data)
        if header_check != check:
            raise ArchiveCorruptedError(
                f"XZ stream header check {header_check} != footer check {check}"
            )

        # Emit block bounds for this stream (in reverse order, will be reversed later)
        stream_entries: list[_XzBlockBounds] = []
        block_compressed_start = stream_header_start + _STREAM_HEADER_SIZE
        for unpadded_size, uncompressed_size in records:
            stream_entries.append(
                _XzBlockBounds(
                    compressed_start=block_compressed_start,
                    decompressed_start=0,  # placeholder, filled in below
                    unpadded_size=unpadded_size,
                    uncompressed_size=uncompressed_size,
                    check=check,
                )
            )
            block_compressed_start += _round_up_4(unpadded_size)

        all_entries.append(stream_entries)  # type: ignore[arg-type]
        compressed_end = stream_header_start

    # Flatten streams in forward order (we collected them backwards)
    flat: list[_XzBlockBounds] = []
    for stream_entries in reversed(all_entries):  # type: ignore[arg-type]
        flat.extend(stream_entries)

    # Fill in decompressed_start values
    decomp_offset = start_decompressed_offset
    for block in flat:
        block.decompressed_start = decomp_offset
        decomp_offset += block.uncompressed_size

    return flat


# ---------------------------------------------------------------------------
# _XzState — forward streaming state machine
# ---------------------------------------------------------------------------

class _XzState:
    """Streaming state machine for multi-stream XZ decompression.

    feed(chunk) returns (decompressed_bytes, new_streams) where new_streams is
    a list of (decompressed_size, compressed_size) for each completed stream.
    flush() does the same for end-of-input.

    States: NEED_HEADER → IN_STREAM → NEED_HEADER → ...
    """

    _NEED_HEADER = 0
    _IN_STREAM = 1

    def __init__(self) -> None:
        self._state = self._NEED_HEADER
        self._buf = bytearray()
        self._dec: lzma.LZMADecompressor | None = None
        self._bytes_fed: int = 0  # bytes fed to current decompressor
        self._streams_seen: int = 0
        self._finished: bool = False
        self._stream_decomp_bytes: int = 0  # decompressed bytes in current stream

    def feed(self, data: bytes) -> tuple[bytes, list[tuple[int, int]]]:
        self._buf.extend(data)
        return self._process()

    def flush(self) -> tuple[bytes, list[tuple[int, int]]]:
        if self._state == self._NEED_HEADER:
            if self._streams_seen == 0:
                raise ArchiveCorruptedError("Not a valid XZ file: no streams found")
            if len(self._buf) >= 6 and bytes(self._buf[:6]) == _XZ_STREAM_MAGIC:
                raise ArchiveEOFError("XZ file truncated mid-header")
            self._finished = True
            return b"", []
        raise ArchiveEOFError("XZ file is truncated mid-stream")

    def is_finished(self) -> bool:
        return self._finished

    def _process(self) -> tuple[bytes, list[tuple[int, int]]]:
        output = bytearray()
        new_streams: list[tuple[int, int]] = []

        while True:
            if self._state == self._NEED_HEADER:
                # Skip 4-byte-aligned null padding between streams
                while len(self._buf) >= 4 and self._buf[0] == 0:
                    # consume nulls 4 bytes at a time
                    if self._buf[:4] == b"\x00\x00\x00\x00":
                        del self._buf[:4]
                    else:
                        break

                if len(self._buf) < _STREAM_HEADER_SIZE:
                    break

                header = bytes(self._buf[:_STREAM_HEADER_SIZE])
                if header[:6] != _XZ_STREAM_MAGIC:
                    if self._streams_seen == 0:
                        raise ArchiveCorruptedError(
                            f"Not a valid XZ file: expected magic {_XZ_STREAM_MAGIC!r}, "
                            f"got {header[:6]!r}"
                        )
                    # Trailing non-XZ data after valid streams — stop gracefully
                    self._finished = True
                    self._buf.clear()
                    break

                del self._buf[:_STREAM_HEADER_SIZE]
                try:
                    self._dec = lzma.LZMADecompressor(format=lzma.FORMAT_XZ)
                    # Feed the header so the decompressor sees a complete XZ stream
                    out = self._dec.decompress(header)
                    output.extend(out)
                except lzma.LZMAError as e:
                    raise ArchiveCorruptedError(
                        f"XZ stream header error: {e}"
                    ) from e
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
                    raise ArchiveCorruptedError(
                        f"XZ decompression error: {e}"
                    ) from e
                self._bytes_fed += len(chunk)
                self._stream_decomp_bytes += len(plain)
                output.extend(plain)

                if self._dec.eof:
                    unused = self._dec.unused_data
                    compressed_size = self._bytes_fed - len(unused)
                    decompressed_size = self._stream_decomp_bytes
                    new_streams.append((decompressed_size, compressed_size))
                    self._streams_seen += 1
                    self._dec = None
                    self._buf[0:0] = unused
                    self._state = self._NEED_HEADER

        return bytes(output), new_streams


# ---------------------------------------------------------------------------
# _XzBlockChain — block-level decompressor for seek points
# ---------------------------------------------------------------------------

class _XzBlockChain:
    """Block-level decompressor that chains through a list of XZ block bounds.

    Used after the index is known. Each block is wrapped in a synthetic XZ
    stream (header + block bytes + index + footer) and fed to LZMADecompressor.

    Exposes the same feed()/flush()/is_finished() interface as _XzState so
    the base class can use either transparently.
    """

    def __init__(self, blocks: list[_XzBlockBounds], inner: BinaryIO) -> None:
        self._blocks = blocks
        self._inner = inner
        self._block_idx = 0
        self._dec: lzma.LZMADecompressor | None = None
        self._block_bytes_fed: int = 0  # compressed bytes fed for current block
        self._finished = len(blocks) == 0

        if not self._finished:
            self._start_block(0)

    def _start_block(self, idx: int) -> None:
        """Set up decompressor for block idx, seeking inner stream to the block start.

        The seek is a no-op for contiguous blocks (single-stream multi-block XZ)
        but necessary for multi-stream XZ where index/footer/stream-header bytes
        separate consecutive blocks in different streams.
        """
        self._block_idx = idx
        block = self._blocks[idx]
        self._inner.seek(block.compressed_start)
        self._dec = lzma.LZMADecompressor(format=lzma.FORMAT_XZ)
        self._block_bytes_fed = 0

        # Build synthetic XZ header for this block's stream flags
        stream_flags = bytes([0x00, block.check])
        header_crc = zlib.crc32(stream_flags) & 0xFFFFFFFF
        synthetic_header = _XZ_STREAM_MAGIC + stream_flags + struct.pack("<I", header_crc)

        try:
            out = self._dec.decompress(synthetic_header)
            # The header bytes won't produce output, but we count them as fed
            # so we know how many real block bytes remain.
            _ = out
        except lzma.LZMAError as e:
            raise ArchiveCorruptedError(f"XZ synthetic header error: {e}") from e

    def _build_synthetic_footer(self, block: _XzBlockBounds) -> bytes:
        """Build the synthetic index+footer for a block to close the stream."""
        # Index: indicator(1) + num_records MBI(1) + unpadded MBI + uncompressed MBI
        # + padding to 4-byte boundary + CRC32(4)
        indicator = b"\x00"
        num_records = _encode_mbi(1)
        unpadded_mbi = _encode_mbi(block.unpadded_size)
        uncompressed_mbi = _encode_mbi(block.uncompressed_size)
        index_body = indicator + num_records + unpadded_mbi + uncompressed_mbi
        # Pad to 4-byte boundary
        padded_len = _round_up_4(len(index_body))
        index_body += b"\x00" * (padded_len - len(index_body))
        index_crc = zlib.crc32(index_body) & 0xFFFFFFFF
        index_bytes = index_body + struct.pack("<I", index_crc)

        # Footer: CRC32(4) + backward_size(4) + stream_flags(2) + magic(2)
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
            # How many more compressed block bytes can this block accept?
            remaining = _round_up_4(block.unpadded_size) - self._block_bytes_fed
            chunk = data[pos : pos + remaining]
            pos += len(chunk)
            self._block_bytes_fed += len(chunk)

            try:
                assert self._dec is not None
                plain = self._dec.decompress(chunk)
                output.extend(plain)
            except lzma.LZMAError as e:
                raise ArchiveCorruptedError(f"XZ block decompression error: {e}") from e

            # If we've fed all the block's bytes, inject the synthetic footer
            if self._block_bytes_fed >= _round_up_4(block.unpadded_size):
                synthetic_footer = self._build_synthetic_footer(block)
                try:
                    plain = self._dec.decompress(synthetic_footer)
                    output.extend(plain)
                except lzma.LZMAError as e:
                    raise ArchiveCorruptedError(
                        f"XZ synthetic footer error: {e}"
                    ) from e

                new_streams.append((block.uncompressed_size, _round_up_4(block.unpadded_size)))
                self._dec = None

                next_idx = self._block_idx + 1
                if next_idx >= len(self._blocks):
                    self._finished = True
                else:
                    prev_end = block.compressed_start + _round_up_4(block.unpadded_size)
                    self._start_block(next_idx)
                    # For multi-stream XZ the next block is not contiguous
                    # (index/footer/stream-header bytes intervene).  Stop consuming
                    # the current chunk so the next read comes from the seeked position.
                    if self._blocks[next_idx].compressed_start != prev_end:
                        break

        return bytes(output), new_streams

    def flush(self) -> tuple[bytes, list[tuple[int, int]]]:
        if self._finished:
            return b"", []
        raise ArchiveEOFError("XZ block chain is not exhausted at flush")

    def is_finished(self) -> bool:
        return self._finished


def _encode_mbi(value: int) -> bytes:
    """Encode a non-negative integer as an XZ multi-byte integer."""
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


# ---------------------------------------------------------------------------
# XzDecompressorStream — seekable stream
# ---------------------------------------------------------------------------


class XzDecompressorStream(_SegmentedDecompressorStream["_XzState | _XzBlockChain"]):
    """Seekable XZ decompressor backed by Python's stdlib lzma.

    Builds a block-level seek-point table:
      - Progressively as XZ streams complete during forward reads.
      - On-demand via _build_index (triggered by SEEK_END or forward seek past frontier).

    SeekPoint.state is None             →  stream-level decompressor (_XzState),
                                           used only for the initial SeekPoint(0, 0)
                                           before any index is known.
    SeekPoint.state is _XzBlockBounds   →  block metadata; uses _XzBlockChain for
                                           block-level random access.
    """

    def _make_decompressor(self, point: SeekPoint) -> "_XzState | _XzBlockChain":
        if point.state is None:
            return _XzState()
        # point.state and every subsequent block-level point hold an _XzBlockBounds
        # (with absolute offsets), so _XzBlockChain can consume them directly.
        start_block: _XzBlockBounds = point.state
        subsequent = [
            sp.state
            for sp in self._seek_points
            if sp.decompressed_offset > point.decompressed_offset
            and sp.state is not None
        ]
        return _XzBlockChain([start_block, *subsequent], cast("BinaryIO", self._inner))

    def _on_completed_segments(self, units: list[tuple[int, int]]) -> None:
        if isinstance(self._decompressor, _XzState):
            self._update_index(units)
        else:
            for decomp_size, comp_size in units:
                self._comp_cursor += comp_size
                self._decomp_cursor += decomp_size

    def _update_index(self, new_streams: list[tuple[int, int]]) -> None:
        """Extend seek points with newly completed streams (only called for _XzState).

        For each completed stream: adds a stream-level SeekPoint, then scans that
        stream's compressed range backwards to populate block-level seek points.
        """
        for decompressed_size, compressed_size in new_streams:
            stream_comp_start = self._comp_cursor
            stream_decomp_start = self._decomp_cursor

            # Skip for stream 0 — SeekPoint(0, 0) already covers it
            if stream_decomp_start > 0:
                self.add_seek_points(
                    [SeekPoint(stream_decomp_start, stream_comp_start, state=None)]
                )

            stream_comp_end = stream_comp_start + compressed_size

            if not self._index_built and self._inner.seekable():
                saved_pos = self._inner.tell()
                try:
                    blocks = _read_xz_index_backwards(
                        cast("BinaryIO", self._inner),
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
                except ArchiveCorruptedError as e:
                    logger.warning(
                        "XZ per-stream backward scan failed, block-level seek points "
                        "for this stream will not be available: %s",
                        e,
                    )
                finally:
                    self._inner.seek(saved_pos)

            self._comp_cursor = stream_comp_end
            self._decomp_cursor += decompressed_size

    def _build_index(self, last_known: SeekPoint) -> tuple[list[SeekPoint], int | None]:
        """Full backwards scan from EOF to last_known, building block seek points."""
        return self._build_index_backwards(
            last_known,
            _read_xz_index_backwards,
            lambda b: SeekPoint(b.decompressed_start, b.compressed_start, state=b),
            "XZ backwards index scan failed; falling back to sequential "
            "decompression. Reason: %s",
        )
