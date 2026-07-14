"""Unix-compress (``.Z``) LZW decode via :class:`DecompressorStream`.

The LZW kernel is adapted from `uncompresspy` (https://github.com/kYwzor/uncompresspy),
Copyright (c) 2025 Tiago Gomes, used under the BSD 3-Clause License (see the notice at
the bottom of this file). Archivey wraps it in an :class:`UnixCompressDecoder` so
CLEAR boundaries become :class:`~archivey.internal.streams.decompressor_stream.SeekPoint`s
and forward decode never requires a seekable source.
"""

from __future__ import annotations

import os
from typing import BinaryIO

from archivey.exceptions import CorruptionError, TruncatedError, UnsupportedFeatureError
from archivey.internal.diagnostics_collector import DiagnosticCollector
from archivey.internal.streams.decompressor_stream import (
    BaseDecoder,
    DecodeOut,
    DecompressorStream,
    SeekPoint,
)

_INITIAL_CODE_WIDTH = 9
_INITIAL_MASK = 2**_INITIAL_CODE_WIDTH - 1
_CLEAR_CODE = 256
_MAGIC_BYTE0 = 0x1F
_MAGIC_BYTE1 = 0x9D
_BLOCK_MODE_FLAG = 0x80
_CODE_WIDTH_FLAG = 0x1F
_RESERVED_FLAGS = 0x60
_HEADER_SIZE = 3


def _parse_header(header: bytes) -> tuple[int, bool]:
    """Validate the 3-byte ``.Z`` header → ``(max_width, block_mode)``."""
    if len(header) < _HEADER_SIZE:
        raise CorruptionError("unix-compress (.Z) stream is too short (missing header)")
    if header[0] != _MAGIC_BYTE0 or header[1] != _MAGIC_BYTE1:
        raise CorruptionError(
            f"unix-compress (.Z) magic mismatch (expected {_MAGIC_BYTE0:02x} "
            f"{_MAGIC_BYTE1:02x}, got {header[0]:02x} {header[1]:02x})"
        )
    flag_byte = header[2]
    reserved = flag_byte & _RESERVED_FLAGS
    if reserved:
        raise UnsupportedFeatureError(
            f"unix-compress (.Z) header has unknown reserved flags "
            f"(0x{reserved:02x} in flag byte 0x{flag_byte:02x})"
        )
    max_width = flag_byte & _CODE_WIDTH_FLAG
    if max_width < _INITIAL_CODE_WIDTH:
        raise CorruptionError(
            f"unix-compress (.Z) max code width {max_width} is below the minimum "
            f"of {_INITIAL_CODE_WIDTH}"
        )
    block_mode = bool(flag_byte & _BLOCK_MODE_FLAG)
    return max_width, block_mode


class LzwState:
    """Push LZW decoder: ``feed`` / ``flush`` → ``(bytes, segment_units)``.

    Each CLEAR ends a segment unit ``(decompressed_size, compressed_size)`` measured
    from the previous CLEAR (or from just after the header). Absolute offsets and
    :class:`SeekPoint` registration belong to the stream wrapper.

    Format errors raise :class:`CorruptionError` directly (same pattern as native
    xz/lzip). Unknown reserved header flags raise :class:`UnsupportedFeatureError`.
    At EOF, nonzero leftover bits after the last complete code are a best-effort
    truncation signal (:attr:`truncated`); zero padding is normal for finished streams.
    """

    def __init__(
        self,
        *,
        max_width: int | None = None,
        block_mode: bool | None = None,
    ) -> None:
        self._buf = bytearray()
        self._finished = False
        self._truncated = False
        self._header_params: tuple[int, bool] | None = None
        self._need_header = max_width is None
        self._seg_comp = 0
        self._seg_decomp = 0
        self._pending_skip = 0
        if not self._need_header:
            assert max_width is not None and block_mode is not None
            self._init_dictionary(max_width, block_mode)
            self._header_params = (max_width, block_mode)

    @property
    def header_params(self) -> tuple[int, bool] | None:
        """``(max_width, block_mode)`` once the header is known, else ``None``."""
        return self._header_params

    @property
    def truncated(self) -> bool:
        """True after ``flush`` when leftover bits after the last code were nonzero."""
        return self._truncated

    def feed(self, data: bytes) -> tuple[bytes, list[tuple[int, int]]]:
        if self._finished:
            return b"", []
        self._buf.extend(data)
        return self._process(eof=False)

    def flush(self) -> tuple[bytes, list[tuple[int, int]]]:
        out, units = self._process(eof=True)
        # Finished compressors zero-pad the last incomplete code slot. Nonzero leftover
        # bits are a best-effort truncation / corrupt-padding signal (exact mid-code
        # cuts that leave only zero bits remain undetectable — no length trailer).
        if self._header_params is not None and self._bits_in_buffer > 0:
            leftover = self._bit_buffer & ((1 << self._bits_in_buffer) - 1)
            if leftover:
                self._truncated = True
        if self._header_params is not None:
            del self._dictionary[self._starting_code :]
        self._finished = True
        return out, units

    def is_finished(self) -> bool:
        return self._finished

    def _init_dictionary(self, max_width: int, block_mode: bool) -> None:
        self._max_width = max_width
        self._block_mode = block_mode
        self._dictionary: list[bytes] = [i.to_bytes() for i in range(256)]
        if block_mode:
            self._dictionary.append(b"")
        self._starting_code = len(self._dictionary)
        self._next_code = self._starting_code
        self._bit_buffer = 0
        self._bits_in_buffer = 0
        self._prev_entry: bytes | None = None
        self._code_width = _INITIAL_CODE_WIDTH
        self._current_mask = _INITIAL_MASK
        self._bytes_in_era = 0
        self._codes_in_era = 0
        self._pending_skip = 0

    def _process(self, *, eof: bool) -> tuple[bytes, list[tuple[int, int]]]:
        output = bytearray()
        units: list[tuple[int, int]] = []

        if self._need_header:
            if len(self._buf) < _HEADER_SIZE:
                if eof and self._buf:
                    raise CorruptionError(
                        "unix-compress (.Z) stream is too short (missing header)"
                    )
                return b"", []
            header = bytes(self._buf[:_HEADER_SIZE])
            del self._buf[:_HEADER_SIZE]
            max_width, block_mode = _parse_header(header)
            self._init_dictionary(max_width, block_mode)
            self._header_params = (max_width, block_mode)
            self._need_header = False

        if self._pending_skip:
            skip = min(self._pending_skip, len(self._buf))
            del self._buf[:skip]
            self._pending_skip -= skip
            if self._pending_skip:
                return b"", []

        # Local aliases — hot path (same trick as uncompresspy, ~2x).
        bit_buffer = self._bit_buffer
        bits_in_buffer = self._bits_in_buffer
        code_width = self._code_width
        current_mask = self._current_mask
        next_code = self._next_code
        prev_entry = self._prev_entry
        dictionary = self._dictionary
        max_width = self._max_width
        block_mode = self._block_mode
        starting_code = self._starting_code
        seg_comp = self._seg_comp
        seg_decomp = self._seg_decomp
        # Bytes/codes since the last CLEAR or code-width bump (era start). uncompresspy
        # reads one era-block of size code_width * 2**(code_width-4) at a time; we process
        # push chunks byte-by-byte but keep the same alignment and width-bump rules.
        bytes_in_era = self._bytes_in_era
        codes_in_era = self._codes_in_era
        codes_per_era = 1 << (code_width - 1)

        buf_i = 0
        while buf_i < len(self._buf):
            cur_byte = self._buf[buf_i]
            buf_i += 1
            bytes_in_era += 1
            bit_buffer += cur_byte << bits_in_buffer
            bits_in_buffer += 8
            seg_comp += 1

            cleared = False
            while bits_in_buffer >= code_width:
                code = bit_buffer & current_mask
                bit_buffer >>= code_width
                bits_in_buffer -= code_width
                codes_in_era += 1

                if code == _CLEAR_CODE and block_mode:
                    # Realign to the next code_width-byte boundary within this era,
                    # then unread the remainder (in-memory stand-in for file.seek).
                    # bytes_in_era is the 1-based count of bytes consumed in the era;
                    # uncompresspy's `i` is 0-based within the current read chunk, which
                    # always starts at an era boundary — so i == bytes_in_era - 1.
                    i = bytes_in_era - 1
                    if advanced := i % code_width:
                        i += code_width - advanced
                    era_start = buf_i - bytes_in_era
                    target = era_start + i
                    # Padding forward, or re-read the CLEAR-completing byte when it
                    # already sits on a code_width boundary (matches uncompresspy).
                    if target > buf_i:
                        seg_comp += target - buf_i
                    elif target < buf_i:
                        seg_comp -= buf_i - target
                    units.append((seg_decomp, seg_comp))
                    seg_comp = 0
                    seg_decomp = 0
                    del dictionary[starting_code:]
                    next_code = starting_code
                    code_width = _INITIAL_CODE_WIDTH
                    current_mask = _INITIAL_MASK
                    bit_buffer = 0
                    bits_in_buffer = 0
                    prev_entry = None
                    bytes_in_era = 0
                    codes_in_era = 0
                    codes_per_era = 1 << (code_width - 1)
                    if target > len(self._buf):
                        # CLEAR realignment extends past this feed — skip the rest
                        # of the padding at the start of the next feed.
                        self._pending_skip = target - len(self._buf)
                        buf_i = len(self._buf)
                    else:
                        buf_i = target
                    cleared = True
                    break

                try:
                    entry = dictionary[code]
                except IndexError:
                    if code == next_code:
                        if prev_entry is None:
                            # First code after CLEAR/start must be a literal; KwKwK
                            # needs a previous entry. Corrupt or non-block-mode abuse.
                            raise CorruptionError(
                                f"unix-compress (.Z) invalid code {code} "
                                "(expected a literal)"
                            ) from None
                        entry = prev_entry + prev_entry[:1]
                    else:
                        raise CorruptionError(
                            f"unix-compress (.Z) invalid code {code} in bitstream"
                        ) from None

                output.extend(entry)
                seg_decomp += len(entry)

                if next_code <= current_mask and prev_entry is not None:
                    dictionary.append(prev_entry + entry[:1])
                    next_code += 1

                prev_entry = entry

                if codes_in_era >= codes_per_era and code_width < max_width:
                    code_width += 1
                    current_mask = (1 << code_width) - 1
                    bit_buffer = 0
                    bits_in_buffer = 0
                    bytes_in_era = 0
                    codes_in_era = 0
                    codes_per_era = 1 << (code_width - 1)
                    # Remainder bits from the current byte are discarded (matches
                    # uncompresspy clearing the bit buffer at a width bump).
                    break

            if cleared:
                continue

        del self._buf[:buf_i]

        self._bit_buffer = bit_buffer
        self._bits_in_buffer = bits_in_buffer
        self._code_width = code_width
        self._current_mask = current_mask
        self._next_code = next_code
        self._prev_entry = prev_entry
        self._seg_comp = seg_comp
        self._seg_decomp = seg_decomp
        self._bytes_in_era = bytes_in_era
        self._codes_in_era = codes_in_era
        return bytes(output), units


class UnixCompressDecoder(BaseDecoder):
    """LZW decoder: CLEAR seek points (after-placement) + deferred truncation."""

    def __init__(
        self,
        state: LzwState,
        *,
        comp_cursor: int,
        decomp_cursor: int,
        max_width: int | None = None,
        block_mode: bool | None = None,
        header_committed: bool = False,
    ) -> None:
        self._state = state
        self._comp_cursor = comp_cursor
        self._decomp_cursor = decomp_cursor
        self._max_width = max_width
        self._block_mode = block_mode
        self._header_committed = header_committed

    def recreate(self, point: SeekPoint, inner: BinaryIO) -> UnixCompressDecoder:
        del inner
        if self._max_width is not None and self._block_mode is not None:
            state = LzwState(max_width=self._max_width, block_mode=self._block_mode)
            header_committed = True
        else:
            state = LzwState()
            header_committed = False
        return UnixCompressDecoder(
            state,
            comp_cursor=point.compressed_offset,
            decomp_cursor=point.decompressed_offset,
            max_width=self._max_width,
            block_mode=self._block_mode,
            header_committed=header_committed,
        )

    def feed(self, chunk: bytes) -> DecodeOut:
        data, units = self._state.feed(chunk)
        points = self._commit_header_points()
        points.extend(self._points_for_units(units))
        return DecodeOut(data, points)

    def flush(self) -> DecodeOut:
        data, units = self._state.flush()
        points = self._commit_header_points()
        points.extend(self._points_for_units(units))
        if self._state.truncated:
            self._pending_error = TruncatedError(
                "unix-compress (.Z) stream is truncated (nonzero leftover bits after "
                "the last complete LZW code)"
            )
        return DecodeOut(data, points)

    @property
    def finished(self) -> bool:
        return self._state.is_finished()

    def _commit_header_points(self) -> list[SeekPoint]:
        if self._header_committed:
            return []
        params = self._state.header_params
        if params is None:
            return []
        self._max_width, self._block_mode = params
        self._comp_cursor = _HEADER_SIZE
        self._decomp_cursor = 0
        self._header_committed = True
        # Refine origin: resume after the 3-byte header (same decompressed offset).
        return [SeekPoint(0, _HEADER_SIZE)]

    def _points_for_units(self, units: list[tuple[int, int]]) -> list[SeekPoint]:
        points: list[SeekPoint] = []
        for decomp_size, comp_size in units:
            # After-placement: advance past CLEAR realignment, then emit the point.
            self._comp_cursor += comp_size
            self._decomp_cursor += decomp_size
            points.append(SeekPoint(self._decomp_cursor, self._comp_cursor))
        return points


def UnixCompressDecompressorStream(
    path: str | os.PathLike[str] | BinaryIO,
    *,
    collector: DiagnosticCollector | None = None,
    seekable: bool = True,
) -> DecompressorStream:
    """Seekable unix-compress stream: CLEAR → :class:`SeekPoint` when indexing is on."""

    def make_decoder(point: SeekPoint, inner: BinaryIO) -> UnixCompressDecoder:
        del inner
        return UnixCompressDecoder(
            LzwState(),
            comp_cursor=point.compressed_offset,
            decomp_cursor=point.decompressed_offset,
        )

    return DecompressorStream(
        path,
        make_decoder=make_decoder,
        collector=collector,
        codec_name="unix_compress",
        seekable=seekable,
    )


# ---------------------------------------------------------------------------
# BSD 3-Clause License notice for the adapted LZW kernel (from uncompresspy)
# ---------------------------------------------------------------------------
#
# Copyright (c) 2025, Tiago Gomes
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# 1. Redistributions of source code must retain the above copyright notice, this
#    list of conditions and the following disclaimer.
#
# 2. Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.
#
# 3. Neither the name of the copyright holder nor the names of its
#    contributors may be used to endorse or promote products derived from
#    this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
# OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
