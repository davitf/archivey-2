"""Unix-compress (``.Z``) LZW decode via :class:`DecompressorStream`.

The LZW kernel is adapted from `uncompresspy` (https://github.com/kYwzor/uncompresspy),
Copyright (c) 2025 Tiago Gomes, used under the BSD 3-Clause License (see the notice at
the bottom of this file). Archivey wraps it in :class:`SegmentedDecompressorStream` so
CLEAR boundaries become :class:`~archivey.internal.streams.decompressor_stream.SeekPoint`s
and forward decode never requires a seekable source.
"""

from __future__ import annotations

import os
import warnings
from typing import BinaryIO

from archivey.internal.diagnostics_collector import DiagnosticCollector
from archivey.internal.streams.decompressor_stream import (
    SeekPoint,
    SegmentedDecompressorStream,
)

_INITIAL_CODE_WIDTH = 9
_INITIAL_MASK = 2**_INITIAL_CODE_WIDTH - 1
_CLEAR_CODE = 256
_MAGIC_BYTE0 = 0x1F
_MAGIC_BYTE1 = 0x9D
_BLOCK_MODE_FLAG = 0x80
_CODE_WIDTH_FLAG = 0x1F
_UNKNOWN_FLAGS = 0x60
_HEADER_SIZE = 3


def _parse_header(header: bytes) -> tuple[int, bool]:
    """Validate the 3-byte ``.Z`` header → ``(max_width, block_mode)``."""
    if len(header) < _HEADER_SIZE:
        raise ValueError("File too short, missing header.")
    if header[0] != _MAGIC_BYTE0 or header[1] != _MAGIC_BYTE1:
        raise ValueError(
            f"Invalid file header: Magic bytes do not match (expected {_MAGIC_BYTE0:02x} "
            f"{_MAGIC_BYTE1:02x}, got {header[0]:02x} {header[1]:02x})."
        )
    flag_byte = header[2]
    max_width = flag_byte & _CODE_WIDTH_FLAG
    if max_width < _INITIAL_CODE_WIDTH:
        raise ValueError(
            f"Invalid file header: Max code width less than the minimum of "
            f"{_INITIAL_CODE_WIDTH}."
        )
    if flag_byte & _UNKNOWN_FLAGS:
        warnings.warn(
            "File header contains unknown flags, decompression may be incorrect.",
            RuntimeWarning,
            stacklevel=2,
        )
    block_mode = bool(flag_byte & _BLOCK_MODE_FLAG)
    return max_width, block_mode


class LzwState:
    """Push LZW decoder: ``feed`` / ``flush`` → ``(bytes, segment_units)``.

    Each CLEAR ends a segment unit ``(decompressed_size, compressed_size)`` measured
    from the previous CLEAR (or from just after the header). Absolute offsets and
    :class:`SeekPoint` registration belong to the stream wrapper.
    """

    def __init__(
        self,
        *,
        max_width: int | None = None,
        block_mode: bool | None = None,
        warn_truncation: bool = True,
    ) -> None:
        self._warn_truncation = warn_truncation
        self._buf = bytearray()
        self._finished = False
        self._header_params: tuple[int, bool] | None = None
        self._need_header = max_width is None
        self._seg_comp = 0
        self._seg_decomp = 0
        if not self._need_header:
            if max_width is None or block_mode is None:
                raise ValueError("max_width and block_mode are required together")
            self._init_dictionary(max_width, block_mode)
            self._header_params = (max_width, block_mode)

    @property
    def header_params(self) -> tuple[int, bool] | None:
        """``(max_width, block_mode)`` once the header is known, else ``None``."""
        return self._header_params

    def feed(self, data: bytes) -> tuple[bytes, list[tuple[int, int]]]:
        if self._finished:
            return b"", []
        self._buf.extend(data)
        return self._process(eof=False)

    def flush(self) -> tuple[bytes, list[tuple[int, int]]]:
        out, units = self._process(eof=True)
        if (
            self._warn_truncation
            and self._header_params is not None
            and self._bits_in_buffer >= 8
        ):
            warnings.warn(
                "Bitstream ended in a partial code, file may be truncated.",
                RuntimeWarning,
                stacklevel=2,
            )
        # Release dictionary growth; .Z has no end marker so EOF always finishes.
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

    def _process(self, *, eof: bool) -> tuple[bytes, list[tuple[int, int]]]:
        output = bytearray()
        units: list[tuple[int, int]] = []

        if self._need_header:
            if len(self._buf) < _HEADER_SIZE:
                if eof and self._buf:
                    raise ValueError("File too short, missing header.")
                return b"", []
            header = bytes(self._buf[:_HEADER_SIZE])
            del self._buf[:_HEADER_SIZE]
            max_width, block_mode = _parse_header(header)
            self._init_dictionary(max_width, block_mode)
            self._header_params = (max_width, block_mode)
            self._need_header = False

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

        while True:
            block_size = code_width * (1 << (code_width - 4))
            if not self._buf:
                break
            if len(self._buf) < block_size and not eof:
                break

            cur_chunk = bytes(self._buf[:block_size])
            del self._buf[: len(cur_chunk)]
            cleared = False

            for i, cur_byte in enumerate(cur_chunk):
                bit_buffer += cur_byte << bits_in_buffer
                bits_in_buffer += 8

                if bits_in_buffer < code_width:
                    continue

                code = bit_buffer & current_mask
                bit_buffer >>= code_width
                bits_in_buffer -= code_width

                if code == _CLEAR_CODE and block_mode:
                    # Realign to the next code_width-byte boundary within this chunk,
                    # then unread the remainder (in-memory stand-in for file.seek).
                    if advanced := i % code_width:
                        i += code_width - advanced
                    # Bytes 0..i-1 of cur_chunk are consumed; put i.. back.
                    self._buf[0:0] = cur_chunk[i:]
                    seg_comp += i
                    units.append((seg_decomp, seg_comp))
                    seg_comp = 0
                    seg_decomp = 0
                    # Mirror uncompresspy reset (dict / width / bitbuf).
                    del dictionary[starting_code:]
                    next_code = starting_code
                    code_width = _INITIAL_CODE_WIDTH
                    current_mask = _INITIAL_MASK
                    bit_buffer = 0
                    bits_in_buffer = 0
                    prev_entry = None
                    cleared = True
                    break

                try:
                    entry = dictionary[code]
                except IndexError:
                    if code == next_code:
                        if prev_entry is None:
                            raise ValueError(
                                f"Invalid code {code} encountered in bitstream. "
                                "Expected a literal character."
                            ) from None
                        entry = prev_entry + prev_entry[:1]
                    else:
                        raise ValueError(
                            f"Invalid code {code} encountered in bitstream."
                        ) from None

                output.extend(entry)
                seg_decomp += len(entry)

                if next_code <= current_mask and prev_entry is not None:
                    dictionary.append(prev_entry + entry[:1])
                    next_code += 1

                prev_entry = entry
            else:
                # Full/partial chunk consumed without CLEAR — count all bytes.
                seg_comp += len(cur_chunk)
                if code_width < max_width and len(cur_chunk) == block_size:
                    code_width += 1
                    current_mask = (1 << code_width) - 1
                    bit_buffer = 0
                    bits_in_buffer = 0

            if cleared:
                continue
            if len(cur_chunk) < block_size:
                # Short final chunk at EOF — stop (matches uncompresspy's short read).
                break

        self._bit_buffer = bit_buffer
        self._bits_in_buffer = bits_in_buffer
        self._code_width = code_width
        self._current_mask = current_mask
        self._next_code = next_code
        self._prev_entry = prev_entry
        self._seg_comp = seg_comp
        self._seg_decomp = seg_decomp
        return bytes(output), units


class UnixCompressDecompressorStream(SegmentedDecompressorStream[LzwState]):
    """Seekable unix-compress stream: CLEAR → :class:`SeekPoint` when indexing is on."""

    def __init__(
        self,
        path: str | os.PathLike[str] | BinaryIO,
        *,
        collector: DiagnosticCollector | None = None,
        seekable: bool = True,
    ) -> None:
        self._max_width: int | None = None
        self._block_mode: bool | None = None
        self._header_committed = False
        super().__init__(
            path, collector=collector, codec_name="unix_compress", seekable=seekable
        )

    def _make_decompressor(self, point: SeekPoint) -> LzwState:
        if self._max_width is not None and self._block_mode is not None:
            return LzwState(max_width=self._max_width, block_mode=self._block_mode)
        # Origin open only: header still in the stream at compressed_offset 0.
        return LzwState()

    def _decompress_chunk(self, chunk: bytes) -> bytes:
        data, units = self._decompressor.feed(chunk)
        self._commit_header_if_needed()
        self._on_completed_segments(units)
        return data

    def _flush_decompressor(self) -> bytes:
        data, units = self._decompressor.flush()
        self._commit_header_if_needed()
        self._on_completed_segments(units)
        return data

    def _commit_header_if_needed(self) -> None:
        if self._header_committed:
            return
        params = self._decompressor.header_params
        if params is None:
            return
        self._max_width, self._block_mode = params
        self._comp_cursor = _HEADER_SIZE
        self._decomp_cursor = 0
        if self._seek_points and self._seek_points[0].compressed_offset == 0:
            self._seek_points[0] = SeekPoint(0, _HEADER_SIZE)
        self._header_committed = True

    def _on_completed_segments(self, units: list[tuple[int, int]]) -> None:
        for decomp_size, comp_size in units:
            self._comp_cursor += comp_size
            self._decomp_cursor += decomp_size
            self.add_seek_points(
                [SeekPoint(self._decomp_cursor, self._comp_cursor)]
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
