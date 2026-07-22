"""Tests for the ``compressed-streams`` capability: the codec layer, crypto wrapper, and
the digest-verification stage."""

from __future__ import annotations

import gzip
import hashlib
import importlib.util
import io
import zlib
from pathlib import Path

import pytest

from archivey.diagnostics import DiagnosticCode
from archivey.exceptions import (
    ArchiveyError,
    CorruptionError,
    PackageNotInstalledError,
    TruncatedError,
    UnsupportedFeatureError,
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
from archivey.types import HashAlgorithm, StreamFormat, crc32_digest
from tests.conftest import requires, requires_binary, requires_zstd, zstd_backend
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
_STDLIB_GZIP = StreamConfig(use_rapidgzip=AcceleratorMode.OFF, seekable=True)


# --- default backends ------------------------------------------------------------------


def test_default_gzip_backend_roundtrip() -> None:
    """A gzip stream opened with default config decompresses via stdlib gzip."""
    compressed = gzip.compress(CONTENT)
    with open_codec_stream(Codec.GZIP, io.BytesIO(compressed)) as stream:
        assert stream.read() == CONTENT


def test_default_lzma_alone_backend_roundtrip() -> None:
    import lzma

    compressed = lzma.compress(CONTENT, format=lzma.FORMAT_ALONE)
    with open_codec_stream(Codec.LZMA_ALONE, io.BytesIO(compressed)) as stream:
        assert stream.read() == CONTENT
    assert codec_for_stream_format(StreamFormat.LZMA_ALONE) is Codec.LZMA_ALONE


def test_raw_lzma2_backend_for_7z_folder() -> None:
    """A 7z folder's LZMA2 stream decompresses via lzma FORMAT_RAW."""
    compressed = compress_lzma2_raw(CONTENT)
    params = CodecParams(filters=lzma2_raw_filters())
    with open_codec_stream(
        Codec.LZMA2, io.BytesIO(compressed), params=params
    ) as stream:
        assert stream.read() == CONTENT


@requires("brotli")
def test_brotli_backend_roundtrip() -> None:
    """A Brotli stream decompresses via the brotli-backed stream (no file-like open())."""
    import brotli

    compressed = brotli.compress(CONTENT)
    with open_codec_stream(Codec.BROTLI, io.BytesIO(compressed)) as stream:
        assert stream.read() == CONTENT


@requires("ncompress")
def test_unix_compress_backend_roundtrip() -> None:
    """A unix-compress (.Z) stream decompresses via the native LZW backend."""
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
    stage = backend.aes_cbc_decrypt_stage(
        crypto.AesParams(key=b"\x00" * 32, iv=b"\x00" * 16)
    )
    # Round-trip one AES block through the shared stage (format parsers never import
    # cryptography directly — they go through this wrapper).
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    encryptor = Cipher(
        algorithms.AES(b"\x00" * 32), modes.CBC(b"\x00" * 16)
    ).encryptor()
    plaintext = b"0123456789abcdef"
    ciphertext = encryptor.update(plaintext) + encryptor.finalize()
    assert stage.update(ciphertext) + stage.finalize() == plaintext


@pytest.mark.skipif(
    importlib.util.find_spec("cryptography") is None,
    reason="cryptography is not installed (core-only leg); the present-path cannot run",
)
def test_sevenzip_kdf_cache_reuses_derived_keys() -> None:
    password = "secret".encode("utf-16le")
    salt = b"salt"
    cache = crypto.SevenZipKeyCache()
    first = cache.derive(password, salt=salt, cycles=1)
    second = cache.derive(password, salt=salt, cycles=1)
    assert first == second
    assert first is second  # same cached object
    # 0x3f special case: salt+password copied into 32-byte key (no hashing).
    special = crypto.derive_sevenzip_aes_key(password, salt=salt, cycles=0x3F)
    assert len(special) == 32
    assert special == bytes(bytearray(salt + password + bytes(32))[:32])


def test_sevenzip_kdf_rejects_cycles_above_24() -> None:
    """Match 7-Zip's ``k_NumCyclesPower_Supported_MAX = 24`` (PR #115 F3).

    The cap raises before any ``cryptography`` import, so this runs in core-only too.
    """
    from archivey.exceptions import UnsupportedFeatureError

    with pytest.raises(UnsupportedFeatureError, match="NumCyclesPower"):
        crypto.derive_sevenzip_aes_key(b"pw", salt=b"s", cycles=25)


# --- exception translation -------------------------------------------------------------


def test_corrupt_gzip_translates_to_corruption_with_cause() -> None:
    corrupt = bytearray(gzip.compress(CONTENT))
    corrupt[1] = 0x00  # break the gzip magic
    with open_codec_stream(
        Codec.GZIP, io.BytesIO(bytes(corrupt)), config=_STDLIB_GZIP
    ) as stream:
        with pytest.raises(CorruptionError) as excinfo:
            stream.read()
    # Stdlib path uses zlib's gzip window (not GzipFile); bad magic → zlib.error.
    assert isinstance(excinfo.value.__cause__, zlib.error)


def test_mid_stream_corrupt_gzip_translates_to_corruption_with_cause() -> None:
    # Corruption *inside* the deflate body (a valid header, then a flipped byte) surfaces as
    # zlib.error from the gzip-window decoder. It must still be translated to
    # CorruptionError, not leak a raw zlib.error.
    corrupt = bytearray(gzip.compress(CONTENT))
    corrupt[len(corrupt) // 2] ^= 0xFF  # flip a byte well past the 10-byte header
    with open_codec_stream(
        Codec.GZIP, io.BytesIO(bytes(corrupt)), config=_STDLIB_GZIP
    ) as stream:
        with pytest.raises(CorruptionError) as excinfo:
            stream.read()
    assert isinstance(excinfo.value.__cause__, zlib.error)


def test_truncated_gzip_translates_to_truncated() -> None:
    compressed = gzip.compress(CONTENT)
    truncated = compressed[: len(compressed) // 2]
    with open_codec_stream(
        Codec.GZIP, io.BytesIO(truncated), config=_STDLIB_GZIP
    ) as stream:
        with pytest.raises(TruncatedError):
            stream.read()


def test_accelerator_path_translates_errors_no_raw_leak() -> None:
    """When rapidgzip is the active backend, its errors still surface as ArchiveyError."""
    if importlib.util.find_spec("rapidgzip") is None:
        pytest.skip("rapidgzip is not installed; the accelerator path cannot run")
    corrupt = bytearray(gzip.compress(CONTENT))
    corrupt[1] = 0x00
    config = StreamConfig(use_rapidgzip=AcceleratorMode.ON, seekable=True)
    with open_codec_stream(
        Codec.GZIP, io.BytesIO(bytes(corrupt)), config=config
    ) as stream:
        with pytest.raises(ArchiveyError):  # never a raw rapidgzip ValueError
            stream.read()


def test_corrupt_lzma2_translates_to_corruption() -> None:
    corrupt = bytearray(compress_lzma2_raw(CONTENT))
    corrupt[len(corrupt) // 2] ^= 0xFF
    params = CodecParams(filters=lzma2_raw_filters())
    with open_codec_stream(
        Codec.LZMA2, io.BytesIO(bytes(corrupt)), params=params
    ) as stream:
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


@requires("ncompress")
def test_corrupt_unix_compress_translates_to_corruption() -> None:
    corrupt = bytearray(make_unix_compress(CONTENT))
    corrupt[10] ^= 0xFF  # break the LZW bitstream
    with open_codec_stream(Codec.UNIX_COMPRESS, io.BytesIO(bytes(corrupt))) as stream:
        with pytest.raises(CorruptionError):
            stream.read()


@requires("ncompress")
def test_unix_compress_non_seekable_source_streams() -> None:
    """Native LZW forward-decodes a non-seekable source; the stream is not seekable."""
    compressed = make_unix_compress(CONTENT)
    with open_codec_stream(
        Codec.UNIX_COMPRESS, NonSeekableBytesIO(compressed)
    ) as stream:
        assert not stream.seekable()
        assert stream.read() == CONTENT


@requires("ncompress")
def test_unix_compress_truncated_raises_on_next_read() -> None:
    """Chunked reads: deferred TruncatedError surfaces on the empty follow-up read."""
    compressed = make_unix_compress(CONTENT)
    truncated = compressed[: len(compressed) // 2]
    with open_codec_stream(Codec.UNIX_COMPRESS, io.BytesIO(truncated)) as stream:
        # Bounded chunked idiom: return available bytes, raise on the next empty read.
        chunk = stream.read(256)
        assert chunk  # got a prefix
        buf = bytearray(chunk)
        with pytest.raises(TruncatedError, match="leftover bits"):
            while True:
                c = stream.read(256)
                if not c:
                    break
                buf.extend(c)


@requires("ncompress")
def test_unix_compress_truncated_readall_raises() -> None:
    """Single-shot read()/readall() must raise TruncatedError (not swallow it)."""
    compressed = make_unix_compress(CONTENT)
    truncated = compressed[: len(compressed) // 2]
    with open_codec_stream(Codec.UNIX_COMPRESS, io.BytesIO(truncated)) as stream:
        with pytest.raises(TruncatedError, match="leftover bits"):
            stream.read()


@requires("ncompress")
def test_unix_compress_maxbits_above_16_rejected() -> None:
    """Format ceiling is 16; 17–31 must raise CorruptionError (not grow the dict)."""
    for maxbits in (17, 24, 31):
        header = bytes([0x1F, 0x9D, 0x80 | maxbits])
        with open_codec_stream(Codec.UNIX_COMPRESS, io.BytesIO(header)) as stream:
            with pytest.raises(CorruptionError, match="ceiling of 16"):
                stream.read()


@requires("ncompress")
def test_unix_compress_maxbits_16_accepted() -> None:
    compressed = make_unix_compress(CONTENT)
    # ncompress emits a legal maxbits ≤ 16; a full decode must succeed.
    assert (compressed[2] & 0x1F) <= 16
    with open_codec_stream(Codec.UNIX_COMPRESS, io.BytesIO(compressed)) as stream:
        assert stream.read() == CONTENT


def test_decompressor_read_one_bounds_internal_buffer() -> None:
    """Bounded read(1) must not buffer megabytes of highly compressible output (F3a)."""
    import lzma

    from archivey.internal.streams.decompress import ZlibDecompressorStream
    from archivey.internal.streams.xz import XzDecompressorStream

    payload = b"A" * 2_000_000
    # deflate
    co = zlib.compressobj(wbits=-15)
    deflate = co.compress(payload) + co.flush()
    with ZlibDecompressorStream(io.BytesIO(deflate)) as stream:
        assert stream.read(1) == b"A"
        assert len(stream._buffer) < 64_000
        assert stream.read(1000) == b"A" * 1000

    # xz
    xz = lzma.compress(payload, format=lzma.FORMAT_XZ)
    with XzDecompressorStream(io.BytesIO(xz)) as stream:
        assert stream.read(1) == b"A"
        assert len(stream._buffer) < 64_000


@requires("ncompress")
def test_unix_compress_read_one_bounds_internal_buffer() -> None:
    payload = b"A" * 2_000_000
    compressed = make_unix_compress(payload)
    with open_codec_stream(Codec.UNIX_COMPRESS, io.BytesIO(compressed)) as stream:
        assert stream.read(1) == b"A"
        # DecompressorStream buffer after a budgeted feed.
        assert len(getattr(stream, "_inner")._buffer) < 64_000


@requires("brotli")
def test_brotli_read_one_bounds_internal_buffer() -> None:
    """Brotli process(output_buffer_limit) must bound read(1) peak buffer (CVE-2025-6176)."""
    import brotli

    from archivey.internal.streams.decompress import BrotliDecompressorStream

    payload = b"A" * 2_000_000
    compressed = brotli.compress(payload)
    with BrotliDecompressorStream(io.BytesIO(compressed)) as stream:
        assert stream.read(1) == b"A"
        # Block-granular floor is ~32 KiB; still far below a multi-MB bomb.
        assert len(stream._buffer) < 128_000
        assert stream.read(1000) == b"A" * 1000


@requires("inflate64")
@requires_binary("7z")
def test_deflate64_read_one_bounds_internal_buffer(tmp_path: Path) -> None:
    """Small compressed feeds under max_length must bound Deflate64 read(1) buffers."""
    import struct
    import subprocess

    from archivey.internal.streams.decompress import Deflate64DecompressorStream

    payload = b"A" * 500_000
    src = tmp_path / "a.bin"
    src.write_bytes(payload)
    archive = tmp_path / "d.zip"
    subprocess.check_call(
        ["7z", "a", "-tzip", "-mm=Deflate64", str(archive), str(src)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    data = archive.read_bytes()
    hdr = struct.unpack_from("<HHHHHIIIHH", data, 4)
    method, csize, nlen, elen = hdr[2], hdr[6], hdr[8], hdr[9]
    assert method == 9
    comp = data[30 + nlen + elen : 30 + nlen + elen + csize]
    with Deflate64DecompressorStream(io.BytesIO(comp)) as stream:
        assert stream.read(1) == b"A"
        assert len(stream._buffer) < 64_000
        assert stream.read(1000) == b"A" * 1000


@requires("ncompress")
def test_unix_compress_reserved_header_flags_unsupported() -> None:
    compressed = bytearray(make_unix_compress(CONTENT))
    compressed[2] |= 0x60  # classic compress reserved flag bits
    with open_codec_stream(
        Codec.UNIX_COMPRESS, io.BytesIO(bytes(compressed))
    ) as stream:
        with pytest.raises(UnsupportedFeatureError, match="reserved flags"):
            stream.read()


@requires("ncompress")
def test_unix_compress_valid_stream_has_zero_leftover_padding() -> None:
    """Finished compressors zero-pad; a full read must not arm TruncatedError."""
    compressed = make_unix_compress(CONTENT)
    with open_codec_stream(Codec.UNIX_COMPRESS, io.BytesIO(compressed)) as stream:
        assert stream.read() == CONTENT
        assert stream.read() == b""
        assert stream.read() == b""


@requires("ncompress")
def test_unix_compress_clear_seek_points() -> None:
    """CLEAR boundaries become SeekPoints; random access resumes without rewind diagnostics."""
    # Distinct words force dictionary growth until classic compress emits CLEAR.
    payload = b"".join(i.to_bytes(4, "big") for i in range(50_000))
    compressed = make_unix_compress(payload)
    config = StreamConfig(seekable=True)
    with open_codec_stream(
        Codec.UNIX_COMPRESS, io.BytesIO(compressed), config=config
    ) as stream:
        assert stream.seekable()
        # Drive a full pass so CLEAR points accumulate, then seek via ArchiveStream.
        assert stream.read() == payload
        stream.seek(0)
        assert stream.read(16) == payload[:16]
        mid = len(payload) // 2
        stream.seek(mid)
        assert stream.read(8) == payload[mid : mid + 8]
        stream.seek(100)
        assert stream.read(4) == payload[100:104]
        # Indexed CLEAR seeks must not emit the O(n) rewind diagnostic.
        assert (
            stream.diagnostics.counts.get(
                DiagnosticCode.STREAM_REWIND_REDECOMPRESSES, 0
            )
            == 0
        )


def test_unix_compress_consecutive_clear_seek_points_no_assert() -> None:
    """Atheris: consecutive CLEARs at the same decompressed offset must not assert.

    Empty CLEAR segments re-emit a SeekPoint at the prior decompressed offset with a
    later compressed resume point; indexing must forward-refine rather than crash.
    """
    # CI crash input (detect_format target, 2026-07-15): triggers the collision during
    # a seekable decode of a short hostile .Z prefix.
    compressed = bytes.fromhex(
        "1f9d8b008b000000000000000000000000000000000000000000000000000000"
        "0000000000000000000000000000000000000000000000000000000000000000"
        "00000000000000000000000000000000002e0100000010000003550100000000"
        "0000035500e00008"
    )
    config = StreamConfig(seekable=True)
    with open_codec_stream(
        Codec.UNIX_COMPRESS, io.BytesIO(compressed), config=config
    ) as stream:
        # May raise typed ArchiveyError on corrupt payload; must not AssertionError.
        try:
            stream.read(4096)
        except ArchiveyError:
            pass


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


def _crc32(data: bytes) -> bytes:
    return crc32_digest(zlib.crc32(data))


def test_verify_matching_crc32_passes() -> None:
    stream = VerifyingStream(
        io.BytesIO(CONTENT), {HashAlgorithm.CRC32: _crc32(CONTENT)}
    )
    assert stream.read() == CONTENT
    assert stream.read() == b""  # terminal read verifies; no error


def test_verify_matching_adler32_passes() -> None:
    expected = (zlib.adler32(CONTENT) & 0xFFFFFFFF).to_bytes(4, "big")
    stream = VerifyingStream(io.BytesIO(CONTENT), {HashAlgorithm.ADLER32: expected})
    assert stream.read() == CONTENT


def test_verify_adler32_mismatch_raises() -> None:
    bad = ((zlib.adler32(CONTENT) & 0xFFFFFFFF) ^ 0xFFFF).to_bytes(4, "big")
    stream = VerifyingStream(io.BytesIO(CONTENT), {HashAlgorithm.ADLER32: bad})
    collected = bytearray()
    with pytest.raises(CorruptionError, match="adler32"):
        while True:
            chunk = stream.read(7)
            if not chunk:
                break
            collected.extend(chunk)
    assert bytes(collected) == CONTENT


def test_verify_multiple_algorithms() -> None:
    expected = {
        HashAlgorithm.CRC32: _crc32(CONTENT),
        "sha256": hashlib.sha256(CONTENT).digest(),
    }
    stream = VerifyingStream(io.BytesIO(CONTENT), expected)
    assert stream.read() == CONTENT


def test_verify_mismatch_raises_at_eof_without_losing_final_chunk() -> None:
    """Size-unknown: deliver every byte; raise on the terminal empty read (ADR 0014)."""
    bad = crc32_digest(zlib.crc32(CONTENT) ^ 0xFFFF)
    stream = VerifyingStream(io.BytesIO(CONTENT), {HashAlgorithm.CRC32: bad})
    collected = bytearray()
    with pytest.raises(CorruptionError, match="crc32"):
        while True:
            chunk = stream.read(7)
            if not chunk:
                break
            collected.extend(chunk)
    assert bytes(collected) == CONTENT  # every byte was delivered before the verdict


def test_verify_sized_mismatch_withholds_on_reaching_read() -> None:
    """Size-declared: reaching read raises and withholds that chunk (ADR 0014)."""
    bad = crc32_digest(zlib.crc32(CONTENT) ^ 0xFFFF)
    stream = VerifyingStream(
        io.BytesIO(CONTENT),
        {HashAlgorithm.CRC32: bad},
        expected_size=len(CONTENT),
    )
    with pytest.raises(CorruptionError, match="crc32"):
        stream.read(len(CONTENT))


def test_verify_sized_mismatch_chunked_withholds_final_chunk() -> None:
    bad = crc32_digest(zlib.crc32(CONTENT) ^ 0xFFFF)
    stream = VerifyingStream(
        io.BytesIO(CONTENT),
        {HashAlgorithm.CRC32: bad},
        expected_size=len(CONTENT),
    )
    collected = bytearray()
    with pytest.raises(CorruptionError, match="crc32"):
        while True:
            chunk = stream.read(7)
            if not chunk:
                break
            collected.extend(chunk)
    assert bytes(collected) != CONTENT
    assert len(collected) < len(CONTENT)
    assert CONTENT.startswith(bytes(collected))


def test_verify_partial_read_is_not_verified() -> None:
    bad = crc32_digest(zlib.crc32(CONTENT) ^ 0xFFFF)
    stream = VerifyingStream(io.BytesIO(CONTENT), {HashAlgorithm.CRC32: bad})
    assert stream.read(10) == CONTENT[:10]
    stream.close()  # abandoned before EOF — must not raise


def test_verify_slurp_raises_on_mismatch_not_close() -> None:
    """Complete-stream read() must raise on bad CRC so read(); close() cannot succeed."""
    bad = crc32_digest(zlib.crc32(CONTENT) ^ 0xFFFF)
    stream = VerifyingStream(io.BytesIO(CONTENT), {HashAlgorithm.CRC32: bad})
    with pytest.raises(CorruptionError, match="crc32"):
        stream.read()
    stream.close()  # teardown-only; must not raise the digest fault again


def test_verify_read_then_close_anti_footgun() -> None:
    bad = crc32_digest(zlib.crc32(CONTENT) ^ 0xFFFF)
    stream = VerifyingStream(io.BytesIO(CONTENT), {HashAlgorithm.CRC32: bad})
    with pytest.raises(CorruptionError, match="crc32"):
        data = stream.read()
        stream.close()
        del data


def test_verify_expected_size_exact_passes() -> None:
    stream = VerifyingStream(io.BytesIO(CONTENT), {}, expected_size=len(CONTENT))
    assert stream.read() == CONTENT
    assert stream.read() == b""


def test_verify_expected_size_short_raises_truncated() -> None:
    """A hash-less member that ends before its declared size raises TruncatedError on read."""
    stream = VerifyingStream(io.BytesIO(CONTENT), {}, expected_size=len(CONTENT) + 100)
    with pytest.raises(TruncatedError):
        stream.read()
    stream.close()  # quiet after the fault was observed on read


def test_verify_expected_size_short_chunked_then_empty_raises() -> None:
    stream = VerifyingStream(io.BytesIO(CONTENT), {}, expected_size=len(CONTENT) + 100)
    collected = bytearray()
    with pytest.raises(TruncatedError):
        while True:
            chunk = stream.read(64)
            if not chunk:
                break
            collected.extend(chunk)
    assert bytes(collected) == CONTENT
    stream.close()


def test_verify_expected_size_overlong_stops_at_declared_size() -> None:
    """An over-long stream raises on the reaching read and never returns past the cap."""
    inner = io.BytesIO(CONTENT)
    declared = len(CONTENT) - 200
    stream = VerifyingStream(inner, {}, expected_size=declared)
    out = bytearray()
    with pytest.raises(CorruptionError, match="exceeds"):
        while True:
            chunk = stream.read(64)
            if not chunk:
                break
            out.extend(chunk)
    # Withhold-on-reaching-read: the completing chunk is not returned.
    assert len(out) < declared
    assert inner.tell() <= declared + 1


def test_verify_hashed_overlong_with_matching_crc_still_capped() -> None:
    """F6: a CRC matching the bloated payload must not defeat the declared-size cap."""
    declared = 10
    bloated = b"A" * 200
    stream = VerifyingStream(
        io.BytesIO(bloated),
        {HashAlgorithm.CRC32: _crc32(bloated)},
        expected_size=declared,
    )
    out = bytearray()
    with pytest.raises(CorruptionError, match="exceeds"):
        while True:
            chunk = stream.read(64)
            if not chunk:
                break
            out.extend(chunk)
    assert len(out) < declared


def test_verify_expected_size_short_with_hash_is_truncated() -> None:
    """Short+hash raises TruncatedError (best-effort; shortfall vs digest may coincide)."""
    stream = VerifyingStream(
        io.BytesIO(CONTENT),
        {HashAlgorithm.CRC32: _crc32(CONTENT + b"more")},
        expected_size=len(CONTENT) + 4,
    )
    with pytest.raises(TruncatedError):
        stream.read()
    stream.close()


def test_verify_expected_size_partial_read_then_close_is_ok() -> None:
    """Deliberate partial read then close is not a truncation."""
    stream = VerifyingStream(io.BytesIO(CONTENT), {}, expected_size=len(CONTENT))
    assert stream.read(10) == CONTENT[:10]
    stream.close()  # must not raise


def test_verify_expected_size_short_abandon_before_empty_read_close_ok() -> None:
    """Deliver all available short bytes, then close without the terminal empty read.

    The TruncatedError surfaces only on the follow-up empty ``read`` (or on
    ``read(-1)``). Abandoning at the boundary — after the last data chunk, before
    that empty read — must not raise from ``close``.
    """
    stream = VerifyingStream(io.BytesIO(CONTENT), {}, expected_size=len(CONTENT) + 100)
    assert stream.read(len(CONTENT)) == CONTENT
    stream.close()  # must not raise TruncatedError


def test_verify_sized_readall_gathers_across_short_reads() -> None:
    """read(-1) must drain a short-reading BinaryIO and still fire the EOF verdict."""

    class ShortReading(io.BytesIO):
        def read(self, n: int = -1) -> bytes:  # noqa: A003
            if n is None or n < 0:
                n = 3
            return super().read(min(3, n) if n else 0)

    stream = VerifyingStream(
        ShortReading(CONTENT),
        {HashAlgorithm.CRC32: _crc32(CONTENT)},
        expected_size=len(CONTENT),
    )
    assert stream.read(-1) == CONTENT


def test_verify_sized_readall_overlong_stops_at_cap() -> None:
    """Sized read(-1) must not slurp past the declared decompression-bomb cap."""
    inner = io.BytesIO(CONTENT)
    declared = len(CONTENT) - 200
    stream = VerifyingStream(inner, {}, expected_size=declared)
    with pytest.raises(CorruptionError, match="exceeds"):
        stream.read(-1)
    assert inner.tell() <= declared + 1


def test_verify_read0_is_not_eof() -> None:
    """F1: read(0) must not run end-of-stream verification (BytesIO / file contract)."""
    stream = VerifyingStream(
        io.BytesIO(CONTENT), {HashAlgorithm.CRC32: _crc32(CONTENT)}
    )
    assert stream.read(0) == b""
    assert stream.read(5) == CONTENT[:5]
    assert stream.read(0) == b""  # mid-stream
    assert stream.read() == CONTENT[5:]
    assert stream.read() == b""


def test_verify_read0_hashless_does_not_truncate_on_close() -> None:
    stream = VerifyingStream(io.BytesIO(CONTENT), {}, expected_size=len(CONTENT))
    assert stream.read(0) == b""
    stream.close()  # must not raise TruncatedError


def test_verify_full_count_over_short_reading_inner() -> None:
    """Bounded read(n) coalesces across fill-or-EOF shorts (stop on terminal short).

    Inners that only short at EOF (``DecompressorStream``, typical ``ZipExtFile``)
    are full-count for healthy data. A RawIOBase that shorts mid-stream needs a
    buffer in front; we do not keep pulling after a short, so deferred truncation
    on ``DecompressorStream`` still returns the prefix from this call.
    """

    class FillOrEof(io.BytesIO):
        """Returns at most 3 bytes per read, but empty only at true EOF."""

        def read(self, n: int = -1) -> bytes:  # noqa: A003
            if n is None or n < 0:
                return super().read(n)
            if n == 0:
                return b""
            return super().read(min(3, n))

    # Stop-on-short: one bounded read gets one 3-byte piece. Full gather needs a
    # drain loop (read(-1)) or an already-full-count inner — covered elsewhere.
    stream = VerifyingStream(
        FillOrEof(CONTENT),
        {HashAlgorithm.CRC32: _crc32(CONTENT)},
        expected_size=len(CONTENT),
    )
    assert stream.read(50) == CONTENT[:3]
    # Completing drain still verifies.
    assert stream.read(-1) == CONTENT[3:]


def test_verify_exact_available_read_then_close_is_quiet() -> None:
    """read(k) when k == available < declared succeeds; truncation only past available."""
    stream = VerifyingStream(io.BytesIO(CONTENT), {}, expected_size=len(CONTENT) + 100)
    assert stream.read(len(CONTENT) - 1) == CONTENT[:-1]
    assert stream.read(1) == CONTENT[-1:]
    stream.close()  # quiet — never asked past available


def test_verify_seek_forfeits_checksum_keeps_length() -> None:
    """Seek off frontier disables CRC but still raises TruncatedError when short."""
    stream = VerifyingStream(
        io.BytesIO(CONTENT),
        {HashAlgorithm.CRC32: _crc32(CONTENT + b"x")},  # would mismatch if checked
        expected_size=len(CONTENT) + 4,
    )
    assert stream.read(10) == CONTENT[:10]
    stream.seek(0)
    assert not stream._verifier.digests_enabled
    with pytest.raises(TruncatedError):
        stream.read(-1)
    stream.close()


def test_archive_stream_passthrough_full_count() -> None:
    """Unverified ArchiveStream.read(n) coalesces until n or a terminal short."""
    from archivey.internal.streams.archive_stream import ArchiveStream

    stream = ArchiveStream(
        lambda: io.BytesIO(CONTENT),
        translate=lambda _exc: None,
    )
    assert stream.read(40) == CONTENT[:40]
    assert stream.read(10) == CONTENT[40:50]
    stream.close()


def test_verify_close_propagates_inner_close_error_and_closes_wrapper() -> None:
    """A teardown/integrity error raised by the inner's *own* close() propagates,
    and the wrapper is still marked closed. close() never probes/reads the inner to
    force a verdict — that is the read path's job — so this error can only come from
    inner.close() itself (e.g. WinZip AES HMAC verification on close)."""
    from archivey.exceptions import EncryptionError

    class AuthOnClose(io.BytesIO):
        def __init__(self) -> None:
            super().__init__(b"ab")
            self.close_called = False

        def close(self) -> None:
            self.close_called = True
            super().close()
            raise EncryptionError("boom")  # integrity verified in the inner's close

    inner = AuthOnClose()
    stream = VerifyingStream(inner, {}, expected_size=3)
    assert stream.read(2) == b"ab"
    with pytest.raises(EncryptionError, match="boom"):
        stream.close()
    assert stream.closed
    assert inner.close_called


def test_verify_close_quiet_when_inner_defers_truncation() -> None:
    """Abandon a truncated verified stream at the recoverable-prefix boundary: close
    must stay quiet. close() must not probe-read the inner and trip its *deferred*
    TruncatedError — that is the never-raise-a-first-content-fault-on-close rule, and
    it must match the plain (non-verified) DecompressorStream, which closes quietly."""
    from archivey.internal.streams.decompress import GzipDecompressorStream

    body = b"payload-" * 8
    trunc = gzip.compress(body)[:-6]  # cut the gzip trailer → deferred TruncatedError

    # Recoverable prefix length via the plain stream (read until the deferred raise).
    plain = GzipDecompressorStream(io.BytesIO(trunc))
    prefix = bytearray()
    with pytest.raises(TruncatedError):
        while True:
            c = plain.read(4)
            if not c:
                break
            prefix.extend(c)
    plain.close()  # plain stream: quiet after the error was observed

    # Verified stream: read exactly the prefix (no terminal empty read), then close.
    inner = GzipDecompressorStream(io.BytesIO(trunc))
    vs = VerifyingStream(inner, {HashAlgorithm.CRC32: crc32_digest(zlib.crc32(body))})
    got = bytearray()
    while len(got) < len(prefix):
        got.extend(vs.read(min(4, len(prefix) - len(got))))
    assert bytes(got) == bytes(prefix)
    vs.close()  # quiet: abandon before clean EOF, close never surfaces the truncation


def test_verify_unverifiable_algorithm_skipped_with_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level("WARNING", logger="archivey.integrity"):
        stream = VerifyingStream(
            io.BytesIO(CONTENT), {"not_a_real_digest_algo": b"\x00" * 32}
        )
        assert stream.read() == CONTENT  # no raise: the algorithm is just skipped
    assert any("not_a_real_digest_algo" in rec.message for rec in caplog.records)


def test_verify_blake2sp_match() -> None:
    from archivey.internal.hashing.blake2sp import blake2sp

    expected = {HashAlgorithm.BLAKE2SP: blake2sp(CONTENT)}
    stream = VerifyingStream(io.BytesIO(CONTENT), expected)
    assert stream.read() == CONTENT


def test_verify_blake2sp_mismatch_raises() -> None:
    stream = VerifyingStream(
        io.BytesIO(CONTENT), {HashAlgorithm.BLAKE2SP: b"\x00" * 32}
    )
    with pytest.raises(CorruptionError, match="blake2sp"):
        while stream.read(64):
            pass


def test_verify_crc32_accepts_bytes_form() -> None:
    expected = {HashAlgorithm.CRC32: _crc32(CONTENT)}
    stream = VerifyingStream(io.BytesIO(CONTENT), expected)
    assert stream.read() == CONTENT


def test_verify_wrong_width_digest_mismatches_not_raises() -> None:
    """A stored digest wider than the hasher's digest_size must surface as CorruptionError.

    Callers always pass ``bytes``; a malformed width must compare unequal (and raise
    :class:`CorruptionError`) rather than raising a non-ArchiveyError from the
    stream layer.
    """
    wrong_width = _crc32(CONTENT) + b"\x00\x00"  # 6 bytes vs CRC-32's 4
    stream = VerifyingStream(io.BytesIO(CONTENT), {HashAlgorithm.CRC32: wrong_width})
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


def test_codec_stream_size_via_cheap_index(tmp_path) -> None:
    # An xz codec stream reports its decompressed size through the seekable reader's
    # try_get_size (a backward index scan, no decompression), surfaced as `.size`.
    import lzma

    payload = b"sizeable " * 4096
    stream = open_codec_stream(
        Codec.XZ, io.BytesIO(lzma.compress(payload)), config=StreamConfig(seekable=True)
    )
    assert stream.size == len(payload)
    # gzip via stdlib has no cheap index; its stream must not claim a size.
    gz = open_codec_stream(
        Codec.GZIP, io.BytesIO(gzip.compress(payload)), config=_STDLIB_GZIP
    )
    assert gz.size is None


def _gzipfile_read1_prefix(truncated: bytes) -> bytes:
    """Max recoverable prefix via GzipFile read(1) loop (oracle for truncated gzip)."""
    gf = gzip.GzipFile(fileobj=io.BytesIO(truncated))
    buf = bytearray()
    try:
        while True:
            c = gf.read(1)
            if not c:
                break
            buf.extend(c)
    except EOFError:
        pass
    return bytes(buf)


def test_truncated_gzip_large_read_recovers_prefix_like_read1() -> None:
    compressed = gzip.compress(CONTENT)
    truncated = compressed[: len(compressed) // 2]
    oracle = _gzipfile_read1_prefix(truncated)
    with open_codec_stream(
        Codec.GZIP, io.BytesIO(truncated), config=_STDLIB_GZIP
    ) as stream:
        data = stream.read(65536)
        assert data == oracle
        with pytest.raises(TruncatedError):
            stream.read(1)
        stream.close()  # quiet after observed truncation


def test_truncated_gzip_readall_raises() -> None:
    compressed = gzip.compress(CONTENT)
    truncated = compressed[: len(compressed) // 2]
    with open_codec_stream(
        Codec.GZIP, io.BytesIO(truncated), config=_STDLIB_GZIP
    ) as stream:
        with pytest.raises(TruncatedError):
            stream.read()


def test_truncated_gzip_seek_end_does_not_report_clean_size() -> None:
    compressed = gzip.compress(CONTENT)
    truncated = compressed[: len(compressed) // 2]
    with open_codec_stream(
        Codec.GZIP, io.BytesIO(truncated), config=_STDLIB_GZIP
    ) as stream:
        stream.read(256)  # deliver some prefix
        with pytest.raises(TruncatedError):
            while True:
                if not stream.read(256):
                    break
        with pytest.raises(TruncatedError):
            stream.seek(0, io.SEEK_END)


def test_truncated_zlib_deflate_large_read_recovers_prefix() -> None:
    from archivey.internal.streams.decompress import ZlibDecompressorStream

    for wbits, raw in (
        (-15, zlib.compress(CONTENT)[2:-4]),  # raw deflate
        (zlib.MAX_WBITS, zlib.compress(CONTENT)),
    ):
        truncated = raw[: len(raw) // 2]
        with ZlibDecompressorStream(io.BytesIO(truncated), wbits=wbits) as stream:
            prefix = stream.read(65536)
            assert prefix
            with pytest.raises(TruncatedError):
                stream.read(1)
            stream.close()
        with ZlibDecompressorStream(io.BytesIO(truncated), wbits=wbits) as stream:
            with pytest.raises(TruncatedError):
                stream.read()


def test_gzip_multi_member_and_padding_parity() -> None:
    m1 = gzip.compress(b"first")
    m2 = gzip.compress(b"second")
    with open_codec_stream(
        Codec.GZIP, io.BytesIO(m1 + m2), config=_STDLIB_GZIP
    ) as stream:
        assert stream.read() == b"firstsecond"
    with open_codec_stream(
        Codec.GZIP, io.BytesIO(m1 + b"\x00\x00\x00\x00" + m2), config=_STDLIB_GZIP
    ) as stream:
        assert stream.read() == b"firstsecond"
    with open_codec_stream(
        Codec.GZIP, io.BytesIO(m1 + b"\x00\x00\x00"), config=_STDLIB_GZIP
    ) as stream:
        assert stream.read() == b"first"


def test_gzip_trailing_junk_delivers_member_then_corruption() -> None:
    """Trailing junk after a valid member: deliver every member byte, then raise.

    Matches GzipFile read(1) oracle / deliver-then-raise (F1). ``readall`` still
    raises without returning the prefix (complete-stream contract).
    """
    payload = b"hello world " * 10
    member = gzip.compress(payload)
    junked = member + b"NOTGZIP!"

    # Large bounded read recovers the full member, then empty read raises.
    with open_codec_stream(
        Codec.GZIP, io.BytesIO(junked), config=_STDLIB_GZIP
    ) as stream:
        assert stream.read(65536) == payload
        with pytest.raises(CorruptionError, match="Trailing non-gzip"):
            stream.read(1)
        stream.close()

    # Chunked reads deliver every byte before the verdict.
    with open_codec_stream(
        Codec.GZIP, io.BytesIO(junked), config=_STDLIB_GZIP
    ) as stream:
        collected = bytearray()
        with pytest.raises(CorruptionError, match="Trailing non-gzip"):
            while True:
                chunk = stream.read(7)
                if not chunk:
                    break
                collected.extend(chunk)
        assert bytes(collected) == payload

    # Slurping readall raises (no silent lossy success).
    with open_codec_stream(
        Codec.GZIP, io.BytesIO(junked), config=_STDLIB_GZIP
    ) as stream:
        with pytest.raises(CorruptionError, match="Trailing non-gzip"):
            stream.read()


def test_gzip_multi_member_cross_feed_edges() -> None:
    """NUL padding / magic split across small reads must still concatenate."""
    m1 = gzip.compress(b"aa")
    m2 = gzip.compress(b"bb")
    # Bytewise output across a padded boundary.
    with open_codec_stream(
        Codec.GZIP, io.BytesIO(m1 + b"\x00\x00" + m2), config=_STDLIB_GZIP
    ) as stream:
        buf = bytearray()
        while True:
            c = stream.read(1)
            if not c:
                break
            buf.extend(c)
        assert bytes(buf) == b"aabb"
    # Lone trailing partial magic at EOF → deliver member, then CorruptionError.
    with open_codec_stream(
        Codec.GZIP, io.BytesIO(m1 + b"\x1f"), config=_STDLIB_GZIP
    ) as stream:
        assert stream.read(65536) == b"aa"
        with pytest.raises(CorruptionError, match="Trailing non-gzip"):
            stream.read(1)


def test_gzip_empty_and_empty_payload_member() -> None:
    with open_codec_stream(
        Codec.GZIP, io.BytesIO(gzip.compress(b"")), config=_STDLIB_GZIP
    ) as stream:
        assert stream.read() == b""
    # Empty-payload member concatenated with a real member.
    with open_codec_stream(
        Codec.GZIP,
        io.BytesIO(gzip.compress(b"") + gzip.compress(b"x")),
        config=_STDLIB_GZIP,
    ) as stream:
        assert stream.read() == b"x"


def test_truncated_gzip_mid_second_member_delivers_first_plus_partial() -> None:
    m1 = gzip.compress(b"AAAA" * 50)
    m2 = gzip.compress(b"BBBB" * 50)
    truncated = m1 + m2[: len(m2) // 2]
    with open_codec_stream(
        Codec.GZIP, io.BytesIO(truncated), config=_STDLIB_GZIP
    ) as stream:
        data = stream.read(65536)
        assert data.startswith(b"AAAA" * 50)
        assert len(data) > len(b"AAAA" * 50)
        with pytest.raises(TruncatedError):
            stream.read(1)


@requires("ncompress")
def test_unix_compress_truncated_close_quiet_and_size_unknown() -> None:
    compressed = make_unix_compress(CONTENT)
    truncated = compressed[: len(compressed) // 2]
    with open_codec_stream(Codec.UNIX_COMPRESS, io.BytesIO(truncated)) as stream:
        chunk = stream.read(256)
        assert chunk
        with pytest.raises(TruncatedError, match="leftover bits"):
            while True:
                c = stream.read(256)
                if not c:
                    break
        stream.close()  # quiet
    with open_codec_stream(Codec.UNIX_COMPRESS, io.BytesIO(truncated)) as stream:
        with pytest.raises(TruncatedError):
            stream.read()
        # Must not publish the prefix length as a clean complete size.
        assert stream.size is None
        with pytest.raises(TruncatedError):
            stream.seek(0, io.SEEK_END)


def test_verify_fused_archive_stream_slurp_raises() -> None:
    """Fused MemberVerifier on ArchiveStream: read() raises on bad CRC (not close)."""
    from archivey.internal.streams.archive_stream import ArchiveStream

    bad = crc32_digest(zlib.crc32(CONTENT) ^ 0xFFFF)
    stream = ArchiveStream(
        lambda: io.BytesIO(CONTENT),
        translate=lambda _exc: None,
        expected_hashes={HashAlgorithm.CRC32: bad},
    )
    with pytest.raises(CorruptionError, match="crc32"):
        stream.read()
    stream.close()
