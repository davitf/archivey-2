"""
Pure-stdlib seekable lzip decompression over Python's ``lzma`` module.

lzip format (per the lzip manual): a file is a sequence of one or more *members* that may
be concatenated freely. Each member carries its own sizes in a 20-byte trailer (the
"distributed index"), enabling random access without a separate index.

  Per member:
    Header  (6 bytes):  magic b"LZIP"(4) + version(1, must be 1) +
                        coded_dict(1; dict_size = 1 << (coded_dict & 0x1F), exp 12-29)
    LZMA1 data:         raw LZMA1 with an end-of-stream marker, fixed lc=3,lp=0,pb=2.
                        The 13-byte LZMA_ALONE header is absent and synthesised here.
    Trailer (20 bytes): crc32(4 LE) + data_size(8 LE) + member_size(8 LE)

Spec: https://www.nongnu.org/lzip/manual/lzip_manual.html#File-format
"""

from __future__ import annotations

import lzma
import os
import struct
import zlib
from dataclasses import dataclass
from typing import BinaryIO

from archivey.exceptions import CorruptionError, TruncatedError
from archivey.internal.diagnostics_collector import DiagnosticCollector
from archivey.internal.hashing import crc32_combine
from archivey.internal.streams.decompressor_stream import (
    BaseDecoder,
    DecodeOut,
    DecompressorStream,
    SeekPoint,
    build_index_backwards,
)

_MAGIC = b"LZIP"
_HEADER_SIZE = 6
_TRAILER_SIZE = 20

# lzip mandates lc=3, lp=0, pb=2: props byte = (pb*5 + lp)*9 + lc = 93 = 0x5D.
_PROPS_BYTE = bytes([0x5D])
# "uncompressed size unknown" sentinel — relies on the in-stream EOS marker.
_UNKNOWN_SIZE = b"\xff" * 8


@dataclass
class _MemberBounds:
    compressed_start: int
    decompressed_start: int
    compressed_size: int
    decompressed_size: int
    crc32: int

    @property
    def decompressed_end(self) -> int:
        return self.decompressed_start + self.decompressed_size


def _read_index_backwards(
    stream: BinaryIO,
    file_size: int,
    stop_at: int = 0,
    start_decompressed_offset: int = 0,
) -> list[_MemberBounds]:
    """Build the member index by scanning trailers backwards (no decompression).

    A 4-byte magic check at each computed member start catches a corrupt ``member_size``
    before it cascades into wrong offsets for every earlier member. Each entry retains the
    trailer CRC-32 so callers can combine a whole-stream digest without decompressing.
    """
    entries: list[tuple[int, int, int, int]] = []
    compressed_end = file_size

    while compressed_end > stop_at:
        if compressed_end < _TRAILER_SIZE:
            raise CorruptionError("Lzip file is too small to contain a valid trailer")
        stream.seek(compressed_end - _TRAILER_SIZE)
        trailer = stream.read(_TRAILER_SIZE)
        if len(trailer) < _TRAILER_SIZE:
            raise CorruptionError("Lzip file truncated during backward index scan")
        crc32, data_size, member_size = struct.unpack_from("<IQQ", trailer, 0)
        if member_size < _HEADER_SIZE + _TRAILER_SIZE:
            raise CorruptionError(
                f"Lzip member_size {member_size} in trailer is too small to be valid"
            )
        compressed_start = compressed_end - member_size
        if compressed_start < 0:
            raise CorruptionError(
                f"Lzip member_size {member_size} exceeds remaining file size"
            )
        stream.seek(compressed_start)
        magic = stream.read(4)
        if magic != _MAGIC:
            raise CorruptionError(
                f"Lzip magic not found at expected member start {compressed_start} "
                f"(got {magic!r}); member_size in trailer may be corrupt"
            )
        entries.append((compressed_start, int(data_size), int(member_size), int(crc32)))
        compressed_end = compressed_start

    result: list[_MemberBounds] = []
    decompressed_offset = start_decompressed_offset
    for comp_start, decomp_size, comp_size, crc32 in reversed(entries):
        result.append(
            _MemberBounds(
                comp_start, decompressed_offset, comp_size, decomp_size, crc32
            )
        )
        decompressed_offset += decomp_size
    return result


def combined_crc32_from_index(members: list[_MemberBounds]) -> int:
    """Whole-stream CRC-32 = ``crc32(concat(payloads))`` via trailer combine."""
    crc = 0
    for member in members:
        crc = crc32_combine(crc, member.crc32, member.decompressed_size)
    return crc


def peek_combined_crc32(stream: BinaryIO, file_size: int) -> int:
    """Scan the lzip index and return the combined whole-stream CRC-32."""
    return combined_crc32_from_index(_read_index_backwards(stream, file_size))


class _LzipState:
    """Streaming state machine for multi-member lzip decompression.

    Cycles per member: NEED_HEADER → IN_MEMBER → NEED_TRAILER → NEED_HEADER. ``feed`` and
    ``flush`` return ``(decompressed_bytes, completed_members)`` where each completed
    member is ``(decompressed_size, compressed_size)``.
    """

    _NEED_HEADER = 0
    _IN_MEMBER = 1
    _NEED_TRAILER = 2

    def __init__(self) -> None:
        self._state = self._NEED_HEADER
        self._buf = bytearray()
        self._dec: lzma.LZMADecompressor | None = None
        self._crc = 0
        self._member_size = 0
        self._finished = False
        self._members_seen = 0

    def feed(
        self, data: bytes, max_length: int = -1
    ) -> tuple[bytes, list[tuple[int, int]]]:
        self._buf.extend(data)
        return self._process(max_length=max_length)

    def flush(self) -> tuple[bytes, list[tuple[int, int]]]:
        if self._state == self._NEED_HEADER:
            if self._members_seen == 0 and not self._buf:
                raise CorruptionError("Not a valid lzip file: no members found")
            if len(self._buf) >= 4 and self._buf[:4] == _MAGIC:
                raise TruncatedError("Lzip file truncated mid-header")
            self._finished = True
            return b"", []
        raise TruncatedError("Lzip file is truncated")

    def is_finished(self) -> bool:
        return self._finished

    @property
    def needs_input(self) -> bool:
        if self._finished:
            return True
        if self._dec is not None and not self._dec.needs_input:
            return False
        return not self._buf

    def _process(self, max_length: int = -1) -> tuple[bytes, list[tuple[int, int]]]:
        output = bytearray()
        new_members: list[tuple[int, int]] = []
        while True:
            if max_length >= 0 and len(output) >= max_length:
                break
            if self._state == self._NEED_HEADER:
                if len(self._buf) < _HEADER_SIZE:
                    break
                header = bytes(self._buf[:_HEADER_SIZE])
                del self._buf[:_HEADER_SIZE]
                if not self._start_member(header):
                    self._buf.clear()  # discard valid trailing data
                    break
                self._state = self._IN_MEMBER

            elif self._state == self._IN_MEMBER:
                assert self._dec is not None
                if not self._buf and self._dec.needs_input:
                    break
                chunk = bytes(self._buf)
                self._buf.clear()
                try:
                    remaining = max_length - len(output) if max_length >= 0 else -1
                    plain = self._dec.decompress(chunk, remaining)
                except lzma.LZMAError as e:
                    raise CorruptionError(f"Error reading Lzip archive: {e}") from e
                if plain:
                    self._crc = zlib.crc32(plain, self._crc)
                    self._member_size += len(plain)
                    output.extend(plain)
                if self._dec.eof:
                    self._buf[0:0] = self._dec.unused_data
                    self._dec = None
                    self._state = self._NEED_TRAILER
                elif not self._dec.needs_input:
                    if not plain and not chunk:
                        break
                    continue
                else:
                    break

            elif self._state == self._NEED_TRAILER:
                if len(self._buf) < _TRAILER_SIZE:
                    break
                trailer = bytes(self._buf[:_TRAILER_SIZE])
                del self._buf[:_TRAILER_SIZE]
                new_members.append(self._verify_trailer(trailer))
                self._state = self._NEED_HEADER
        return bytes(output), new_members

    def _start_member(self, header: bytes) -> bool:
        """Initialise a new member; return ``False`` on valid trailing data after a member."""
        if header[:4] != _MAGIC:
            if self._members_seen == 0:
                raise CorruptionError(
                    f"Not a valid lzip file: expected magic {_MAGIC!r}, got {header[:4]!r}"
                )
            self._finished = (
                True  # lzip spec §7: trailing data after members is allowed
            )
            return False
        if header[4] != 1:
            raise CorruptionError(f"Unsupported lzip version: {header[4]}")
        exp = header[5] & 0x1F
        if not (12 <= exp <= 29):
            raise CorruptionError(
                f"Invalid lzip dict_size exponent {exp}: valid range is 12-29"
            )
        dict_size = 1 << exp
        lzma_alone_header = _PROPS_BYTE + struct.pack("<I", dict_size) + _UNKNOWN_SIZE
        try:
            self._dec = lzma.LZMADecompressor(format=lzma.FORMAT_ALONE)
            self._dec.decompress(lzma_alone_header)
        except lzma.LZMAError as e:
            raise CorruptionError(f"Error reading lzip header: {e}") from e
        self._crc = 0
        self._member_size = 0
        return True

    def _verify_trailer(self, trailer: bytes) -> tuple[int, int]:
        crc32_stored, data_size, member_size = struct.unpack_from("<IQQ", trailer, 0)
        if (self._crc & 0xFFFFFFFF) != crc32_stored:
            raise CorruptionError(
                f"Lzip CRC32 mismatch: stored {crc32_stored:#010x}, "
                f"computed {self._crc & 0xFFFFFFFF:#010x}"
            )
        if self._member_size != data_size:
            raise CorruptionError(
                f"Lzip size mismatch: stored {data_size}, actual {self._member_size}"
            )
        self._members_seen += 1
        return (int(data_size), int(member_size))


class LzipDecoder(BaseDecoder):
    """Lzip decoder: member-start seek points (before-placement) + trailer index."""

    def __init__(
        self,
        state: _LzipState,
        *,
        comp_cursor: int,
        decomp_cursor: int,
        collector: DiagnosticCollector | None,
    ) -> None:
        self._state = state
        self._comp_cursor = comp_cursor
        self._decomp_cursor = decomp_cursor
        self._collector = collector

    def recreate(self, point: SeekPoint, inner: BinaryIO) -> LzipDecoder:
        del inner
        return LzipDecoder(
            _LzipState(),
            comp_cursor=point.compressed_offset,
            decomp_cursor=point.decompressed_offset,
            collector=self._collector,
        )

    def feed(self, chunk: bytes, max_length: int = -1) -> DecodeOut:
        data, units = self._state.feed(chunk, max_length=max_length)
        return DecodeOut(data, self._points_for_units(units))

    def flush(self) -> DecodeOut:
        data, units = self._state.flush()
        return DecodeOut(data, self._points_for_units(units))

    @property
    def finished(self) -> bool:
        return self._state.is_finished()

    @property
    def needs_input(self) -> bool:
        return self._state.needs_input

    def _points_for_units(self, units: list[tuple[int, int]]) -> list[SeekPoint]:
        points: list[SeekPoint] = []
        for decompressed_size, compressed_size in units:
            # Before-placement: point at member start, then advance cursors.
            points.append(SeekPoint(self._decomp_cursor, self._comp_cursor))
            self._comp_cursor += compressed_size
            self._decomp_cursor += decompressed_size
        return points

    def build_index(
        self, inner: BinaryIO, last_known: SeekPoint
    ) -> tuple[list[SeekPoint], int | None]:
        return build_index_backwards(
            inner,
            last_known,
            _read_index_backwards,
            lambda m: SeekPoint(m.decompressed_start, m.compressed_start),
            "Lzip backwards index scan failed (the file may have trailing data after the "
            "last member, which is valid per the lzip spec); falling back to sequential "
            "decompression. Reason: %s",
            codec_name="lzip",
            collector=self._collector,
            scan="backwards_trailer",
        )


def LzipDecompressorStream(
    path: str | os.PathLike[str] | BinaryIO,
    *,
    collector: DiagnosticCollector | None = None,
    seekable: bool = True,
) -> DecompressorStream:
    """Seekable lzip decompressor backed by stdlib ``lzma``."""

    def make_decoder(point: SeekPoint, inner: BinaryIO) -> LzipDecoder:
        del inner
        return LzipDecoder(
            _LzipState(),
            comp_cursor=point.compressed_offset,
            decomp_cursor=point.decompressed_offset,
            collector=collector,
        )

    return DecompressorStream(
        path,
        make_decoder=make_decoder,
        collector=collector,
        codec_name="lzip",
        seekable=seekable,
    )
