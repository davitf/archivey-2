"""Unit tests for opt-in benchmark measurement counters."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from archivey import open_archive
from archivey.internal.base_reader import BaseArchiveReader
from archivey.internal.measurement import ByteCounter, SeekCounter, enable_measurement
from archivey.internal.streams.counting import (
    CountingReader,
    OutputCountingStream,
    SeekCountingStream,
)


def test_counting_reader_still_counts_input_bytes() -> None:
    inner = io.BytesIO(b"abcdefgh")
    wrapped = CountingReader(inner)
    assert wrapped.read(3) == b"abc"
    assert wrapped.bytes_read == 3
    buf = bytearray(4)
    assert wrapped.readinto(buf) == 4
    assert wrapped.bytes_read == 7


def test_output_counting_stream_feeds_shared_counter() -> None:
    counter = ByteCounter()
    wrapped = OutputCountingStream(io.BytesIO(b"hello-world"), counter)
    assert wrapped.read(5) == b"hello"
    assert counter.total == 5
    assert wrapped.read() == b"-world"
    assert counter.total == 11


def test_seek_counting_stream_records_seeks_only() -> None:
    counter = SeekCounter()
    inner = io.BytesIO(b"0123456789")
    wrapped = SeekCountingStream(inner, counter)
    assert wrapped.read(2) == b"01"
    assert counter.count == 0
    wrapped.seek(5)
    assert counter.count == 1
    wrapped.seek(0)
    assert counter.count == 2
    assert wrapped.read(2) == b"01"


def test_measurement_off_leaves_counters_at_zero(tmp_path: Path) -> None:
    path = tmp_path / "a.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("a.txt", b"payload-aaa")
        zf.writestr("b.txt", b"payload-bbb")
    with open_archive(path) as reader:
        assert isinstance(reader, BaseArchiveReader)
        for _member, stream in reader.stream_members():
            if stream is not None:
                stream.read()
        assert reader.bytes_decompressed == 0
        assert reader.source_seek_count == 0


def test_measurement_on_counts_zip_decompressed_bytes(tmp_path: Path) -> None:
    path = tmp_path / "a.zip"
    payload_a = b"payload-aaa"
    payload_b = b"payload-bbb"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("a.txt", payload_a)
        zf.writestr("b.txt", payload_b)
    with enable_measurement():
        with open_archive(path) as reader:
            assert isinstance(reader, BaseArchiveReader)
            for _member, stream in reader.stream_members():
                if stream is not None:
                    stream.read()
            assert reader.bytes_decompressed == len(payload_a) + len(payload_b)
            assert reader.source_seek_count > 0


def test_solid_7z_sequential_decodes_once(tmp_path: Path) -> None:
    pytest = __import__("pytest")
    py7zr = pytest.importorskip("py7zr")
    files = {f"f{i}.bin": (b"x" * 4096) + bytes([i]) for i in range(8)}
    source = tmp_path / "src"
    for name, data in files.items():
        (source / name).parent.mkdir(parents=True, exist_ok=True)
        (source / name).write_bytes(data)
    archive = tmp_path / "solid.7z"
    with py7zr.SevenZipFile(archive, "w", filters=[{"id": py7zr.FILTER_LZMA2}]) as zf:
        for name in sorted(files):
            zf.write(source / name, arcname=name)

    unpacked = sum(len(v) for v in files.values())
    with enable_measurement():
        with open_archive(archive) as reader:
            assert isinstance(reader, BaseArchiveReader)
            assert reader.info.is_solid is True
            for _member, stream in reader.stream_members():
                if stream is not None:
                    stream.read()
            # Sequential solid sweep: decode each packed byte at most once.
            assert reader.bytes_decompressed <= unpacked * 2
            sequential = reader.bytes_decompressed

    with enable_measurement():
        with open_archive(archive) as reader:
            assert isinstance(reader, BaseArchiveReader)
            names = [m.name for m in reader.members() if m.is_file]
            # Out-of-order random opens: re-decode is expected and recorded.
            for name in reversed(names):
                reader.read(name)
            assert reader.bytes_decompressed >= sequential
