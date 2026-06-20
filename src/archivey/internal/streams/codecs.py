"""The uniform, pull-based codec layer.

This is the single place codecs are implemented; format backends compose these stream
backends instead of importing codec libraries (the ``compressed-streams`` contract). Each
codec has one default backend (stdlib where possible, an optional package otherwise), a
matching exception translator, and a resolver that hands back the open function + its
translator *without* opening a stream (so detection / the TAR reader / the 7z folder
pipeline can reuse the right backend).

Scope here is the spec's codec table. The AES decrypt **stage** lives in ``crypto.py`` and
the decompressed-digest verification stage in ``verify.py`` — both compose with these
codec streams in a pipeline.
"""

from __future__ import annotations

import bz2
import gzip
import importlib
import io
import lzma
import os
import zlib
from dataclasses import dataclass, field
from enum import Enum
from types import ModuleType
from typing import TYPE_CHECKING, BinaryIO, Callable

from archivey.internal.config import DEFAULT_STREAM_CONFIG, StreamConfig
from archivey.internal.errors import (
    ArchiveyError,
    CorruptionError,
    PackageNotInstalledError,
    StreamNotSeekableError,
    TruncatedError,
)
from archivey.internal.logs import streams as logger
from archivey.internal.streams.archive_stream import ArchiveStream, ExceptionTranslator
from archivey.internal.streams.binaryio import (
    ensure_binaryio,
    ensure_bufferedio,
)
from archivey.internal.streams.decompress import (
    BrotliDecompressorStream,
    ZlibDecompressorStream,
)
from archivey.internal.streams.lzip import LzipDecompressorStream
from archivey.internal.streams.xz import XzDecompressorStream
from archivey.internal.types import StreamFormat

if TYPE_CHECKING:
    from _typeshed import WriteableBuffer


# Optional packages: resolved once via importlib (rather than static imports) because
# several of these have no type stubs and are absent in the core-only environment. Absence
# becomes a clear PackageNotInstalledError when the corresponding codec is opened.
def _optional(name: str) -> ModuleType | None:
    try:
        return importlib.import_module(name)
    except ImportError:  # pragma: no cover - the absent path runs in the core-only CI leg
        return None


_zstandard = _optional("zstandard")
_lz4_frame = _optional("lz4.frame")
_brotli = _optional("brotli")
_pyppmd = _optional("pyppmd")
_inflate64 = _optional("inflate64")
_rapidgzip = _optional("rapidgzip")
_indexed_bzip2 = _optional("indexed_bzip2")


CodecSource = str | os.PathLike[str] | BinaryIO


class Codec(Enum):
    """The codecs the stream layer can decompress (the ``compressed-streams`` table).

    Single-file/TAR stream formats and 7z/ZIP folder coders both resolve to these.
    Filter-only entries (Delta, the BCJ family) are not opened standalone — they compose
    into a raw-LZMA filter chain (built by the 7z reader in Phase 7); their LZMA filter ids
    are recorded in :data:`LZMA_FILTER_IDS`.
    """

    STORED = "stored"
    GZIP = "gzip"
    BZIP2 = "bzip2"
    XZ = "xz"
    LZIP = "lzip"
    LZMA = "lzma"  # raw LZMA1 (FORMAT_RAW + properties)
    LZMA2 = "lzma2"  # raw LZMA2 (FORMAT_RAW + properties)
    DEFLATE = "deflate"  # raw deflate (zlib -15)
    ZLIB = "zlib"  # zlib-wrapped deflate
    ZSTD = "zstd"
    LZ4 = "lz4"
    BROTLI = "brotli"
    PPMD = "ppmd"
    DEFLATE64 = "deflate64"
    # Filter-only (composed with raw LZMA; see LZMA_FILTER_IDS).
    DELTA = "delta"
    BCJ_X86 = "bcj_x86"
    BCJ_ARM = "bcj_arm"
    BCJ_ARMT = "bcj_armt"
    BCJ_PPC = "bcj_ppc"
    BCJ_SPARC = "bcj_sparc"
    BCJ_IA64 = "bcj_ia64"


# LZMA raw-filter ids for the filter-only codecs, for assembling 7z coder chains.
LZMA_FILTER_IDS: dict[Codec, int] = {
    Codec.DELTA: lzma.FILTER_DELTA,
    Codec.BCJ_X86: lzma.FILTER_X86,
    Codec.BCJ_ARM: lzma.FILTER_ARM,
    Codec.BCJ_ARMT: lzma.FILTER_ARMTHUMB,
    Codec.BCJ_PPC: lzma.FILTER_POWERPC,
    Codec.BCJ_SPARC: lzma.FILTER_SPARC,
    Codec.BCJ_IA64: lzma.FILTER_IA64,
}

# StreamFormat (single-file / TAR stream) → Codec. 7z/ZIP coder-id mapping lands with
# those readers; this is the single-file/TAR side of "a codec is implemented once".
_STREAM_FORMAT_CODECS: dict[StreamFormat, Codec] = {
    StreamFormat.UNCOMPRESSED: Codec.STORED,
    StreamFormat.GZIP: Codec.GZIP,
    StreamFormat.BZIP2: Codec.BZIP2,
    StreamFormat.XZ: Codec.XZ,
    StreamFormat.ZSTD: Codec.ZSTD,
    StreamFormat.LZ4: Codec.LZ4,
}


def codec_for_stream_format(stream_format: StreamFormat) -> Codec:
    """Map a single-file/TAR ``StreamFormat`` to its codec."""
    return _STREAM_FORMAT_CODECS[stream_format]


@dataclass(frozen=True)
class CodecParams:
    """Per-open parameters that vary by container/coder.

    - ``filters`` — the ``lzma`` raw filter chain (required for raw LZMA1/LZMA2; this is
      where Delta/BCJ stages and the coder properties enter).
    """

    filters: list[dict] | None = None


_DEFAULT_PARAMS = CodecParams()


# --- exception translators -------------------------------------------------------------


def _translate_none(_e: Exception) -> ArchiveyError | None:
    return None


def _translate_gzip(e: Exception) -> ArchiveyError | None:
    if isinstance(e, gzip.BadGzipFile):
        return CorruptionError(f"Error reading gzip stream: {e!r}")
    if isinstance(e, EOFError):
        return TruncatedError(f"gzip stream is truncated: {e!r}")
    return None


def _translate_bz2(e: Exception) -> ArchiveyError | None:
    if isinstance(e, OSError) and "Invalid data stream" in str(e):
        return CorruptionError(f"bzip2 stream is corrupt: {e!r}")
    if isinstance(e, (EOFError, ValueError)):
        return TruncatedError(f"bzip2 stream is truncated: {e!r}")
    return None


def _translate_rapidgzip(e: Exception) -> ArchiveyError | None:
    """Translate the rapidgzip accelerator's exceptions to the library's error types."""
    text = str(e)
    if isinstance(e, ValueError) and "Mismatching CRC32" in text:
        return CorruptionError(f"Error reading gzip stream (rapidgzip): {e!r}")
    if isinstance(e, RuntimeError) and "IsalInflateWrapper" in text:
        return CorruptionError(f"Error reading gzip stream (rapidgzip): {e!r}")
    if isinstance(e, ValueError) and "Failed to detect a valid file format" in text:
        # The gzip magic was present when we opened it, so a detection failure now means
        # the stream is truncated/corrupt rather than not-a-gzip.
        return CorruptionError(f"Error reading gzip stream (rapidgzip): {e!r}")
    if isinstance(e, ValueError) and "End of file encountered" in text:
        return TruncatedError(f"gzip stream is truncated (rapidgzip): {e!r}")
    if isinstance(e, ValueError) and "has no valid fileno" in text:
        return StreamNotSeekableError("rapidgzip does not support non-seekable streams")
    if isinstance(e, io.UnsupportedOperation) and "seek" in text:
        return StreamNotSeekableError("rapidgzip does not support non-seekable streams")
    if isinstance(e, RuntimeError) and "std::exception" in text:
        return CorruptionError(f"Error reading gzip stream (rapidgzip): {e!r}")
    return None


def _translate_indexed_bzip2(e: Exception) -> ArchiveyError | None:
    """Translate the indexed_bzip2 accelerator's exceptions to the library's error types."""
    text = str(e)
    if isinstance(e, RuntimeError) and "Calculated CRC" in text:
        return CorruptionError(f"Error reading bzip2 stream (indexed_bzip2): {e!r}")
    if isinstance(e, RuntimeError) and text in ("std::exception", "Unknown exception"):
        return CorruptionError(f"Error reading bzip2 stream (indexed_bzip2): {e!r}")
    if isinstance(e, ValueError) and "[BZip2 block data]" in text:
        return CorruptionError(f"Error reading bzip2 stream (indexed_bzip2): {e!r}")
    if isinstance(e, ValueError) and "has no valid fileno" in text:
        return StreamNotSeekableError("indexed_bzip2 does not support non-seekable streams")
    if isinstance(e, io.UnsupportedOperation) and "seek" in text:
        return StreamNotSeekableError("indexed_bzip2 does not support non-seekable streams")
    return None


def _translate_lzma(e: Exception) -> ArchiveyError | None:
    if isinstance(e, lzma.LZMAError):
        return CorruptionError(f"Error reading LZMA/XZ stream: {e!r}")
    if isinstance(e, EOFError):
        return TruncatedError(f"LZMA/XZ stream is truncated: {e!r}")
    return None


def _translate_zlib(e: Exception) -> ArchiveyError | None:
    if isinstance(e, zlib.error):
        text = str(e)
        if "incomplete" in text or "truncated" in text:
            return TruncatedError(f"deflate stream is truncated: {e!r}")
        return CorruptionError(f"Error reading deflate stream: {e!r}")
    if isinstance(e, EOFError):
        return TruncatedError(f"deflate stream is truncated: {e!r}")
    return None


def _translate_zstd(e: Exception) -> ArchiveyError | None:
    if _zstandard is not None and isinstance(e, _zstandard.ZstdError):
        return CorruptionError(f"Error reading zstandard stream: {e!r}")
    if isinstance(e, EOFError):
        return TruncatedError(f"zstandard stream is truncated: {e!r}")
    return None


def _translate_lz4(e: Exception) -> ArchiveyError | None:
    if isinstance(e, RuntimeError) and str(e).startswith("LZ4"):
        return CorruptionError(f"Error reading lz4 stream: {e!r}")
    if isinstance(e, EOFError):
        return TruncatedError(f"lz4 stream is truncated: {e!r}")
    return None


def _translate_brotli(e: Exception) -> ArchiveyError | None:
    # brotli raises its own brotli.error for corrupt data; a truncated stream doesn't
    # raise here (the decompressor just never reports finished), so the base
    # DecompressorStream surfaces that as TruncatedError on its own.
    if _brotli is not None and isinstance(e, _brotli.error):
        return CorruptionError(f"Error reading brotli stream: {e!r}")
    return None


def _translate_ppmd(e: Exception) -> ArchiveyError | None:
    if isinstance(e, EOFError):
        return TruncatedError(f"PPMd stream is truncated: {e!r}")
    if isinstance(e, ValueError):
        return CorruptionError(f"Error reading PPMd stream: {e!r}")
    return None


def _translate_deflate64(e: Exception) -> ArchiveyError | None:
    if isinstance(e, EOFError):
        return TruncatedError(f"deflate64 stream is truncated: {e!r}")
    if isinstance(e, (ValueError, zlib.error)):
        return CorruptionError(f"Error reading deflate64 stream: {e!r}")
    return None


# --- open functions --------------------------------------------------------------------


class _SlowSeekWarningStream(io.RawIOBase, BinaryIO):
    """Delegate to a sequential stdlib decoder, warning once on a rewinding seek.

    stdlib ``gzip``/``bz2`` streams *can* seek, but a backward seek re-decompresses the
    stream from the start — O(n) per rewind. We don't forbid that (no format here has a
    fast index, and a slow seek still beats failing), but we don't let it pass silently
    either: the first rewinding seek logs a warning pointing at the ``[seekable]``
    accelerator. Forward seeks (linear decompression) and no-op seeks stay quiet.
    """

    def __init__(self, inner: BinaryIO, *, codec_name: str, accelerator: str) -> None:
        super().__init__()
        self._inner = inner
        self._codec_name = codec_name
        self._accelerator = accelerator
        self._warned = False

    def read(self, n: int = -1, /) -> bytes:
        return self._inner.read(n)

    def readinto(self, b: "WriteableBuffer", /) -> int:
        raw_readinto = getattr(self._inner, "readinto", None)
        if raw_readinto is not None:
            return raw_readinto(b)
        mv = memoryview(b).cast("B")
        data = self._inner.read(len(mv))
        mv[: len(data)] = data
        return len(data)

    def seek(self, offset: int, whence: int = io.SEEK_SET, /) -> int:
        before = self._inner.tell()
        result = self._inner.seek(offset, whence)
        if not self._warned and result < before:
            logger.warning(
                "Seeking backward in a %s stream without a random-access accelerator "
                "re-decompresses from the start (O(n) per rewind). Install the 'seekable' "
                "extra (%s) for indexed random access.",
                self._codec_name,
                self._accelerator,
            )
            self._warned = True
        return result

    def tell(self, /) -> int:
        return self._inner.tell()

    def readable(self) -> bool:
        return True

    def writable(self) -> bool:
        return False

    def seekable(self) -> bool:
        return self._inner.seekable()

    def close(self) -> None:
        self._inner.close()
        super().close()


def _open_stored(source: CodecSource, params: CodecParams, config: StreamConfig) -> BinaryIO:
    if isinstance(source, (str, os.PathLike)):
        return open(os.fspath(source), "rb")
    return ensure_binaryio(source)


def _open_gzip(source: CodecSource, params: CodecParams, config: StreamConfig) -> BinaryIO:
    if config.use_rapidgzip.enabled_for(
        streaming=config.streaming, available=_rapidgzip is not None
    ):
        if _rapidgzip is None:
            raise PackageNotInstalledError(
                "The 'rapidgzip' package is required for gzip random access "
                "(install the 'seekable' extra)."
            )
        return ensure_binaryio(_rapidgzip.open(source, parallelization=0))
    if isinstance(source, (str, os.PathLike)):
        gz: BinaryIO = ensure_binaryio(gzip.open(source, "rb"))
    else:
        gz = ensure_binaryio(gzip.GzipFile(fileobj=ensure_bufferedio(source), mode="rb"))
    # stdlib gzip can seek, but a rewind re-decompresses from the start; warn rather than
    # degrade silently (the [seekable] rapidgzip accelerator gives real random access).
    return _SlowSeekWarningStream(gz, codec_name="gzip", accelerator="rapidgzip")


def _open_bzip2(source: CodecSource, params: CodecParams, config: StreamConfig) -> BinaryIO:
    if config.use_indexed_bzip2.enabled_for(
        streaming=config.streaming, available=_indexed_bzip2 is not None
    ):
        if _indexed_bzip2 is None:
            raise PackageNotInstalledError(
                "The 'indexed_bzip2' package is required for bzip2 random access "
                "(install the 'seekable' extra)."
            )
        return ensure_binaryio(_indexed_bzip2.open(source, parallelization=0))
    # stdlib bz2 can seek, but a rewind re-decompresses from the start; warn rather than
    # degrade silently (the [seekable] indexed_bzip2 accelerator gives real random access).
    return _SlowSeekWarningStream(
        ensure_binaryio(bz2.open(source, "rb")),
        codec_name="bzip2",
        accelerator="indexed_bzip2",
    )


def _open_xz(source: CodecSource, params: CodecParams, config: StreamConfig) -> BinaryIO:
    return XzDecompressorStream(source)


def _open_lzip(source: CodecSource, params: CodecParams, config: StreamConfig) -> BinaryIO:
    return LzipDecompressorStream(source)


def _open_lzma_raw(source: CodecSource, params: CodecParams, config: StreamConfig) -> BinaryIO:
    if params.filters is None:
        raise ValueError("raw LZMA decoding requires filter properties (CodecParams.filters)")
    return ensure_binaryio(
        lzma.LZMAFile(source, mode="rb", format=lzma.FORMAT_RAW, filters=params.filters)
    )


def _open_deflate(source: CodecSource, params: CodecParams, config: StreamConfig) -> BinaryIO:
    return ZlibDecompressorStream(source, wbits=-15)


def _open_zlib(source: CodecSource, params: CodecParams, config: StreamConfig) -> BinaryIO:
    return ZlibDecompressorStream(source, wbits=zlib.MAX_WBITS)


def _open_zstd(source: CodecSource, params: CodecParams, config: StreamConfig) -> BinaryIO:
    if _zstandard is None:
        raise PackageNotInstalledError(
            "The 'zstandard' package is required for zstd streams (install the 'zstd' extra)."
        )
    return ensure_binaryio(_zstandard.open(source, "rb"))


def _open_lz4(source: CodecSource, params: CodecParams, config: StreamConfig) -> BinaryIO:
    if _lz4_frame is None:
        raise PackageNotInstalledError(
            "The 'lz4' package is required for lz4 streams (install the 'lz4' extra)."
        )
    return ensure_binaryio(_lz4_frame.open(source, "rb"))


def _open_brotli(source: CodecSource, params: CodecParams, config: StreamConfig) -> BinaryIO:
    if _brotli is None:
        raise PackageNotInstalledError(
            "The 'brotli' package is required for Brotli streams (install the '7z' extra)."
        )
    return BrotliDecompressorStream(source)


def _open_ppmd(source: CodecSource, params: CodecParams, config: StreamConfig) -> BinaryIO:
    if _pyppmd is None:
        raise PackageNotInstalledError(
            "The 'pyppmd' package is required for PPMd streams (install the '7z' extra)."
        )
    # The concrete PPMd stream construction (var.H parameters from the 7z coder) lands with
    # the native 7z reader in Phase 7; the resolver + missing-backend gating are complete.
    raise NotImplementedError("PPMd decoding is implemented in Phase 7 (native 7z reader)")


def _open_deflate64(source: CodecSource, params: CodecParams, config: StreamConfig) -> BinaryIO:
    if _inflate64 is None:
        raise PackageNotInstalledError(
            "The 'inflate64' package is required for Deflate64 streams (install the '7z' extra)."
        )
    return ensure_binaryio(_inflate64.Inflate64File(ensure_bufferedio(source)))


@dataclass(frozen=True)
class _CodecSpec:
    open: Callable[[CodecSource, CodecParams, StreamConfig], BinaryIO]
    translate: ExceptionTranslator


_REGISTRY: dict[Codec, _CodecSpec] = {
    Codec.STORED: _CodecSpec(_open_stored, _translate_none),
    Codec.GZIP: _CodecSpec(_open_gzip, _translate_gzip),
    Codec.BZIP2: _CodecSpec(_open_bzip2, _translate_bz2),
    Codec.XZ: _CodecSpec(_open_xz, _translate_lzma),
    Codec.LZIP: _CodecSpec(_open_lzip, _translate_lzma),
    Codec.LZMA: _CodecSpec(_open_lzma_raw, _translate_lzma),
    Codec.LZMA2: _CodecSpec(_open_lzma_raw, _translate_lzma),
    Codec.DEFLATE: _CodecSpec(_open_deflate, _translate_zlib),
    Codec.ZLIB: _CodecSpec(_open_zlib, _translate_zlib),
    Codec.ZSTD: _CodecSpec(_open_zstd, _translate_zstd),
    Codec.LZ4: _CodecSpec(_open_lz4, _translate_lz4),
    Codec.BROTLI: _CodecSpec(_open_brotli, _translate_brotli),
    Codec.PPMD: _CodecSpec(_open_ppmd, _translate_ppmd),
    Codec.DEFLATE64: _CodecSpec(_open_deflate64, _translate_deflate64),
}


@dataclass(frozen=True)
class CodecBackend:
    """A resolved codec backend: its open function (config-bound) and its translator.

    Returned by :func:`resolve_codec` so callers can obtain (and reuse) the backend
    without opening a stream — the "backend dispatch is separable from opening" contract.
    """

    codec: Codec
    config: StreamConfig
    translate: ExceptionTranslator
    _open: Callable[[CodecSource, CodecParams, StreamConfig], BinaryIO] = field(repr=False)

    def open(self, source: CodecSource, params: CodecParams = _DEFAULT_PARAMS) -> BinaryIO:
        return self._open(source, params, self.config)


def _gzip_uses_accelerator(config: StreamConfig) -> bool:
    return _rapidgzip is not None and config.use_rapidgzip.enabled_for(
        streaming=config.streaming, available=True
    )


def _bzip2_uses_accelerator(config: StreamConfig) -> bool:
    return _indexed_bzip2 is not None and config.use_indexed_bzip2.enabled_for(
        streaming=config.streaming, available=True
    )


def resolve_codec(codec: Codec, config: StreamConfig = DEFAULT_STREAM_CONFIG) -> CodecBackend:
    """Resolve ``codec`` to its backend (open function + translator) without opening anything.

    The translator must match the *active* backend: when an accelerator
    (``rapidgzip`` / ``indexed_bzip2``) is the chosen backend, its exception taxonomy
    differs from stdlib's, so the matching translator is selected here.

    Raises ``KeyError`` for a filter-only codec (Delta/BCJ), which is composed into a raw
    LZMA chain rather than opened standalone.
    """
    spec = _REGISTRY[codec]
    translate = spec.translate
    if codec is Codec.GZIP and _gzip_uses_accelerator(config):
        translate = _translate_rapidgzip
    elif codec is Codec.BZIP2 and _bzip2_uses_accelerator(config):
        translate = _translate_indexed_bzip2
    return CodecBackend(codec=codec, config=config, translate=translate, _open=spec.open)


def open_codec_stream(
    codec: Codec,
    source: CodecSource,
    *,
    config: StreamConfig = DEFAULT_STREAM_CONFIG,
    params: CodecParams = _DEFAULT_PARAMS,
    stamp: Callable[[ArchiveyError], None] | None = None,
) -> BinaryIO:
    """Open a decompressing stream for ``codec`` with exceptions translated/stamped.

    The returned stream wraps the backend so corrupt/truncated/non-seekable errors surface
    as ``ArchiveyError`` subclasses (never raw codec exceptions).
    """
    backend = resolve_codec(codec, config)
    # Opened eagerly (lazy=False), so ArchiveStream.seekable() reflects the real backend
    # stream — no seekable hint needed (that only matters for a lazily-opened stream).
    return ArchiveStream(
        lambda: backend.open(source, params),
        translate=backend.translate,
        stamp=stamp,
        lazy=False,
    )
