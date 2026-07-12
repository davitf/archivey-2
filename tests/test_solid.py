"""Contract for :class:`SolidBlockReader` — the sequential solid-block demux.

This primitive backs 7z solid-folder iteration and is intended for RAR's ``unrar p``
pipe too, so its lazy-skip / no-drain-on-close behaviour is pinned here directly rather
than only through the 7z reader.
"""

from __future__ import annotations

import io

import pytest

from archivey.internal.streams.streamtools import SolidBlockReader, skip_forward


class _CountingBlock(io.BytesIO):
    """A BytesIO that records how many bytes were read and whether it was closed."""

    def __init__(self, data: bytes) -> None:
        super().__init__(data)
        self.bytes_read = 0
        self.was_closed = False

    def read(self, n: int = -1, /) -> bytes:
        data = super().read(n)
        self.bytes_read += len(data)
        return data

    def close(self) -> None:
        self.was_closed = True
        super().close()


def test_consecutive_members_read_in_order() -> None:
    block = _CountingBlock(b"AAAABBBBBCC")
    reader = SolidBlockReader(block)
    assert reader.open_member(0, 4).read() == b"AAAA"
    assert reader.open_member(4, 5).read() == b"BBBBB"
    assert reader.open_member(9, 2).read() == b"CC"


def test_partial_read_then_next_member_lazily_skips_tail() -> None:
    block = _CountingBlock(b"AAAABBBB")
    reader = SolidBlockReader(block)
    first = reader.open_member(0, 4)
    assert first.read(1) == b"A"  # only one byte consumed from the first member
    # Opening the next member skips the first member's unread tail (the lazy drain).
    assert reader.open_member(4, 4).read() == b"BBBB"


def test_close_does_not_drain_the_block() -> None:
    block = _CountingBlock(b"AAAABBBB")
    reader = SolidBlockReader(block)
    reader.open_member(0, 4).read(1)  # read a single byte, leave the rest
    reader.close()
    # Only the one requested byte was ever read; close discards the block without draining.
    assert block.bytes_read == 1
    assert block.was_closed is True


def test_out_of_order_open_raises() -> None:
    block = _CountingBlock(b"AAAABBBB")
    reader = SolidBlockReader(block)
    reader.open_member(4, 4).read()
    with pytest.raises(ValueError, match="in order"):
        reader.open_member(0, 4)


def test_gap_between_members_is_skipped() -> None:
    block = _CountingBlock(b"AA__BB")  # bytes 2..4 belong to no member
    reader = SolidBlockReader(block)
    assert reader.open_member(0, 2).read() == b"AA"
    assert reader.open_member(4, 2).read() == b"BB"


def test_truncated_block_raises_eof_on_skip() -> None:
    block = _CountingBlock(b"AAAA")  # only 4 bytes, second member starts past the end
    reader = SolidBlockReader(block)
    reader.open_member(0, 4).read(1)
    with pytest.raises(EOFError):
        reader.open_member(8, 2)  # skip of 7 bytes over a 3-byte remainder


def test_close_block_false_leaves_block_open() -> None:
    block = _CountingBlock(b"AAAA")
    reader = SolidBlockReader(block, close_block=False)
    reader.open_member(0, 4).read()
    reader.close()
    assert block.was_closed is False


def test_skip_forward_helper_raises_on_short_stream() -> None:
    stream = io.BytesIO(b"1234")
    skip_forward(stream, 4)
    assert stream.read() == b""
    stream.seek(0)
    with pytest.raises(EOFError):
        skip_forward(stream, 5)
