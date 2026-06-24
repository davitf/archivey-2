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
import weakref
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
from archivey.internal.streams.decompress import (
    BrotliDecompressorStream,
    ZlibDecompressorStream,
)
from archivey.internal.streams.lzip import LzipDecompressorStream
from archivey.internal.streams.streamtools import (
    ensure_binaryio,
    ensure_bufferedio,
    is_seekable,
)
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
_uncompresspy = _optional("uncompresspy")
_pyppmd = _optional("pyppmd")
_inflate64 = _optional("inflate64")
_rapidgzip = _optional("rapidgzip")
# bzip2 random access is provided by rapidgzip's *bundled* IndexedBzip2File, NOT the separate
# ``indexed_bzip2`` package. Loading both rapidgzip and indexed_bzip2 into one process corrupts
# the heap and aborts on macOS (they statically bundle an overlapping C++ core, whose symbols
# collide under dyld). Routing both gzip and bzip2 through rapidgzip keeps a single accelerator
# library in the process, which is safe on every platform. See docs/known-issues.md.
_rapidgzip_bzip2 = getattr(_rapidgzip, "IndexedBzip2File", None)


class _AcceleratorStream(io.RawIOBase, BinaryIO):
    """Wrap a threaded accelerator (``rapidgzip`` / ``indexed_bzip2``) so its underlying object
    is always *closed* before it is freed.

    The accelerators spawn C++ ``std::thread``s (invisible to Python's ``threading`` module).
    A worker thread still running when the interpreter finalizes aborts the process with
    SIGABRT ("Detected Python finalization from running … thread" → "terminate called").
    Crucially, ``join_threads()`` does **not** stop the thread — only ``close()`` does (the
    libraries' own message says to "close all … objects"). So an object that is merely joined,
    or that is finalized by the garbage collector without being closed — which happens when a
    corrupt/truncated read raises and the exception traceback captures the stream in a reference
    cycle, where finalizer ordering is undefined — still trips the abort.

    A :func:`weakref.finalize` guard closes that window: it ``close()``s the raw object exactly
    once, when this wrapper is collected (cyclically or not) or at interpreter exit, whichever
    comes first, holding a strong reference to the raw object so the close always runs *before*
    that object is freed. ``close()`` on the wrapper simply triggers the same guard early.
    """

    def __init__(self, inner: object) -> None:
        super().__init__()
        self._inner: BinaryIO = ensure_binaryio(inner)
        # The finalize callback must NOT reference self — a bound method would pin the wrapper
        # and defeat GC-time finalization — so it takes the raw inner and lives as a staticmethod.
        self._finalize = weakref.finalize(self, self._close_inner, self._inner)

    @staticmethod
    def _close_inner(inner: BinaryIO) -> None:
        # close() — not join_threads() — stops the C++ worker thread, and must run before the
        # interpreter finalizes or the process aborts. Best-effort; the guard runs it once.
        try:
            inner.close()
        except Exception:  # noqa: BLE001 - best-effort; the object is going away regardless
            pass

    def read(self, n: int = -1, /) -> bytes:
        return self._inner.read(n)

    def readinto(self, b: "WriteableBuffer", /) -> int:
        # ensure_binaryio() guarantees a readinto-capable object (readinto is in the required IO
        # method set, and BinaryIOWrapper implements it) — but typing.BinaryIO does not *declare*
        # readinto, so it is reached via getattr to satisfy the type-checkers without a cast.
        return getattr(self._inner, "readinto")(b)  # noqa: B009

    def seek(self, offset: int, whence: int = io.SEEK_SET, /) -> int:
        return self._inner.seek(offset, whence)

    def tell(self, /) -> int:
        return self._inner.tell()

    def readable(self) -> bool:
        return True

    def writable(self) -> bool:
        return False

    def seekable(self) -> bool:
        return self._inner.seekable()

    def close(self) -> None:
        if self.closed:
            return
        # Trigger the finalize guard (closes the raw object) once; it is then disarmed.
        self._finalize()
        super().close()


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
    UNIX_COMPRESS = "unix_compress"  # LZW (.Z)
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
    if isinstance(e, (ValueError, RuntimeError)) and (
        "gzip/zlib header" in text or "gzip magic" in text
    ):
        # Corrupt header. The type/message varies by platform backend (ISA-L on Linux vs
        # the macOS fallback) and source type: RuntimeError "Failed to parse gzip/zlib
        # header (… Invalid gzip/zlib wrapper)" or ValueError "Failed to read gzip/zlib
        # header (… Invalid gzip magic bytes)". The gzip magic matched at open, so this is
        # corruption.
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
    if "[BZip2 block" in text:
        # Corrupt block data or block header (e.g. "[BZip2 block header] Invalid Huffman
        # coding group count"); surfaced as ValueError or RuntimeError depending on where.
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


def _translate_unix_compress(e: Exception) -> ArchiveyError | None:
    # uncompresspy raises ValueError both for a non-seekable input (it needs random
    # access to decode) and for a corrupt LZW bitstream. The .Z format carries no length
    # or checksum trailer, so truncation is undetectable — a cut stream just yields fewer
    # bytes with no error (there is intentionally no TruncatedError path here).
    if isinstance(e, ValueError):
        if "seekable" in str(e):
            return StreamNotSeekableError("uncompresspy does not support non-seekable streams")
        return CorruptionError(f"Error reading unix-compress (.Z) stream: {e!r}")
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
    """Delegate to a forward-only decoder, warning once on a rewinding seek.

    Several codecs *can* seek but service a backward seek by re-decompressing the stream
    from the start — O(n) per rewind — because they carry no random-access index: gzip/bz2
    (stdlib), brotli, lz4, and zlib. We don't forbid that (a slow seek beats failing, and
    not every format can offer fast random access), but we don't let it pass silently
    either: the first rewinding seek logs a warning. When an accelerator backend exists
    (gzip → ``rapidgzip``, bz2 → ``indexed_bzip2``, both in the ``[seekable]`` extra), the
    warning names it; otherwise it just states the codec re-decompresses from the start.
    Forward seeks (linear decompression) and no-op seeks stay quiet.
    """

    def __init__(
        self, inner: BinaryIO, *, codec_name: str, accelerator: str | None = None
    ) -> None:
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
            if self._accelerator is not None:
                logger.warning(
                    "Seeking backward in a %s stream without a random-access accelerator "
                    "re-decompresses from the start (O(n) per rewind). Install the "
                    "'seekable' extra (%s) for indexed random access.",
                    self._codec_name,
                    self._accelerator,
                )
            else:
                logger.warning(
                    "Seeking backward in a %s stream re-decompresses from the start "
                    "(O(n) per rewind): this codec has no random-access index.",
                    self._codec_name,
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


class _GzipTruncationCheckStream(io.RawIOBase, BinaryIO):
    """Backstop truncation detection for the rapidgzip accelerator.

    rapidgzip surfaces some truncations as exceptions but silently returns short/zero
    output for others (notably a cut that leaves no fully-decodable block — its
    ``EndOfFileReached`` does not always reach Python). On a full sequential read to EOF,
    this compares the total decompressed length (mod 2**32) against the gzip ISIZE trailer;
    a mismatch means truncation — unless the file is multi-member (the trailer is only the
    *last* member's size), which is disambiguated by scanning for a further gzip header.

    Only used for a seekable **path** source, where the trailer and the scan are cheaply
    available via an independent handle; a caller ``seek`` disables the check (the
    sequential byte total is then meaningless, and partial reads are never verified).
    """

    def __init__(self, inner: BinaryIO, source_path: str) -> None:
        super().__init__()
        self._inner = inner
        self._source_path = source_path
        self._total = 0
        self._checked = False
        self._verify = True

    def read(self, size: int = -1, /) -> bytes:
        data = self._inner.read(size)
        if data:
            self._total += len(data)
        elif self._verify and not self._checked:
            self._checked = True
            self._verify_not_truncated()
        return data

    def readinto(self, b: "WriteableBuffer", /) -> int:
        mv = memoryview(b).cast("B")
        data = self.read(len(mv))
        mv[: len(data)] = data
        return len(data)

    def seek(self, offset: int, whence: int = io.SEEK_SET, /) -> int:
        self._verify = False  # random access invalidates the sequential byte total
        return self._inner.seek(offset, whence)

    def _verify_not_truncated(self) -> None:
        try:
            with open(self._source_path, "rb") as f:
                size = f.seek(0, io.SEEK_END)
                if size < 18:  # header(10) + min deflate + trailer(8): too small to trust
                    return
                f.seek(-4, io.SEEK_END)
                isize = int.from_bytes(f.read(4), "little")
        except OSError:
            return
        if self._total % (1 << 32) == isize:
            return
        # Mismatch: truncation, unless this is a concatenated multi-member gzip (then the
        # trailer is only the last member's size). A conservative scan: any further gzip
        # header means "treat as multi-member" and do not raise (a false match only costs a
        # missed truncation, never a false positive on a valid file).
        if self._has_additional_gzip_member():
            return
        raise TruncatedError(
            "gzip stream is truncated: the decompressed size does not match the ISIZE "
            "trailer (the rapidgzip accelerator does not surface this truncation itself)"
        )

    def _has_additional_gzip_member(self) -> bool:
        # Scan in fixed-size blocks (never read the whole file into memory), carrying a small
        # overlap so a header split across a block boundary is still found. Start one byte in so
        # this member's own header at offset 0 is not matched.
        magic = b"\x1f\x8b\x08"
        block = 1 << 20
        try:
            with open(self._source_path, "rb") as f:
                f.seek(1)
                tail = b""
                while True:
                    chunk = f.read(block)
                    if not chunk:
                        return False
                    if magic in tail + chunk:
                        return True
                    tail = chunk[-(len(magic) - 1) :]
        except OSError:
            return True  # cannot rule out a second member -> do not raise

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


def _open_gzip(source: CodecSource, params: CodecParams, config: StreamConfig) -> BinaryIO:
    if config.use_rapidgzip.enabled_for(
        streaming=config.streaming, available=_rapidgzip is not None
    ):
        if _rapidgzip is None:
            raise PackageNotInstalledError(
                "The 'rapidgzip' package is required for gzip random access "
                "(install the 'seekable' extra)."
            )
        stream = _AcceleratorStream(_rapidgzip.open(source, parallelization=0))
        # rapidgzip does not reliably surface truncation; add the ISIZE backstop when we
        # have a seekable path to read the trailer / scan for extra members.
        if isinstance(source, (str, os.PathLike)):
            return _GzipTruncationCheckStream(stream, os.fspath(source))
        return stream
    if isinstance(source, (str, os.PathLike)):
        gz: BinaryIO = ensure_binaryio(gzip.open(source, "rb"))
    else:
        gz = ensure_binaryio(gzip.GzipFile(fileobj=ensure_bufferedio(source), mode="rb"))
    # stdlib gzip can seek, but a rewind re-decompresses from the start; warn rather than
    # degrade silently (the [seekable] rapidgzip accelerator gives real random access).
    return _SlowSeekWarningStream(gz, codec_name="gzip", accelerator="rapidgzip")


def _open_bzip2(source: CodecSource, params: CodecParams, config: StreamConfig) -> BinaryIO:
    if config.use_indexed_bzip2.enabled_for(
        streaming=config.streaming, available=_rapidgzip_bzip2 is not None
    ):
        if _rapidgzip_bzip2 is None:
            raise PackageNotInstalledError(
                "The 'rapidgzip' package is required for bzip2 random access "
                "(install the 'seekable' extra)."
            )
        # rapidgzip's bundled bzip2 decoder, not the separate indexed_bzip2 package (see the
        # _rapidgzip_bzip2 note above): keeps a single accelerator library in the process.
        return _AcceleratorStream(_rapidgzip_bzip2(source, parallelization=0))
    # stdlib bz2 can seek, but a rewind re-decompresses from the start; warn rather than
    # degrade silently (the [seekable] rapidgzip accelerator gives real random access).
    return _SlowSeekWarningStream(
        ensure_binaryio(bz2.open(source, "rb")),
        codec_name="bzip2",
        accelerator="rapidgzip",
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
    # Raw deflate is container-only (ZIP/7z members), never a standalone stream: the
    # container owns member offsets, so it isn't wrapped in the rewind-warning stream the
    # standalone single-file codecs use.
    return ZlibDecompressorStream(source, wbits=-15)


def _open_zlib(source: CodecSource, params: CodecParams, config: StreamConfig) -> BinaryIO:
    # zlib has no random-access index, so a backward seek re-decodes from the start; warn.
    return _SlowSeekWarningStream(
        ZlibDecompressorStream(source, wbits=zlib.MAX_WBITS), codec_name="zlib"
    )


class _ZstdReopenStream(io.RawIOBase, BinaryIO):
    """A zstd decoder that services a backward seek by reopening from the start.

    zstandard's reader raises ``OSError`` on a backward seek. To give zstd the same
    forward-only-but-rewindable behaviour as the other index-less codecs (brotli/lz4/zlib),
    a backward seek closes the reader, rewinds the source, and reopens a fresh decoder, then
    re-decompresses forward to the target. The O(n) rewind cost is surfaced by the
    ``_SlowSeekWarningStream`` the opener wraps around this, so this class stays quiet.
    """

    def __init__(self, source: CodecSource) -> None:
        super().__init__()
        self._source = source
        self._inner = self._open()
        self._size: int | None = None

    def _open(self) -> BinaryIO:
        assert _zstandard is not None
        return _zstandard.open(self._source, "rb")

    def _reopen(self) -> None:
        # Closing the zstandard reader does not close the underlying source, so it can be
        # rewound and reopened. (A path source is simply reopened from scratch.)
        self._inner.close()
        if not isinstance(self._source, (str, os.PathLike)):
            self._source.seek(0)
        self._inner = self._open()

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
        if whence == io.SEEK_SET:
            new_pos = offset
        elif whence == io.SEEK_CUR:
            new_pos = self._inner.tell() + offset
        elif whence == io.SEEK_END:
            # zstandard cannot report the size without decoding; read to the end once and
            # cache it (matching what _compression.DecompressReader does internally).
            if self._size is None:
                while self._inner.read(65536):
                    pass
                self._size = self._inner.tell()
            new_pos = self._size + offset
        else:
            raise ValueError(f"Invalid whence: {whence}")
        try:
            return self._inner.seek(new_pos)
        except OSError as e:
            if "cannot seek zstd decompression stream backwards" in str(e):
                self._reopen()
                return self._inner.seek(new_pos)
            raise

    def tell(self, /) -> int:
        return self._inner.tell()

    def readable(self) -> bool:
        return True

    def writable(self) -> bool:
        return False

    def seekable(self) -> bool:
        # A path can always be reopened; a stream source only if it can be rewound.
        if isinstance(self._source, (str, os.PathLike)):
            return True
        return is_seekable(self._source)

    def close(self) -> None:
        self._inner.close()
        super().close()


def _open_zstd(source: CodecSource, params: CodecParams, config: StreamConfig) -> BinaryIO:
    if _zstandard is None:
        raise PackageNotInstalledError(
            "The 'zstandard' package is required for zstd streams (install the 'zstd' extra)."
        )
    # zstd's reader raises on a backward seek; reopen-from-start gives it the same
    # rewindable forward-only behaviour as the other index-less codecs, and the wrapper
    # warns on the (O(n)) rewind.
    return _SlowSeekWarningStream(_ZstdReopenStream(source), codec_name="zstd")


def _open_lz4(source: CodecSource, params: CodecParams, config: StreamConfig) -> BinaryIO:
    if _lz4_frame is None:
        raise PackageNotInstalledError(
            "The 'lz4' package is required for lz4 streams (install the 'lz4' extra)."
        )
    # lz4's frame reader seeks by re-decompressing from the start; warn on a rewind.
    return _SlowSeekWarningStream(
        ensure_binaryio(_lz4_frame.open(source, "rb")), codec_name="lz4"
    )


def _open_brotli(source: CodecSource, params: CodecParams, config: StreamConfig) -> BinaryIO:
    if _brotli is None:
        raise PackageNotInstalledError(
            "The 'brotli' package is required for Brotli streams (install the '7z' extra)."
        )
    # Brotli has no random-access index, so a backward seek re-decodes from the start; warn.
    return _SlowSeekWarningStream(BrotliDecompressorStream(source), codec_name="brotli")


def _open_unix_compress(
    source: CodecSource, params: CodecParams, config: StreamConfig
) -> BinaryIO:
    if _uncompresspy is None:
        raise PackageNotInstalledError(
            "The 'uncompresspy' package is required for unix-compress (.Z) streams "
            "(install the 'unix-compress' extra)."
        )
    # uncompresspy.LZWFile accepts a path or a (seekable) file object and is itself a
    # RawIOBase, so ensure_binaryio passes it through unchanged.
    src = os.fspath(source) if isinstance(source, (str, os.PathLike)) else source
    return ensure_binaryio(_uncompresspy.LZWFile(src))


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
    Codec.UNIX_COMPRESS: _CodecSpec(_open_unix_compress, _translate_unix_compress),
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
    return _rapidgzip_bzip2 is not None and config.use_indexed_bzip2.enabled_for(
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
