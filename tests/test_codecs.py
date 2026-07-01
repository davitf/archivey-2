"""Tests for the ``compressed-streams`` capability: the codec layer, crypto wrapper, and
the digest-verification stage."""

from __future__ import annotations

import gzip
import hashlib
import importlib.util
import io
import zlib

import pytest

from archivey.exceptions import (
    ArchiveyError,
    CorruptionError,
    PackageNotInstalledError,
    StreamNotSeekableError,
    TruncatedError,
)
from archivey.internal.config import (
    AcceleratorMode,
    StreamConfig,
)
from archivey.internal.streams import crypto
from archivey.internal.streams.codecs import (
    Codec,
    CodecParams,
    codec_for_stream_format,
    open_codec_stream,
    resolve_codec,
)
from archivey.internal.streams.verify import VerifyingStream
from archivey.types import StreamFormat
from tests.conftest import requires, requires_zstd, zstd_backend
from tests.streams_util import (
    NonSeekableBytesIO,
    compress_lzma2_raw,
    lzma2_raw_filters,
    make_unix_compress,
)

CONTENT = b"the quick brown fox jumps over the lazy dog\n" * 50

# Force the stdlib gzip backend for the translation-contract tests so they assert the same
# exception taxonomy regardless of whether the [seekable] rapidgzip accelerator is
# installed (rapidgzip, when present, would otherwise be auto-selected for random access).
_STDLIB_GZIP = StreamConfig(use_rapidgzip=AcceleratorMode.OFF)


# --- default backends ------------------------------------------------------------------


def test_default_gzip_backend_roundtrip() -> None:
    """A gzip stream opened with default config decompresses via stdlib gzip."""
    compressed = gzip.compress(CONTENT)
    with open_codec_stream(Codec.GZIP, io.BytesIO(compressed)) as stream:
        assert stream.read() == CONTENT


def test_raw_lzma2_backend_for_7z_folder() -> None:
    """A 7z folder's LZMA2 stream decompresses via lzma FORMAT_RAW."""
    compressed = compress_lzma2_raw(CONTENT)
    params = CodecParams(filters=lzma2_raw_filters())
    with open_codec_stream(Codec.LZMA2, io.BytesIO(compressed), params=params) as stream:
        assert stream.read() == CONTENT


@requires("brotli")
def test_brotli_backend_roundtrip() -> None:
    """A Brotli stream decompresses via the brotli-backed stream (no file-like open())."""
    import brotli

    compressed = brotli.compress(CONTENT)
    with open_codec_stream(Codec.BROTLI, io.BytesIO(compressed)) as stream:
        assert stream.read() == CONTENT


@requires("uncompresspy", "ncompress")
def test_unix_compress_backend_roundtrip() -> None:
    """A unix-compress (.Z) stream decompresses via the uncompresspy backend."""
    compressed = make_unix_compress(CONTENT)
    with open_codec_stream(Codec.UNIX_COMPRESS, io.BytesIO(compressed)) as stream:
        assert stream.read() == CONTENT


def test_stored_passthrough() -> None:
    with open_codec_stream(Codec.STORED, io.BytesIO(CONTENT)) as stream:
        assert stream.read() == CONTENT


def test_codec_implemented_once_is_shared_across_formats() -> None:
    """The single-file gzip format and any other gzip consumer resolve to one codec."""
    assert codec_for_stream_format(StreamFormat.GZIP) is Codec.GZIP
    assert codec_for_stream_format(StreamFormat.XZ) is Codec.XZ
    assert codec_for_stream_format(StreamFormat.UNCOMPRESSED) is Codec.STORED


# --- resolve without opening -----------------------------------------------------------


def test_resolve_backend_without_opening() -> None:
    """The open function and its translator are obtainable without opening a stream."""
    # Pin the stdlib backend so the assertion is independent of whether the [seekable]
    # accelerator is installed (which would otherwise select the rapidgzip translator).
    backend = resolve_codec(Codec.GZIP, _STDLIB_GZIP)
    assert backend.codec is Codec.GZIP
    # The translator is returned and maps the library's own corruption exception.
    translated = backend.translate(gzip.BadGzipFile("bad"))
    assert isinstance(translated, CorruptionError)
    # And the open function is callable on demand (nothing was opened yet).
    with backend.open(io.BytesIO(gzip.compress(b"hi"))) as stream:
        assert stream.read() == b"hi"


# --- missing optional backends ---------------------------------------------------------


@pytest.mark.skipif(
    importlib.util.find_spec("pyppmd") is not None,
    reason="pyppmd is installed; the missing-backend path cannot be exercised",
)
def test_ppmd_without_pyppmd_raises() -> None:
    with pytest.raises(PackageNotInstalledError, match="pyppmd"):
        open_codec_stream(Codec.PPMD, io.BytesIO(b""))


@pytest.mark.skipif(
    importlib.util.find_spec("brotli") is not None,
    reason="brotli is installed; the missing-backend path cannot be exercised",
)
def test_brotli_without_brotli_raises() -> None:
    with pytest.raises(PackageNotInstalledError, match="brotli"):
        open_codec_stream(Codec.BROTLI, io.BytesIO(b""))


@pytest.mark.skipif(
    importlib.util.find_spec("uncompresspy") is not None,
    reason="uncompresspy is installed; the missing-backend path cannot be exercised",
)
def test_unix_compress_without_uncompresspy_raises() -> None:
    with pytest.raises(PackageNotInstalledError, match="uncompresspy"):
        open_codec_stream(Codec.UNIX_COMPRESS, io.BytesIO(b""))


def test_aes_without_crypto_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(crypto, "_crypto_available", lambda: False)
    with pytest.raises(PackageNotInstalledError, match="cryptography"):
        crypto.get_crypto_backend()


@pytest.mark.skipif(
    importlib.util.find_spec("cryptography") is None,
    reason="cryptography is not installed (core-only leg); the present-path cannot run",
)
def test_crypto_reachable_only_through_wrapper() -> None:
    """With [crypto] present, the backend is reached via the wrapper (not a direct import)."""
    backend = crypto.get_crypto_backend()
    assert backend.name == crypto.CRYPTO_PACKAGE
    # The concrete AES stage is deferred to Phase 7; the wrapper boundary is what's real now.
    with pytest.raises(NotImplementedError):
        backend.aes_cbc_decrypt_stage(crypto.AesParams(key=b"\x00" * 32, iv=b"\x00" * 16))


# --- exception translation -------------------------------------------------------------


def test_corrupt_gzip_translates_to_corruption_with_cause() -> None:
    corrupt = bytearray(gzip.compress(CONTENT))
    corrupt[1] = 0x00  # break the gzip magic
    with open_codec_stream(Codec.GZIP, io.BytesIO(bytes(corrupt)), config=_STDLIB_GZIP) as stream:
        with pytest.raises(CorruptionError) as excinfo:
            stream.read()
    assert isinstance(excinfo.value.__cause__, gzip.BadGzipFile)


def test_mid_stream_corrupt_gzip_translates_to_corruption_with_cause() -> None:
    # Corruption *inside* the deflate body (a valid header, then a flipped byte) surfaces as
    # zlib.error from stdlib gzip — a different exception type than a broken header's
    # BadGzipFile. It must still be translated to CorruptionError, not leak a raw zlib.error.
    corrupt = bytearray(gzip.compress(CONTENT))
    corrupt[len(corrupt) // 2] ^= 0xFF  # flip a byte well past the 10-byte header
    with open_codec_stream(Codec.GZIP, io.BytesIO(bytes(corrupt)), config=_STDLIB_GZIP) as stream:
        with pytest.raises(CorruptionError) as excinfo:
            stream.read()
    assert isinstance(excinfo.value.__cause__, zlib.error)


def test_truncated_gzip_translates_to_truncated() -> None:
    compressed = gzip.compress(CONTENT)
    truncated = compressed[: len(compressed) // 2]
    with open_codec_stream(Codec.GZIP, io.BytesIO(truncated), config=_STDLIB_GZIP) as stream:
        with pytest.raises(TruncatedError):
            stream.read()


def test_accelerator_path_translates_errors_no_raw_leak() -> None:
    """When rapidgzip is the active backend, its errors still surface as ArchiveyError."""
    if importlib.util.find_spec("rapidgzip") is None:
        pytest.skip("rapidgzip is not installed; the accelerator path cannot run")
    corrupt = bytearray(gzip.compress(CONTENT))
    corrupt[1] = 0x00
    config = StreamConfig(use_rapidgzip=AcceleratorMode.ON)
    with open_codec_stream(Codec.GZIP, io.BytesIO(bytes(corrupt)), config=config) as stream:
        with pytest.raises(ArchiveyError):  # never a raw rapidgzip ValueError
            stream.read()


def test_corrupt_lzma2_translates_to_corruption() -> None:
    corrupt = bytearray(compress_lzma2_raw(CONTENT))
    corrupt[len(corrupt) // 2] ^= 0xFF
    params = CodecParams(filters=lzma2_raw_filters())
    with open_codec_stream(Codec.LZMA2, io.BytesIO(bytes(corrupt)), params=params) as stream:
        with pytest.raises(CorruptionError):
            stream.read()


@requires("brotli")
def test_corrupt_brotli_translates_to_corruption_with_cause() -> None:
    import brotli

    corrupt = bytearray(brotli.compress(CONTENT))
    corrupt[len(corrupt) // 2] ^= 0xFF
    with open_codec_stream(Codec.BROTLI, io.BytesIO(bytes(corrupt))) as stream:
        with pytest.raises(CorruptionError) as excinfo:
            stream.read()
    assert isinstance(excinfo.value.__cause__, brotli.error)


@requires("brotli")
def test_truncated_brotli_translates_to_truncated() -> None:
    import brotli

    compressed = brotli.compress(CONTENT)
    truncated = compressed[: len(compressed) // 2]
    with open_codec_stream(Codec.BROTLI, io.BytesIO(truncated)) as stream:
        with pytest.raises(TruncatedError):
            stream.read()


@requires_zstd()
def test_truncated_zstd_translates_to_truncated() -> None:
    zstd = zstd_backend()
    compressed = zstd.compress(CONTENT)
    truncated = compressed[: len(compressed) // 2]
    with open_codec_stream(Codec.ZSTD, io.BytesIO(truncated)) as stream:
        with pytest.raises(TruncatedError):
            stream.read()


@requires("uncompresspy", "ncompress")
def test_corrupt_unix_compress_translates_to_corruption() -> None:
    corrupt = bytearray(make_unix_compress(CONTENT))
    corrupt[10] ^= 0xFF  # break the LZW bitstream
    with open_codec_stream(Codec.UNIX_COMPRESS, io.BytesIO(bytes(corrupt))) as stream:
        with pytest.raises(CorruptionError):
            stream.read()


@requires("uncompresspy", "ncompress")
def test_unix_compress_non_seekable_source_translates() -> None:
    """uncompresspy needs random access; a non-seekable source is reported, not leaked."""
    compressed = make_unix_compress(CONTENT)
    # The seekable requirement is enforced at open time (eager), so the open call raises.
    with pytest.raises(StreamNotSeekableError):
        open_codec_stream(Codec.UNIX_COMPRESS, NonSeekableBytesIO(compressed))


def test_translated_error_is_stamped() -> None:
    """A stamp callback fills archive/member context on the translated error."""

    def stamp(exc: ArchiveyError) -> None:
        exc.archive_name = "a.gz"
        exc.member_name = "<stream>"

    corrupt = bytearray(gzip.compress(CONTENT))
    corrupt[1] = 0x00
    stream = open_codec_stream(
        Codec.GZIP, io.BytesIO(bytes(corrupt)), config=_STDLIB_GZIP, stamp=stamp
    )
    with pytest.raises(CorruptionError) as excinfo:
        stream.read()
    assert excinfo.value.archive_name == "a.gz"
    assert excinfo.value.member_name == "<stream>"


# --- digest verification ---------------------------------------------------------------


def _crc32(data: bytes) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF


def test_verify_matching_crc32_passes() -> None:
    stream = VerifyingStream(io.BytesIO(CONTENT), {"crc32": _crc32(CONTENT)})
    assert stream.read() == CONTENT
    assert stream.read() == b""  # terminal read verifies; no error


def test_verify_multiple_algorithms() -> None:
    expected = {"crc32": _crc32(CONTENT), "sha256": hashlib.sha256(CONTENT).digest()}
    stream = VerifyingStream(io.BytesIO(CONTENT), expected)
    assert stream.read() == CONTENT


def test_verify_mismatch_raises_at_eof_without_losing_final_chunk() -> None:
    stream = VerifyingStream(io.BytesIO(CONTENT), {"crc32": _crc32(CONTENT) ^ 0xFFFF})
    collected = bytearray()
    with pytest.raises(CorruptionError, match="crc32"):
        while True:
            chunk = stream.read(7)
            if not chunk:
                break
            collected.extend(chunk)
    assert bytes(collected) == CONTENT  # every byte was delivered before the verdict


def test_verify_partial_read_is_not_verified() -> None:
    stream = VerifyingStream(io.BytesIO(CONTENT), {"crc32": _crc32(CONTENT) ^ 0xFFFF})
    assert stream.read(10) == CONTENT[:10]
    stream.close()  # abandoned before EOF — must not raise


def test_verify_unverifiable_algorithm_skipped_with_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level("WARNING", logger="archivey.integrity"):
        stream = VerifyingStream(io.BytesIO(CONTENT), {"blake2sp": b"\x00" * 32})
        assert stream.read() == CONTENT  # no raise: the algorithm is just skipped
    assert any("blake2sp" in rec.message for rec in caplog.records)


def test_verify_crc32_accepts_bytes_form() -> None:
    expected = {"crc32": _crc32(CONTENT).to_bytes(4, "big")}
    stream = VerifyingStream(io.BytesIO(CONTENT), expected)
    assert stream.read() == CONTENT


def test_verify_oversized_int_digest_mismatches_not_raises() -> None:
    """A malformed stored digest wider than the hash must surface as CorruptionError.

    A "crc32" int > 2**32 can't equal a 4-byte digest; normalization must not raise
    OverflowError (which would leak a non-ArchiveyError out of the stream layer).
    """
    stream = VerifyingStream(io.BytesIO(CONTENT), {"crc32": _crc32(CONTENT) + (1 << 40)})
    with pytest.raises(CorruptionError, match="crc32"):
        while stream.read(64):  # read to EOF; the terminal read verifies
            pass


# --- gzip ISIZE truncation backstop -------------------------------------------------------


def test_gzip_truncation_check_read0_mid_stream_is_not_eof(tmp_path) -> None:
    # read(0) is not EOF: mid-stream it must not run the ISIZE trailer comparison (which
    # would spuriously report truncation because the byte total is still partial).
    from archivey.internal.streams.codecs import _GzipTruncationCheckStream

    payload = b"hello world" * 100
    path = tmp_path / "f.gz"
    path.write_bytes(gzip.compress(payload))

    # A plain BytesIO stands in for the accelerator's decompressed output.
    stream = _GzipTruncationCheckStream(io.BytesIO(payload), str(path))
    assert stream.read(5) == b"hello"
    assert stream.read(0) == b""  # must not raise TruncatedError
    assert stream.read(-1) == payload[5:]
    assert stream.read() == b""  # clean EOF: the full total matches ISIZE


def test_gzip_truncation_check_detects_short_output(tmp_path) -> None:
    from archivey.internal.streams.codecs import _GzipTruncationCheckStream

    payload = b"hello world" * 100
    path = tmp_path / "f.gz"
    path.write_bytes(gzip.compress(payload))

    # Simulate an accelerator that silently stopped short of the real payload.
    stream = _GzipTruncationCheckStream(io.BytesIO(payload[:64]), str(path))
    stream.read(-1)
    with pytest.raises(TruncatedError):
        stream.read()


def test_gzip_truncation_check_noop_seek_keeps_verification(tmp_path) -> None:
    # A seek that does not leave the sequential frontier (tell()-style seek(0, SEEK_CUR),
    # or a seek to the current offset) keeps the ISIZE check armed, so a short
    # accelerator output is still caught at EOF.
    from archivey.internal.streams.codecs import _GzipTruncationCheckStream

    payload = b"hello world" * 100
    path = tmp_path / "f.gz"
    path.write_bytes(gzip.compress(payload))

    stream = _GzipTruncationCheckStream(io.BytesIO(payload[:64]), str(path))
    stream.read(16)
    stream.seek(0, io.SEEK_CUR)  # no-op: must not disarm the check
    stream.read(-1)
    with pytest.raises(TruncatedError):
        stream.read()


def test_gzip_truncation_check_real_seek_disables_verification(tmp_path) -> None:
    from archivey.internal.streams.codecs import _GzipTruncationCheckStream

    payload = b"hello world" * 100
    path = tmp_path / "f.gz"
    path.write_bytes(gzip.compress(payload))

    stream = _GzipTruncationCheckStream(io.BytesIO(payload[:64]), str(path))
    stream.read(16)
    stream.seek(0)  # genuine random access: the sequential total is meaningless now
    stream.read(-1)
    assert stream.read() == b""  # no spurious TruncatedError after a real seek
