"""Single-file compressor backend tests — Stage 2.

Covers name inference, the one-member shape, the gzip stored-name surface, per-format
size rules, DIRECT/INDEXED cost, non-seekable streaming (including `.Z`), and the
password-rejection rule. ZST/LZ4 standalone are first-class here; only their
seekable-decompressor refinements remain for Phase 8.
"""

from __future__ import annotations

import bz2
import contextlib
import gzip
import io
import logging
import lzma
import random
import zlib
from pathlib import Path

import pytest

from archivey import (
    AcceleratorMode,
    ArchiveFormat,
    ArchiveyConfig,
    MemberStreams,
    MemberType,
    open_archive,
)
from archivey.cost import AccessCost, ListingCost
from archivey.exceptions import (
    ArchiveyUsageError,
    CorruptionError,
    StreamNotSeekableError,
    TruncatedError,
    UnsupportedOperationError,
)
from tests.conftest import requires, requires_zstd, zstd_backend
from tests.streams_util import NonSeekableBytesIO, make_lzip_member, make_unix_compress


def _gzip_bytes(
    payload: bytes, *, filename: str | None = None, mtime: int = 0
) -> bytes:
    buf = io.BytesIO()
    gz = gzip.GzipFile(filename=filename or "", mode="wb", fileobj=buf, mtime=mtime)
    gz.write(payload)
    gz.close()
    return buf.getvalue()


# ---------------------------------------------------------------------------
# One backend, multiple formats; one-member shape
# ---------------------------------------------------------------------------


def test_one_backend_serves_multiple_formats() -> None:
    cases = {
        ArchiveFormat.GZ: gzip.compress(b"gzipped"),
        ArchiveFormat.BZ2: bz2.compress(b"bzipped"),
        ArchiveFormat.XZ: lzma.compress(b"xzipped"),
    }
    for expected_format, data in cases.items():
        with open_archive(io.BytesIO(data)) as ar:
            assert ar.format == expected_format
            members = ar.members()
            assert len(members) == 1
            assert members[0].type == MemberType.FILE


def test_exactly_one_member_no_directories() -> None:
    with open_archive(io.BytesIO(gzip.compress(b"x"))) as ar:
        members = list(ar)
        assert len(members) == 1
        assert members[0].is_file


def test_read_roundtrip() -> None:
    with open_archive(io.BytesIO(bz2.compress(b"hello bzip"))) as ar:
        assert ar.read(ar.members()[0]) == b"hello bzip"


# ---------------------------------------------------------------------------
# Member name inference
# ---------------------------------------------------------------------------


def test_name_strips_known_compression_extension(tmp_path: Path) -> None:
    path = tmp_path / "data.txt.gz"
    with gzip.open(path, "wb") as f:
        f.write(b"content")
    with open_archive(path) as ar:
        assert ar.members()[0].name == "data.txt"


def test_name_strips_lzma_alone_extension(tmp_path: Path) -> None:
    path = tmp_path / "data.txt.lzma"
    path.write_bytes(lzma.compress(b"content", format=lzma.FORMAT_ALONE))
    with open_archive(path) as ar:
        assert ar.format == ArchiveFormat.LZMA_ALONE
        assert ar.members()[0].name == "data.txt"
        assert ar.read(ar.members()[0]) == b"content"


def test_name_appends_uncompressed_for_unknown_extension(tmp_path: Path) -> None:
    # Identified by content (gzip magic), but ".bin" is not a compression extension, so the
    # extension is preserved and ".uncompressed" appended rather than discarding info.
    path = tmp_path / "mystery.bin"
    path.write_bytes(gzip.compress(b"content"))
    with open_archive(path) as ar:
        assert ar.members()[0].name == "mystery.bin.uncompressed"


def test_name_defaults_to_data_for_anonymous_stream() -> None:
    with open_archive(io.BytesIO(gzip.compress(b"x"))) as ar:
        assert ar.members()[0].name == "data"


def test_inferred_bidi_control_name_warns_once(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    name = "invoice\u202ecod.exe"
    path = tmp_path / f"{name}.gz"
    path.write_bytes(gzip.compress(b"x"))
    # rapidgzip's native path open rejects some Unicode filenames on Windows
    # (U+202E); this test covers inferred-name presentation warnings, not the
    # accelerator, so pin the stdlib gzip path.
    config = ArchiveyConfig(use_rapidgzip=AcceleratorMode.OFF)
    with caplog.at_level(logging.WARNING, logger="archivey.normalization"):
        with open_archive(path, config=config) as archive:
            assert archive.members()[0].name == name
    warnings = [
        record for record in caplog.records if "bidirectional control" in record.message
    ]
    assert len(warnings) == 1


# ---------------------------------------------------------------------------
# gzip stored filename + mtime
# ---------------------------------------------------------------------------


def test_gzip_stored_filename_surfaced(tmp_path: Path) -> None:
    path = tmp_path / "archive.gz"
    path.write_bytes(_gzip_bytes(b"payload", filename="report.csv"))
    with open_archive(path) as ar:
        member = ar.members()[0]
        # name comes from the source filename; the stored FNAME lives in extra + raw_name.
        assert member.name == "archive"
        assert member.extra["gzip.original_filename"] == "report.csv"
        assert member.raw_name == b"report.csv"


def test_gzip_stored_filename_non_ascii_is_latin1(tmp_path: Path) -> None:
    # RFC 1952 specifies FNAME as ISO-8859-1 (Latin-1). The gzip tooling stores
    # "café.txt" as the bytes b"caf\xe9.txt"; decoding those as UTF-8 would mangle them
    # (0xE9 is not valid UTF-8), so the decoded value must use Latin-1.
    path = tmp_path / "archive.gz"
    gz = gzip.GzipFile(
        filename="café.txt", mode="wb", fileobj=open(path, "wb"), mtime=0
    )
    gz.write(b"payload")
    gz.close()
    with open_archive(path) as ar:
        member = ar.members()[0]
        assert member.raw_name == b"caf\xe9.txt"  # verbatim stored bytes
        assert member.extra["gzip.original_filename"] == "café.txt"


def test_gzip_without_stored_filename() -> None:
    # gzip.compress writes no FNAME -> no extra key, raw_name stays None.
    with open_archive(io.BytesIO(gzip.compress(b"x"))) as ar:
        member = ar.members()[0]
        assert "gzip.original_filename" not in member.extra
        assert member.raw_name is None


def test_gzip_mtime_surfaced() -> None:
    data = _gzip_bytes(b"payload", mtime=1_600_000_000)
    with open_archive(io.BytesIO(data)) as ar:
        member = ar.members()[0]
        assert member.modified is not None
        assert member.modified.tzinfo is not None
        assert int(member.modified.timestamp()) == 1_600_000_000


# ---------------------------------------------------------------------------
# Per-format size rules
# ---------------------------------------------------------------------------


def test_gz_size_is_always_none() -> None:
    with open_archive(io.BytesIO(gzip.compress(b"x" * 1000))) as ar:
        assert ar.members()[0].size is None


def test_bz2_size_none_before_full_read() -> None:
    with open_archive(io.BytesIO(bz2.compress(b"x" * 1000))) as ar:
        assert ar.members()[0].size is None


def test_zlib_size_none() -> None:
    with open_archive(io.BytesIO(zlib.compress(b"x" * 1000))) as ar:
        assert ar.members()[0].size is None


def test_xz_size_from_header(tmp_path: Path) -> None:
    path = tmp_path / "data.xz"
    with lzma.open(path, "wb") as f:
        f.write(b"x" * 1234)
    # Cheap size from the XZ index requires declared seek demand.
    with open_archive(path, member_streams=MemberStreams.SEEKABLE) as ar:
        assert ar.members()[0].size == 1234
    with open_archive(path) as ar:
        assert ar.members()[0].size is None


def test_lzip_size_from_trailer(tmp_path: Path) -> None:
    path = tmp_path / "data.lz"
    path.write_bytes(make_lzip_member(b"y" * 777))
    with open_archive(path, member_streams=MemberStreams.SEEKABLE) as ar:
        assert ar.members()[0].size == 777
    with open_archive(path) as ar:
        assert ar.members()[0].size is None


# ---------------------------------------------------------------------------
# Stored decompressed CRC (cheap dedupe; no decompression)
# ---------------------------------------------------------------------------


def test_single_member_gzip_exposes_stored_crc32(tmp_path: Path) -> None:
    payload = b"stored-crc-payload"
    path = tmp_path / "one.gz"
    path.write_bytes(gzip.compress(payload))
    with open_archive(path) as ar:
        member = ar.members()[0]
        assert member.hashes["crc32"] == zlib.crc32(payload) & 0xFFFFFFFF


def test_multi_member_gzip_omits_crc32(tmp_path: Path) -> None:
    path = tmp_path / "multi.gz"
    path.write_bytes(gzip.compress(b"first") + gzip.compress(b"second"))
    with open_archive(path) as ar:
        assert "crc32" not in ar.members()[0].hashes


def test_gzip_metadata_omits_crc_without_gzip_magic() -> None:
    """Non-gzip bytes must not get a fake trailer CRC (PR 104 review #1)."""
    from archivey.internal.streams.codecs import GzipCodec, MetadataContext
    from archivey.types import ArchiveMember, MemberType

    def boom() -> int | None:
        raise AssertionError("must not probe CRC without gzip magic")

    member = ArchiveMember(type=MemberType.FILE, name="data")
    ctx = MetadataContext(
        peek_header=lambda _n: b"NOT_A_GZIP_HEADER!!!!!!!!",
        peek_trailer=lambda _n: b"\x11\x22\x33\x44\x55\x66\x77\x88",
        probe_decompressed_size=lambda: None,
        probe_gzip_stored_crc32=boom,
    )
    GzipCodec().extract_metadata(ctx, member)
    assert "crc32" not in member.hashes


def test_gzip_omits_crc32_on_nonseekable_source() -> None:
    data = gzip.compress(b"pipe-payload")
    with open_archive(NonSeekableBytesIO(data), streaming=True) as ar:
        members = ar.get_members_if_available()
        assert members is not None
        assert "crc32" not in members[0].hashes


def test_lzip_exposes_stored_crc32(tmp_path: Path) -> None:
    payload = b"lzip-crc-payload" * 3
    path = tmp_path / "data.lz"
    path.write_bytes(make_lzip_member(payload))
    with open_archive(path, member_streams=MemberStreams.SEEKABLE) as ar:
        member = ar.members()[0]
        assert member.hashes["crc32"] == zlib.crc32(payload) & 0xFFFFFFFF
    # Same gate as size: without declared SEEKABLE, omit the trailer CRC.
    with open_archive(path) as ar:
        assert "crc32" not in ar.members()[0].hashes


def test_other_single_file_codecs_omit_stored_crc32(tmp_path: Path) -> None:
    payload = b"no-whole-member-crc"
    cases = {
        "data.bz2": bz2.compress(payload),
        "data.xz": lzma.compress(payload),
        "data.zz": zlib.compress(payload),
    }
    for name, blob in cases.items():
        path = tmp_path / name
        path.write_bytes(blob)
        with open_archive(path) as ar:
            assert "crc32" not in ar.members()[0].hashes, name


def test_stored_gzip_crc32_does_not_change_read_or_verification(tmp_path: Path) -> None:
    payload = b"verify-unchanged"
    path = tmp_path / "ok.gz"
    path.write_bytes(gzip.compress(payload))
    with open_archive(path) as ar:
        member = ar.members()[0]
        assert "crc32" in member.hashes
        assert ar.read(member) == payload
        # Second open still succeeds (path source; codec verifies its own trailer).
        assert ar.read(member) == payload


def test_lzma_alone_size_from_header_when_known(tmp_path: Path) -> None:
    payload = b"z" * 321
    # stdlib FORMAT_ALONE always writes the unknown-size marker. Patching the 8-byte
    # size field is enough to exercise extract_metadata; do not round-trip the patched
    # bytes — some liblzma builds (notably Windows/macOS 3.14 CI) treat that header
    # rewrite as corrupt because the encoder produced an unknown-size end marker.
    compressed = lzma.compress(payload, format=lzma.FORMAT_ALONE)
    compressed = compressed[:5] + len(payload).to_bytes(8, "little") + compressed[13:]
    path = tmp_path / "data.lzma"
    path.write_bytes(compressed)
    with open_archive(path) as ar:
        assert ar.members()[0].size == 321


def test_lzma_alone_size_none_when_unknown_marker(tmp_path: Path) -> None:
    payload = b"z" * 321
    path = tmp_path / "data.lzma"
    path.write_bytes(lzma.compress(payload, format=lzma.FORMAT_ALONE))
    with open_archive(path) as ar:
        assert ar.members()[0].size is None
        assert ar.read(ar.members()[0]) == payload


def test_tar_lzma_alone_roundtrip(tmp_path: Path) -> None:
    import tarfile

    from archivey.types import ContainerFormat, StreamFormat

    tar_buf = io.BytesIO()
    with tarfile.open(fileobj=tar_buf, mode="w") as tar:
        info = tarfile.TarInfo("nested.txt")
        payload = b"nested alone tar"
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))
    path = tmp_path / "archive.tar.lzma"
    path.write_bytes(lzma.compress(tar_buf.getvalue(), format=lzma.FORMAT_ALONE))
    with open_archive(path) as ar:
        assert ar.format == ArchiveFormat(ContainerFormat.TAR, StreamFormat.LZMA_ALONE)
        member = next(m for m in ar if m.is_file)
        assert member.name == "nested.txt"
        assert ar.read(member) == b"nested alone tar"


# ---------------------------------------------------------------------------
# Cost
# ---------------------------------------------------------------------------


def test_cost_is_indexed_and_direct() -> None:
    with open_archive(io.BytesIO(gzip.compress(b"x"))) as ar:
        cost = ar.cost
        assert cost.listing_cost == ListingCost.INDEXED
        assert cost.access_cost == AccessCost.DIRECT


def test_archive_info() -> None:
    with open_archive(io.BytesIO(gzip.compress(b"x"))) as ar:
        info = ar.info
        assert info.member_count == 1
        assert info.is_solid is False
        assert info.is_encrypted is False


# ---------------------------------------------------------------------------
# Non-seekable behavior
# ---------------------------------------------------------------------------


def test_non_seekable_gzip_requires_streaming_mode() -> None:
    # Random access (streaming=False) promises repeatable open()/read(), which a
    # non-seekable source cannot honor (one decompression pass) — fail fast at open,
    # like every other format (it used to open and then silently return an empty
    # stream on a re-read).
    data = gzip.compress(b"streamed payload")
    with pytest.raises(StreamNotSeekableError):
        open_archive(NonSeekableBytesIO(data))


def test_non_seekable_gzip_streams_fine() -> None:
    # Single-file formats stream from a non-seekable source under streaming=True
    # (the mode a non-seekable source requires).
    data = gzip.compress(b"streamed payload")
    with open_archive(NonSeekableBytesIO(data), streaming=True) as ar:
        # Read while the generator is live: exhaustion closes the current stream.
        pairs = list(ar.stream_members())
        assert len(pairs) == 1
        member, stream = pairs[0]
        assert stream is not None
        # Re-open via random open is unavailable in streaming mode; drain via a fresh
        # streaming pass that reads before the generator finishes.
    with open_archive(NonSeekableBytesIO(data), streaming=True) as ar:
        for _member, stream in ar.stream_members():
            assert stream is not None
            assert stream.read() == b"streamed payload"


@requires("ncompress")
def test_unix_compress_non_seekable_requires_streaming_mode() -> None:
    """Random access still needs a seekable source — same rule as gzip."""
    data = make_unix_compress(b"lzw payload")
    with pytest.raises(StreamNotSeekableError):
        open_archive(NonSeekableBytesIO(data), format=ArchiveFormat.Z)


@requires("ncompress")
def test_unix_compress_non_seekable_streams_fine() -> None:
    data = make_unix_compress(b"lzw payload")
    with open_archive(
        NonSeekableBytesIO(data), format=ArchiveFormat.Z, streaming=True
    ) as ar:
        for _member, stream in ar.stream_members():
            assert stream is not None
            assert stream.read() == b"lzw payload"


@requires("ncompress")
def test_unix_compress_seekable_reads(tmp_path: Path) -> None:
    path = tmp_path / "data.Z"
    path.write_bytes(make_unix_compress(b"lzw payload"))
    with open_archive(path) as ar:
        assert ar.format == ArchiveFormat.Z
        assert ar.read(ar.members()[0]) == b"lzw payload"


# ---------------------------------------------------------------------------
# Password rejection
# ---------------------------------------------------------------------------


def test_password_raises() -> None:
    with pytest.raises(UnsupportedOperationError):
        open_archive(io.BytesIO(gzip.compress(b"x")), password=b"secret")


# ---------------------------------------------------------------------------
# Brotli (magic-less, detected by content probe)
# ---------------------------------------------------------------------------


@requires("brotli")
def test_brotli_roundtrip() -> None:
    import brotli

    data = brotli.compress(b"brotli payload")
    with open_archive(io.BytesIO(data)) as ar:
        assert ar.format == ArchiveFormat.BROTLI
        assert ar.read(ar.members()[0]) == b"brotli payload"


# ---------------------------------------------------------------------------
# zstd / lz4 standalone (now first-class single-file formats)
# ---------------------------------------------------------------------------


@requires_zstd()
def test_zstd_roundtrip(tmp_path: Path) -> None:
    zstd = zstd_backend()
    data = zstd.compress(b"zstd payload")
    with open_archive(io.BytesIO(data)) as ar:
        assert ar.format == ArchiveFormat.ZST
        assert ar.members()[0].name == "data"
        assert ar.read(ar.members()[0]) == b"zstd payload"

    path = tmp_path / "file.bin.zst"
    path.write_bytes(data)
    with open_archive(path) as ar:
        assert ar.members()[0].name == "file.bin"


@requires("lz4")
def test_lz4_roundtrip() -> None:
    import lz4.frame

    data = lz4.frame.compress(b"lz4 payload")
    with open_archive(io.BytesIO(data)) as ar:
        assert ar.format == ArchiveFormat.LZ4
        assert ar.read(ar.members()[0]) == b"lz4 payload"


# ---------------------------------------------------------------------------
# The backend uses the resolved format it is given (no re-inspection)
# ---------------------------------------------------------------------------


def test_explicit_format_bypasses_detection() -> None:
    # Forcing format=GZ routes straight to the gzip codec without re-detecting the source.
    data = gzip.compress(b"forced gzip")
    with open_archive(io.BytesIO(data), format=ArchiveFormat.GZ) as ar:
        assert ar.format == ArchiveFormat.GZ
        assert ar.read(ar.members()[0]) == b"forced gzip"


# ---------------------------------------------------------------------------
# Corrupt / truncated input -> CorruptionError / TruncatedError end-to-end.
# (Per-format slice of testing-contract's adversarial-corpus requirement, pulled
# forward so the backend wires the codec layer's translation through correctly.
# gzip is used because it is always available: zero-dep stdlib.)
# ---------------------------------------------------------------------------


# A non-seekable source keeps the codec sequential (no rapidgzip/indexed_bzip2
# accelerator), so these assert the backend's own stdlib translation deterministically,
# independent of which optional accelerators the environment has installed.


def _read_single_streamed_member(ar) -> bytes:
    for _member, stream in ar.stream_members():
        assert stream is not None
        return stream.read()
    raise AssertionError("expected one member stream")


def test_truncated_gzip_raises_truncated() -> None:
    full = gzip.compress(b"streamed payload" * 1000)
    truncated = full[: len(full) // 2]  # gzip magic intact -> detection picks GZ
    with open_archive(NonSeekableBytesIO(truncated), streaming=True) as ar:
        with pytest.raises(TruncatedError):
            _read_single_streamed_member(ar)


def test_corrupt_gzip_raises_corruption() -> None:
    data = bytearray(gzip.compress(b"streamed payload" * 100))
    data[15:35] = b"\x00" * 20  # clobber the deflate body (past the 10-byte header)
    with open_archive(NonSeekableBytesIO(bytes(data)), streaming=True) as ar:
        with pytest.raises(CorruptionError):
            _read_single_streamed_member(ar)


def test_open_from_mid_positioned_stream() -> None:
    # The compressed stream starts at the caller's position (an embedded .gz after junk
    # bytes); reads — including a re-open for a second read — must use that origin.
    junk = b"X" * 37
    payload = b"embedded payload " * 20
    stream = io.BytesIO(junk + gzip.compress(payload))
    stream.seek(len(junk))
    with open_archive(stream, format=ArchiveFormat.GZ) as ar:
        (member,) = ar.members()
        assert ar.read(member) == payload
        assert ar.read(member) == payload  # re-open rewinds to the embedded origin


def test_concurrent_open_same_member_interleaved() -> None:
    # Single-file routes through SharedSource: two opens of the one member stay correct
    # when read in interleaved partial chunks (and open is reentrant — no _first_stream).
    payload = b"abcdefghijklmnopqrstuvwxyz" * 40
    with open_archive(
        io.BytesIO(gzip.compress(payload)),
        member_streams=MemberStreams.CONCURRENT | MemberStreams.SEEKABLE,
    ) as ar:
        (member,) = ar.members()
        s1 = ar.open(member)
        s2 = ar.open(member)
        assert s1.read(10) == payload[:10]
        assert s2.read(7) == payload[:7]
        assert s1.read(5) == payload[10:15]
        assert s2.read() == payload[7:]
        assert s1.read() == payload[15:]
        s1.close()
        s2.close()


def test_reentrant_open_after_first_read(tmp_path: Path) -> None:
    # Path source: open, read partially, open again, both complete independently.
    path = tmp_path / "data.txt.gz"
    payload = b"reentrant payload " * 50
    path.write_bytes(gzip.compress(payload))
    with open_archive(
        path, member_streams=MemberStreams.CONCURRENT | MemberStreams.SEEKABLE
    ) as ar:
        (member,) = ar.members()
        first = ar.open(member)
        assert first.read(8) == payload[:8]
        second = ar.open(member)
        assert second.read() == payload
        assert first.read() == payload[8:]
        first.close()
        second.close()


def test_read_after_reader_and_source_close_raises_typed_error() -> None:
    # archive-reading "fail loudly" scenario: with the reader AND the caller's source
    # stream closed, reading a still-open member stream surfaces a typed error at the
    # reader boundary — never a raw ValueError. (Reader close alone does NOT invalidate
    # member streams: the SharedSource is non-owning and deliberately left open, matching
    # ZIP/path-source behavior — and killing the source under a live rapidgzip stream
    # would abort the process; see docs/internal/known-issues.md.) Pinned to the stdlib gzip path
    # with an incompressible payload larger than its read-ahead, so the post-close read
    # deterministically touches the closed source (the accelerator may buffer a small
    # member whole on its first read — and terminates on a dead source, per above).
    payload = random.Random(0).randbytes(256 * 1024)  # incompressible: stays ~256 KiB
    config = ArchiveyConfig(use_rapidgzip=AcceleratorMode.OFF)
    source = io.BytesIO(gzip.compress(payload))
    ar = open_archive(source, config=config)
    (member,) = ar.members()
    stream = ar.open(member)
    assert stream.read(16) == payload[:16]
    ar.close()
    assert stream.read(16) == payload[16:32]  # reader close alone: still readable
    source.close()
    with pytest.raises(ArchiveyUsageError):
        while stream.read(65536):
            pass
    with contextlib.suppress(Exception):
        stream.close()
