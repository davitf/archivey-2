"""The uniform, pull-based codec layer.

Format backends compose these stream backends instead of importing codec libraries
(the ``compressed-streams`` contract). Adding a standalone codec is "add one
:class:`StreamCodec` subclass" — not "edit the detector, single-file reader, and
registry separately". Instances live in :data:`STREAM_CODECS`.

Three names that are easy to mix up:

- :class:`Codec` — enum id (``Codec.GZIP``, ``Codec.DEFLATE``, …). What callers pass
  to :func:`open_codec_stream` / :func:`resolve_codec`.
- :class:`StreamCodec` — descriptor class per codec: ``open`` / ``translate`` /
  magic / content probe / optional ``requirement``. Looked up via ``STREAM_CODECS``.
- :class:`CodecBackend` — *resolved* open+translator for a given ``StreamConfig``
  (accelerator choice may change the translator). Returned by :func:`resolve_codec`.

AES decrypt lives in ``crypto.py``; digest/length verify in ``verify.py``. Both
compose *around* these codec streams in a pipeline. Seekable decode engines for
raw deflate/Brotli/… live in ``decompressor_stream`` + ``decompress``; XZ/lzip/
``.Z`` have their own modules and are opened from the matching ``StreamCodec``.
"""

from __future__ import annotations

import bz2
import gzip
import importlib
import io
import lzma
import os
import struct
import weakref
import zlib
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from types import ModuleType
from typing import TYPE_CHECKING, BinaryIO, Callable, ClassVar

from archivey.config import RAPIDGZIP_AUTO_MIN_COMPRESSED_SIZE
from archivey.exceptions import (
    ArchiveyError,
    CorruptionError,
    PackageNotInstalledError,
    StreamNotSeekableError,
    TruncatedError,
)
from archivey.internal.config import (
    DEFAULT_STREAM_CONFIG,
    AcceleratorMode,
    StreamConfig,
)
from archivey.internal.streams.archive_stream import (
    ArchiveStream,
    ExceptionTranslator,
    RewindWarning,
)
from archivey.internal.streams.decompress import (
    BrotliDecompressorStream,
    Deflate64DecompressorStream,
    PpmdDecompressorStream,
    ZlibDecompressorStream,
)
from archivey.internal.streams.lzip import LzipDecompressorStream
from archivey.internal.streams.streamtools import (
    DelegatingStream,
    ensure_binaryio,
    ensure_bufferedio,
    fix_stream_start_position,
    source_byte_size,
)
from archivey.internal.streams.unix_compress import UnixCompressDecompressorStream
from archivey.internal.streams.xz import XzDecompressorStream
from archivey.types import (
    ArchiveFormat,
    ArchiveMember,
    ContainerFormat,
    HashAlgorithm,
    MagicSignature,
    MissingComponent,
    StreamFormat,
    crc32_digest,
)

if TYPE_CHECKING:
    from archivey.internal.diagnostics_collector import DiagnosticCollector


# Optional packages: resolved once via importlib (rather than static imports) because
# several of these have no type stubs and are absent in the core-only environment. Absence
# becomes a clear PackageNotInstalledError when the corresponding codec is opened.
def _optional(name: str) -> ModuleType | None:
    try:
        return importlib.import_module(name)
    except (
        ImportError
    ):  # pragma: no cover - the absent path runs in the core-only CI leg
        return None


def _optional_zstd() -> ModuleType | None:
    """Stdlib ``compression.zstd`` (3.14+) or ``backports.zstd`` (older Pythons)."""
    for name in ("compression.zstd", "backports.zstd"):
        try:
            return importlib.import_module(name)
        except ImportError:
            continue
    return None


_zstd = _optional_zstd()
_lz4_frame = _optional("lz4.frame")
_brotli = _optional("brotli")
_pyppmd = _optional("pyppmd")
_inflate64 = _optional("inflate64")
_rapidgzip = _optional("rapidgzip")
# bzip2 random access is provided by rapidgzip's *bundled* IndexedBzip2File, NOT the separate
# ``indexed_bzip2`` package. Loading both rapidgzip and indexed_bzip2 into one process corrupts
# the heap and aborts on macOS (they statically bundle an overlapping C++ core, whose symbols
# collide under dyld). Routing both gzip and bzip2 through rapidgzip keeps a single accelerator
# library in the process, which is safe on every platform. See docs/internal/known-issues.md.
_rapidgzip_bzip2 = getattr(_rapidgzip, "IndexedBzip2File", None)


class _AcceleratorStream(DelegatingStream):
    """Wrap a threaded accelerator (``rapidgzip``) so its underlying object is always *closed*
    before it is freed (read/seek/etc. are inherited delegation; this adds only the guard).

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
    that object is freed. ``close()`` on the wrapper simply triggers the same guard early. This
    guard lives at the codec's object-creation point (not in the outer ``ArchiveStream``) because
    a raw accelerator object can also be produced via ``backend.open()`` with no ``ArchiveStream``
    around it — the guard must attach where the object is born.
    """

    def __init__(self, inner: object) -> None:
        super().__init__(ensure_binaryio(inner))
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

    def close(self) -> None:
        if self.closed:
            return
        # Trigger the finalize guard (closes the raw object) once; it is then disarmed.
        self._finalize()
        super(DelegatingStream, self).close()


CodecSource = str | os.PathLike[str] | BinaryIO


class Codec(Enum):
    """The codecs the stream layer can decompress (the ``compressed-streams`` table).

    Single-file/TAR stream formats and 7z/ZIP folder coders both resolve to these.
    Filter-only entries (Delta, the BCJ family) are not opened standalone — they compose
    into a raw-LZMA filter chain (built by the 7z reader); their LZMA filter ids
    are recorded in :data:`LZMA_FILTER_IDS`.
    """

    STORED = "stored"
    GZIP = "gzip"
    BZIP2 = "bzip2"
    XZ = "xz"
    LZIP = "lzip"
    LZMA_ALONE = "lzma_alone"  # legacy LZMA Alone file format (FORMAT_ALONE)
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


@dataclass(frozen=True)
class CodecParams:
    """Per-open parameters that vary by container/coder.

    - ``filters`` — the ``lzma`` raw filter chain (required for raw LZMA1/LZMA2; this is
      where Delta/BCJ stages and the coder properties enter).
    - ``properties`` — raw coder properties blob (e.g. 7z PPMd var.H parameters).
    - ``ppmd_order`` / ``ppmd_mem_size`` / ``ppmd_restore_method`` — ZIP method-98 PPMd8
      parameters (mutually exclusive with 7z ``properties`` for :class:`PpmdCodec`).
    - ``unpack_size`` — known uncompressed output length (7z folder unpack size). Passed
      to PPMd as ``max_length`` so PPMd7 cannot overshoot without an end mark.
    """

    filters: list[dict] | None = None
    properties: bytes | None = None
    ppmd_order: int | None = None
    ppmd_mem_size: int | None = None
    ppmd_restore_method: int = 0
    unpack_size: int | None = None


_DEFAULT_PARAMS = CodecParams()


# --- accelerator selection -------------------------------------------------------------


def _rapidgzip_enabled(config: StreamConfig, *, available: bool) -> bool:
    """Resolve ``use_rapidgzip`` including the DEFLATE-family AUTO size gate.

    AUTO also requires truncation to be verifiable: a container-declared
    ``expected_decompressed_size``, or (gzip only) a readable ISIZE trailer flagged
    via ``gzip_isize_backstop``. Without one of those, AUTO falls back to the stdlib
    backend that raises ``TruncatedError``. ``ON`` ignores this — the caller asked for
    the accelerator explicitly.
    """
    if config.use_rapidgzip is AcceleratorMode.AUTO:
        if config.expected_decompressed_size is None and not config.gzip_isize_backstop:
            return False
    return config.use_rapidgzip.enabled_for(
        seekable=config.seekable,
        available=available,
        input_size=config.compressed_input_size,
        min_size=RAPIDGZIP_AUTO_MIN_COMPRESSED_SIZE,
    )


def _rapidgzip_rewind_warning(
    codec_name: str, config: StreamConfig
) -> RewindWarning | None:
    """Rewind diagnostic for DEFLATE-family codecs that rapidgzip can accelerate.

    Below the AUTO size threshold the rewind is cheap enough that warning (and
    especially telling the user to install an already-present package) is noise —
    stay quiet. Otherwise name the accelerator and say whether to install it or
    that it was present but not engaged.
    """
    if _deflate_family_uses_accelerator(config):
        return None
    size = config.compressed_input_size
    if (
        config.use_rapidgzip is AcceleratorMode.AUTO
        and size is not None
        and size < RAPIDGZIP_AUTO_MIN_COMPRESSED_SIZE
    ):
        return None
    return RewindWarning(
        codec_name,
        accelerator="rapidgzip",
        suggest_install=_rapidgzip is None,
    )


def _wrap_accelerated_length(stream: BinaryIO, config: StreamConfig) -> BinaryIO:
    """Bound accelerated output to ``expected_decompressed_size`` when known.

    rapidgzip may return a silent short prefix on truncation; ``VerifyingStream``
    turns that into ``TruncatedError`` at close (and caps over-long output).
    """
    size = config.expected_decompressed_size
    if size is None:
        return stream
    from archivey.internal.streams.verify import VerifyingStream

    return VerifyingStream(stream, {}, expected_size=size)


def _gzip_isize_from_source(source: CodecSource) -> int | None:
    """Read the gzip ISIZE trailer (uncompressed size mod 2**32) when cheaply available.

    Returns ``None`` for non-seekable sources, short files, or I/O errors. Multi-member
    gzip's trailer is only the *last* member's size — callers that need a hard bound
    should still prefer a container-declared size when available.
    """
    try:
        if isinstance(source, (str, os.PathLike)):
            with open(os.fspath(source), "rb") as f:
                size = f.seek(0, io.SEEK_END)
                if size < 18:
                    return None
                f.seek(-4, io.SEEK_END)
                return int.from_bytes(f.read(4), "little")
        seek = getattr(source, "seek", None)
        tell = getattr(source, "tell", None)
        read = getattr(source, "read", None)
        seekable = getattr(source, "seekable", None)
        if seek is None or tell is None or read is None:
            return None
        if seekable is not None and not seekable():
            return None
        pos = tell()
        try:
            end = seek(0, io.SEEK_END)
            if end < 18:
                return None
            seek(-4, io.SEEK_END)
            return int.from_bytes(read(4), "little")
        finally:
            seek(pos)
    except (OSError, io.UnsupportedOperation, ValueError, TypeError):
        return None


def _config_with_gzip_isize(source: CodecSource, config: StreamConfig) -> StreamConfig:
    """Mark gzip ISIZE as available for AUTO / the ISIZE truncation backstop.

    Does **not** set ``expected_decompressed_size`` from ISIZE: that field is an exact
    bound for ``VerifyingStream``, but ISIZE is mod 2**32 and multi-member gzip's
    trailer covers only the last member.
    """
    if config.gzip_isize_backstop or config.expected_decompressed_size is not None:
        return config
    if _gzip_isize_from_source(source) is None:
        return config
    return replace(config, gzip_isize_backstop=True)


def _deflate_family_uses_accelerator(config: StreamConfig) -> bool:
    """Whether gzip/zlib/deflate will open through rapidgzip for this config."""
    return _rapidgzip is not None and _rapidgzip_enabled(config, available=True)


def _bzip2_uses_accelerator(config: StreamConfig) -> bool:
    return _rapidgzip_bzip2 is not None and config.use_indexed_bzip2.enabled_for(
        seekable=config.seekable, available=True
    )


def _open_rapidgzip(source: CodecSource) -> BinaryIO:
    """Open ``source`` through rapidgzip with the close-on-finalize guard."""
    assert _rapidgzip is not None
    return _AcceleratorStream(_rapidgzip.open(source, parallelization=0))


def _translate_rapidgzip(exc: Exception, label: str) -> ArchiveyError | None:
    """Map rapidgzip exceptions for a DEFLATE-family codec (gzip / zlib / deflate)."""
    text = str(exc)
    if isinstance(exc, ValueError) and "Mismatching CRC32" in text:
        return CorruptionError(f"Error reading {label} stream (rapidgzip): {exc!r}")
    if isinstance(exc, RuntimeError) and "IsalInflateWrapper" in text:
        return CorruptionError(f"Error reading {label} stream (rapidgzip): {exc!r}")
    if isinstance(exc, ValueError) and (
        "deflate block" in text or "Huffman coding is not optimal" in text
    ):
        # Corrupt deflate body / block header. Message varies by platform backend:
        # - Linux ISA-L: RuntimeError via IsalInflateWrapper (above)
        # - non-ISA-L (macOS): ValueError "Failed to decode deflate block …" or
        #   "Failed to read deflate block header … The Huffman coding is not optimal!"
        return CorruptionError(f"Error reading {label} stream (rapidgzip): {exc!r}")
    if isinstance(exc, RuntimeError) and "Invalid deflate block" in text:
        # Raw DEFLATE / zlib body corruption (or an over-long unbounded slice that
        # rapidgzip mistook for a concatenated member).
        return CorruptionError(f"Error reading {label} stream (rapidgzip): {exc!r}")
    if isinstance(exc, (ValueError, RuntimeError)) and (
        "gzip/zlib header" in text or "gzip magic" in text or "zlib header" in text
    ):
        return CorruptionError(f"Error reading {label} stream (rapidgzip): {exc!r}")
    if isinstance(exc, ValueError) and "Failed to detect a valid file format" in text:
        return CorruptionError(f"Error reading {label} stream (rapidgzip): {exc!r}")
    if isinstance(exc, (ValueError, RuntimeError)) and (
        "End of file encountered" in text or "Unexpected end of file" in text
    ):
        return TruncatedError(f"{label} stream is truncated (rapidgzip): {exc!r}")
    if isinstance(exc, ValueError) and "has no valid fileno" in text:
        return StreamNotSeekableError("rapidgzip does not support non-seekable streams")
    if isinstance(exc, io.UnsupportedOperation) and "seek" in text:
        return StreamNotSeekableError("rapidgzip does not support non-seekable streams")
    if isinstance(exc, RuntimeError) and "std::exception" in text:
        return CorruptionError(f"Error reading {label} stream (rapidgzip): {exc!r}")
    return None


# --- shared stream wrappers ------------------------------------------------------------
# These wrap a codec's raw decoder; they are cross-codec helpers (or, for the gzip-only
# truncation backstop, a stream class kept beside its peers), so they stay module-level
# rather than nested in a single codec class.


class _GzipTruncationCheckStream(DelegatingStream):
    """Backstop truncation detection for the rapidgzip accelerator (path sources).

    Upstream rapidgzip treats many incomplete streams as soft EOF (by design): ``read()``
    may return empty or a short/full prefix with no exception. This wrapper:

    1. On EOF with **zero** bytes delivered — fall back to stdlib ``gzip`` sized-reads
       *before* returning empty success, so truncation is loud and any recoverable
       prefix is streamed (valid empty gzip still succeeds with zero bytes).
    2. On EOF after **non-empty** delivery — compare decompressed length (mod 2**32) to
       the gzip ISIZE trailer (single-member). Multi-member files keep the conservative
       “further ``1f 8b 08`` ⇒ do not raise” rule (per-member ISIZE sum is deferred).

    A caller ``seek`` off the sequential frontier disarms both checks.
    """

    _STDLIB_READ_SIZE = 1  # see _fill_stdlib_buf — large GzipFile reads can drop a tail

    def __init__(self, inner: BinaryIO, source_path: str) -> None:
        # readinto_passthrough=False routes readinto through this class's read(), so the
        # byte-total tracking and the EOF truncation check still run on readinto-driven reads.
        super().__init__(inner, readinto_passthrough=False)
        self._source_path = source_path
        self._total = 0
        self._checked = False
        self._verify = True
        self._stdlib: BinaryIO | None = None
        self._stdlib_buf = bytearray()
        self._pending_truncated: TruncatedError | None = None

    def read(self, size: int = -1, /) -> bytes:
        if size == 0:
            return b""  # an explicit read(0) is not EOF; it must not trip the check
        if self._stdlib is not None or self._stdlib_buf or self._pending_truncated:
            return self._read_stdlib(size)
        data = self._inner.read(size)
        if data:
            self._total += len(data)
            return data
        if self._verify and not self._checked:
            self._checked = True
            if self._total == 0:
                return self._begin_stdlib_fallback(size)
            self._verify_not_truncated()
        return data

    def seek(self, offset: int, whence: int = io.SEEK_SET, /) -> int:
        result = super().seek(offset, whence)
        # Random access invalidates the sequential byte total — but only when the seek
        # actually moved off the sequential frontier. A no-op seek (tell()-style
        # seek(0, SEEK_CUR), or a seek to the current position) keeps the check armed.
        if result != self._total:
            self._verify = False
            self._abandon_stdlib_fallback()
        return result

    def close(self) -> None:
        pending = self._pending_truncated
        self._pending_truncated = None
        self._abandon_stdlib_fallback()
        super().close()
        if pending is not None:
            raise pending

    def _abandon_stdlib_fallback(self) -> None:
        self._stdlib_buf.clear()
        stdlib = self._stdlib
        self._stdlib = None
        if stdlib is not None:
            try:
                stdlib.close()
            except Exception:  # noqa: BLE001 - best-effort; wrapper is closing/switching
                pass

    def _begin_stdlib_fallback(self, size: int) -> bytes:
        """Re-open via stdlib after rapidgzip silently returned empty at EOF."""
        self._stdlib = ensure_binaryio(gzip.open(self._source_path, "rb"))
        return self._read_stdlib(size)

    def _read_stdlib(self, size: int) -> bytes:
        if self._pending_truncated is not None and not self._stdlib_buf:
            err = self._pending_truncated
            self._pending_truncated = None
            raise err
        self._fill_stdlib_buf(size)
        if size < 0:
            out = bytes(self._stdlib_buf)
            self._stdlib_buf.clear()
        else:
            take = min(size, len(self._stdlib_buf))
            out = bytes(self._stdlib_buf[:take])
            del self._stdlib_buf[:take]
        if not out and self._pending_truncated is not None:
            err = self._pending_truncated
            self._pending_truncated = None
            raise err
        return out

    def _fill_stdlib_buf(self, size: int) -> None:
        if self._stdlib is None:
            return
        # Already have enough for a sized read.
        if size >= 0 and len(self._stdlib_buf) >= size:
            return
        try:
            # Use small reads: GzipFile.read(large) can raise EOFError without
            # returning the last buffered decompressed bytes (sized-read probe).
            while size < 0 or len(self._stdlib_buf) < size:
                chunk = self._stdlib.read(self._STDLIB_READ_SIZE)
                if not chunk:
                    break
                self._stdlib_buf.extend(chunk)
                self._total += len(chunk)
        except EOFError as exc:
            self._pending_truncated = TruncatedError(
                "gzip stream is truncated (stdlib fallback after rapidgzip returned "
                f"empty without error): {exc!r}"
            )
            try:
                self._stdlib.close()
            except Exception:  # noqa: BLE001 - best-effort after truncation
                pass
            self._stdlib = None

    def _verify_not_truncated(self) -> None:
        try:
            with open(self._source_path, "rb") as f:
                size = f.seek(0, io.SEEK_END)
                if size < 18:
                    # Incomplete member (header-only / truncated before a full trailer).
                    # Empty delivery is handled by the stdlib fallback; non-empty soft EOF
                    # with a file this short is still truncation.
                    raise TruncatedError(
                        "gzip stream is truncated: compressed size is too small for a "
                        "complete gzip member (the rapidgzip accelerator did not raise)"
                    )
                f.seek(-4, io.SEEK_END)
                isize = int.from_bytes(f.read(4), "little")
        except TruncatedError:
            raise
        except OSError:
            return
        if self._total % (1 << 32) == isize:
            return
        # Mismatch: truncation, unless this is a concatenated multi-member gzip (then the
        # trailer is only the last member's size). Conservative scan: any further gzip
        # header ⇒ do not raise (false-negative only; per-member ISIZE sum is deferred).
        if self._has_additional_gzip_member():
            return
        raise TruncatedError(
            "gzip stream is truncated: the decompressed size does not match the ISIZE "
            "trailer (the rapidgzip accelerator does not surface this truncation itself)"
        )

    def _has_additional_gzip_member(self) -> bool:
        try:
            with open(self._source_path, "rb") as f:
                return gzip_has_additional_member(f)
        except OSError:
            return True  # cannot rule out a second member -> do not raise


def gzip_has_additional_member(stream: BinaryIO) -> bool:
    """Whether ``stream`` contains a gzip member after the one starting at offset 0.

    Scans in fixed-size blocks (never reads the whole file into memory), carrying a small
    overlap so a header split across a block boundary is still found. Starts one byte in so
    this member's own header at offset 0 is not matched.

    **Side effect:** seeks the stream (starts at offset 1, then reads forward). Callers that
    need the prior position MUST restore it themselves (e.g. ``tell``/``seek`` around the
    call). Path-source callers typically open a fresh handle owned only for this scan.
    """
    magic = b"\x1f\x8b\x08"
    block = 1 << 20
    stream.seek(1)
    tail = b""
    while True:
        chunk = stream.read(block)
        if not chunk:
            return False
        if magic in tail + chunk:
            return True
        tail = chunk[-(len(magic) - 1) :]


# --- single-file metadata + content probes ---------------------------------------------

# How many gzip header bytes to peek for cheap metadata (FNAME/mtime). Longer stored names
# beyond this are simply not surfaced.
_GZIP_HEADER_PEEK = 512
_ALONE_HEADER_SIZE = 13
# Alone header marks unknown uncompressed size with all-ones uint64.
_ALONE_UNKNOWN_SIZE = (1 << 64) - 1

# Bytes fed to a content probe — enough to trip a malformed-stream error without
# decompressing the whole payload.
_PROBE_PREFIX = 256

# zlib's 2-byte CMF/FLG header is not a true magic (the same prefix begins many raw-deflate
# streams and can occur in arbitrary data), so the probe uses it only as a cheap fail-fast
# gate before attempting the decode that actually confirms a zlib stream.
_ZLIB_HEADERS = (b"\x78\x01", b"\x78\x5e", b"\x78\x9c", b"\x78\xda")


@dataclass(frozen=True)
class MetadataContext:
    """The reader-side hooks a codec's metadata extractor may call.

    Lets a codec's ``extract_metadata`` read what it needs from the source without the codec
    layer depending on the single-file reader. ``peek_header(n)`` returns the leading ``n``
    bytes of the compressed source without consuming it; ``peek_trailer(n)`` returns the
    trailing ``n`` bytes when the source is seekable/path (else ``None``);
    ``probe_decompressed_size()`` returns the decompressed size from the stream
    index/trailer when cheaply available (else ``None``); ``probe_gzip_stored_crc32()``
    returns the single-member gzip trailer CRC when that is cheaply knowable (else
    ``None``), in one seekable pass; ``probe_lzip_index()`` returns
    ``(decompressed_size, combined_crc32)`` from one seekable lzip index scan when
    available (else ``None``).
    """

    peek_header: Callable[[int], bytes]
    peek_trailer: Callable[[int], bytes | None]
    probe_decompressed_size: Callable[[], int | None]
    probe_gzip_stored_crc32: Callable[[], int | None]
    probe_lzip_index: Callable[[], tuple[int, int] | None]


# --- the codec descriptors -------------------------------------------------------------


class StreamCodec:
    """One single-stream codec: its behavior, detection signals, and requirement.

    Subclasses override the behavior methods (:meth:`open`, :meth:`translate`, optionally
    :meth:`translator` / :meth:`extract_metadata` / :meth:`content_probe`) and declare the
    detection data as class attributes (``stream_format`` / ``magic``) plus an
    optional-dependency ``requirement``. The standalone single-file ``ArchiveFormat`` and its
    file extension are *derived* from ``stream_format`` (see the properties below). Instances
    are collected in :data:`STREAM_CODECS`, which the detector, the single-file reader, and
    the registry read directly — so a new standalone codec is a single subclass, with no edits
    to those consumers (see ``compressed-streams``). Container-only / filter-only codecs
    override just ``open`` + ``translate``.
    """

    codec: ClassVar[Codec]
    # The single-file/TAR StreamFormat this codec decodes, when it is a stream format at all
    # (raw container coders such as DEFLATE/LZMA have none). This drives the derived
    # single-file format + extension below.
    stream_format: ClassVar[StreamFormat | None] = None
    # Exact magic signals for the standalone format, aggregated by the detector.
    magic: ClassVar[tuple[MagicSignature, ...]] = ()
    # The optional-dependency requirement (package / extra / hint + unlocked capability);
    # ``None`` for codecs served by the stdlib, which are always available.
    requirement: ClassVar[MissingComponent | None] = None

    # --- derived single-file identity ---

    @property
    def single_file_format(self) -> ArchiveFormat | None:
        """The standalone single-file ``ArchiveFormat`` (``RAW_STREAM`` + ``stream_format``).

        ``None`` for a container-only codec (no ``stream_format``) and for ``STORED`` (a bare
        uncompressed stream is not a standalone single-file format).
        """
        sf = self.stream_format
        if sf is None or sf is StreamFormat.UNCOMPRESSED:
            return None
        return ArchiveFormat(ContainerFormat.RAW_STREAM, sf)

    @property
    def extensions(self) -> tuple[str, ...]:
        """Standalone file extension(s), derived from the format (e.g. ``GZIP`` → ``.gz``).

        One canonical extension per codec, taken from ``ArchiveFormat.file_extension()``.
        Extension *aliases* (e.g. ``.zstd``) are intentionally not a per-codec concern; they
        belong in a format-level alias map if/when they are needed.
        """
        fmt = self.single_file_format
        return (f".{fmt.file_extension()}",) if fmt is not None else ()

    # --- behavior (overridden by subclasses) ---

    def open(
        self, source: CodecSource, params: CodecParams, config: StreamConfig
    ) -> BinaryIO:
        raise NotImplementedError

    def translate(self, exc: Exception) -> ArchiveyError | None:
        """Map a raw decoder exception to an ``ArchiveyError`` subclass, or ``None``."""
        return None

    def translator(self, config: StreamConfig) -> ExceptionTranslator:
        """The translator matching the backend chosen for ``config``.

        Default is the static :meth:`translate`; codecs whose backend varies by config (the
        gzip/bzip2 accelerators have a different exception taxonomy) override this.
        """
        return self.translate

    def extract_metadata(self, ctx: MetadataContext, member: ArchiveMember) -> None:
        """Fill ``ArchiveMember`` fields from the source. Default: no extra metadata."""
        return

    def content_probe(self, prefix: bytes) -> bool:
        """Whether ``prefix`` is recognized as this codec's stream.

        Default: this codec has no content probe (it is identified by exact magic). Codecs
        without a usable magic (Brotli; zlib's too-unspecific header) override this.
        """
        return False

    def rewind_warning(self, config: StreamConfig) -> RewindWarning | None:
        """A :class:`RewindWarning` when a backward seek re-decompresses from the start, else None.

        Default ``None`` (the codec has a native random-access index, or none is needed). Codecs
        whose rewind is O(n) override this; gzip/bzip2 return ``None`` when their accelerator is
        active. The outer ``ArchiveStream`` carries this and warns once on the first rewind.
        """
        return None

    # --- availability ---

    @property
    def available(self) -> bool:
        """Whether this codec's decompression backend is importable right now."""
        return self.requirement is None or self._backend_present()

    def _backend_present(self) -> bool:
        """Whether the optional backing package is importable (optional codecs override)."""
        return True

    @property
    def probes_content(self) -> bool:
        """Whether this codec overrides the no-op base content probe (the detector uses it)."""
        return type(self).content_probe is not StreamCodec.content_probe

    # --- shared probe primitive ---

    def _decodes_sample(self, prefix: bytes) -> bool:
        """Whether a bounded ``prefix`` decodes cleanly through this codec (the probe primitive).

        A valid stream decodes some output (or runs out of the bounded prefix →
        ``TruncatedError``), while non-matching data raises a corruption error. Returns
        ``False`` when the backend is absent, so detection falls through to the extension
        guess. Operates on already-peeked bytes, so it consumes nothing from the source.
        """
        if not self.available:
            return False
        try:
            with open_codec_stream(
                self.codec, io.BytesIO(prefix[:_PROBE_PREFIX])
            ) as stream:
                stream.read(_PROBE_PREFIX)
            return True
        except TruncatedError:
            return True  # decoded fine, just ran out of the bounded prefix
        except ArchiveyError:
            return False


class StoredCodec(StreamCodec):
    codec = Codec.STORED
    stream_format = StreamFormat.UNCOMPRESSED

    def open(
        self, source: CodecSource, params: CodecParams, config: StreamConfig
    ) -> BinaryIO:
        if isinstance(source, (str, os.PathLike)):
            return open(os.fspath(source), "rb")
        return ensure_binaryio(source)


class GzipCodec(StreamCodec):
    codec = Codec.GZIP
    stream_format = StreamFormat.GZIP
    magic = (MagicSignature(0, b"\x1f\x8b", ArchiveFormat.GZ),)

    def open(
        self, source: CodecSource, params: CodecParams, config: StreamConfig
    ) -> BinaryIO:
        # Prefer a container-declared size; otherwise note a readable ISIZE so AUTO can
        # still select rapidgzip with the dedicated ISIZE backstop (not VerifyingStream).
        config = _config_with_gzip_isize(source, config)
        if _rapidgzip_enabled(config, available=_rapidgzip is not None):
            if _rapidgzip is None:
                raise PackageNotInstalledError(
                    "The 'rapidgzip' package is required for gzip random access "
                    "(install the 'seekable' extra)."
                )
            stream = _open_rapidgzip(source)
            if config.expected_decompressed_size is not None:
                return _wrap_accelerated_length(stream, config)
            # Path-source truncation backstop: empty→stdlib fallback + single-member ISIZE
            # (multi-member keeps conservative further-magic bailout; sum deferred).
            if isinstance(source, (str, os.PathLike)):
                return _GzipTruncationCheckStream(stream, os.fspath(source))
            return stream
        # stdlib gzip can seek, but a rewind re-decompresses from the start; the outer
        # ArchiveStream warns about that (see rewind_warning). The [seekable] rapidgzip
        # accelerator (above) gives real random access.
        if isinstance(source, (str, os.PathLike)):
            return ensure_binaryio(gzip.open(source, "rb"))
        return ensure_binaryio(
            gzip.GzipFile(fileobj=ensure_bufferedio(source), mode="rb")
        )

    def translate(self, exc: Exception) -> ArchiveyError | None:
        if isinstance(exc, gzip.BadGzipFile):
            return CorruptionError(f"Error reading gzip stream: {exc!r}")
        if isinstance(exc, zlib.error):
            # Corruption inside the deflate body (a valid gzip header, then bad data) is
            # raised by stdlib gzip as a raw zlib.error rather than BadGzipFile. zlib does
            # not flag truncation distinctly here (a short stream surfaces as EOFError
            # below), so any zlib.error at this point is corruption.
            return CorruptionError(f"Error reading gzip stream: {exc!r}")
        if isinstance(exc, EOFError):
            return TruncatedError(f"gzip stream is truncated: {exc!r}")
        return None

    def translator(self, config: StreamConfig) -> ExceptionTranslator:
        if _deflate_family_uses_accelerator(config):
            return self._translate_accelerator
        return self.translate

    def rewind_warning(self, config: StreamConfig) -> RewindWarning | None:
        # The accelerator gives indexed random access; only the stdlib fallback rewinds slowly.
        return _rapidgzip_rewind_warning("gzip", config)

    def _translate_accelerator(self, exc: Exception) -> ArchiveyError | None:
        """Translate the rapidgzip accelerator's exceptions to the library's error types."""
        return _translate_rapidgzip(exc, "gzip")

    def extract_metadata(self, ctx: MetadataContext, member: ArchiveMember) -> None:
        """Surface gzip's stored filename (FNAME), mtime, and trailer CRC when cheap.

        RFC 1952 specifies the FNAME field as ISO-8859-1 (Latin-1), so the decoded value in
        ``extra`` uses that encoding; ``raw_name`` keeps the verbatim stored bytes.

        The 8-byte trailer CRC-32 is surfaced as ``member.hashes[HashAlgorithm.CRC32]``
        only when the header is a valid gzip magic *and* the stream is a single member on a
        seekable/path source (multi-member trailers cover only the last member). Never
        triggers a decompression pass.
        """
        header = ctx.peek_header(_GZIP_HEADER_PEEK)
        if len(header) < 10 or header[:2] != b"\x1f\x8b":
            return
        flg = header[3]
        mtime = int.from_bytes(header[4:8], "little")
        if mtime != 0:
            member.modified = datetime.fromtimestamp(mtime, tz=timezone.utc)

        pos = 10
        if flg & 0x04:  # FEXTRA: 2-byte length + data
            if pos + 2 <= len(header):
                xlen = int.from_bytes(header[pos : pos + 2], "little")
                pos += 2 + xlen
            else:
                pos = len(header) + 1  # stop optional-field walk
        if flg & 0x08 and pos <= len(header):
            # FNAME: null-terminated stored filename (Latin-1 per RFC 1952)
            end = header.find(b"\x00", pos)
            if end != -1:
                name_bytes = header[pos:end]
                member.raw_name = name_bytes
                member.extra["gzip.original_filename"] = name_bytes.decode("latin-1")

        crc32 = ctx.probe_gzip_stored_crc32()
        if crc32 is not None:
            hashes = dict(member.hashes)
            hashes[HashAlgorithm.CRC32] = crc32_digest(crc32)
            member.hashes = hashes


class Bzip2Codec(StreamCodec):
    codec = Codec.BZIP2
    stream_format = StreamFormat.BZIP2
    magic = (MagicSignature(0, b"BZh", ArchiveFormat.BZ2),)

    def open(
        self, source: CodecSource, params: CodecParams, config: StreamConfig
    ) -> BinaryIO:
        if config.use_indexed_bzip2.enabled_for(
            seekable=config.seekable, available=_rapidgzip_bzip2 is not None
        ):
            if _rapidgzip_bzip2 is None:
                raise PackageNotInstalledError(
                    "The 'rapidgzip' package is required for bzip2 random access "
                    "(install the 'seekable' extra)."
                )
            # rapidgzip's bundled bzip2 decoder, not the separate indexed_bzip2 package (see the
            # _rapidgzip_bzip2 note above): keeps a single accelerator library in the process.
            return _AcceleratorStream(_rapidgzip_bzip2(source, parallelization=0))
        # stdlib bz2 can seek, but a rewind re-decompresses from the start; the outer
        # ArchiveStream warns about that (see rewind_warning). The [seekable] accelerator
        # (above) gives real random access.
        return ensure_binaryio(bz2.open(source, "rb"))

    def translate(self, exc: Exception) -> ArchiveyError | None:
        if isinstance(exc, OSError) and "Invalid data stream" in str(exc):
            return CorruptionError(f"bzip2 stream is corrupt: {exc!r}")
        if isinstance(exc, (EOFError, ValueError)):
            return TruncatedError(f"bzip2 stream is truncated: {exc!r}")
        return None

    def translator(self, config: StreamConfig) -> ExceptionTranslator:
        if _bzip2_uses_accelerator(config):
            return self._translate_accelerator
        return self.translate

    def rewind_warning(self, config: StreamConfig) -> RewindWarning | None:
        if _bzip2_uses_accelerator(config):
            return None
        return RewindWarning(
            "bzip2",
            accelerator="rapidgzip",
            suggest_install=_rapidgzip_bzip2 is None,
        )

    def _translate_accelerator(self, exc: Exception) -> ArchiveyError | None:
        """Translate the indexed_bzip2 accelerator's exceptions to the library's error types."""
        text = str(exc)
        if isinstance(exc, RuntimeError) and "Calculated CRC" in text:
            return CorruptionError(
                f"Error reading bzip2 stream (indexed_bzip2): {exc!r}"
            )
        if isinstance(exc, RuntimeError) and text in (
            "std::exception",
            "Unknown exception",
        ):
            return CorruptionError(
                f"Error reading bzip2 stream (indexed_bzip2): {exc!r}"
            )
        if "[BZip2 block" in text:
            # Corrupt block data or block header (e.g. "[BZip2 block header] Invalid Huffman
            # coding group count"); surfaced as ValueError or RuntimeError depending on where.
            return CorruptionError(
                f"Error reading bzip2 stream (indexed_bzip2): {exc!r}"
            )
        if isinstance(exc, (ValueError, RuntimeError)) and (
            "Huffman" in text
            or "magic" in text  # "Input header is not BZip2 magic string 'BZh'…"
            or "bit string" in text
            or "bad optional access" in text  # accelerator read past a corrupt block
        ):
            # Corrupt Huffman tables, stream/block magic, or internal state, outside a
            # "[BZip2 block]"-tagged context (e.g. "Constructing a Huffman coding … failed!"
            # or "bad optional access") — all found by the corpus mutation harness.
            return CorruptionError(
                f"Error reading bzip2 stream (indexed_bzip2): {exc!r}"
            )
        if isinstance(exc, ValueError) and "has no valid fileno" in text:
            return StreamNotSeekableError(
                "indexed_bzip2 does not support non-seekable streams"
            )
        if isinstance(exc, io.UnsupportedOperation) and "seek" in text:
            return StreamNotSeekableError(
                "indexed_bzip2 does not support non-seekable streams"
            )
        return None


class _LzmaErrorCodec(StreamCodec):
    """Shared LZMA/XZ error taxonomy for the lzma-family codecs (xz, lzip, raw LZMA)."""

    def translate(self, exc: Exception) -> ArchiveyError | None:
        if isinstance(exc, lzma.LZMAError):
            return CorruptionError(f"Error reading LZMA/XZ stream: {exc!r}")
        if isinstance(exc, EOFError):
            return TruncatedError(f"LZMA/XZ stream is truncated: {exc!r}")
        return None


class _SizedLzmaCodec(_LzmaErrorCodec):
    """xz / lzip: surface the decompressed size recorded in the stream index/trailer."""

    def extract_metadata(self, ctx: MetadataContext, member: ArchiveMember) -> None:
        member.size = ctx.probe_decompressed_size()


class XzCodec(_SizedLzmaCodec):
    codec = Codec.XZ
    stream_format = StreamFormat.XZ
    magic = (MagicSignature(0, b"\xfd7zXZ\x00", ArchiveFormat.XZ),)

    def open(
        self, source: CodecSource, params: CodecParams, config: StreamConfig
    ) -> BinaryIO:
        return XzDecompressorStream(source, seekable=config.seekable)


class LzipCodec(_SizedLzmaCodec):
    codec = Codec.LZIP
    stream_format = StreamFormat.LZIP
    magic = (MagicSignature(0, b"LZIP", ArchiveFormat.LZIP),)

    def open(
        self, source: CodecSource, params: CodecParams, config: StreamConfig
    ) -> BinaryIO:
        return LzipDecompressorStream(source, seekable=config.seekable)

    def extract_metadata(self, ctx: MetadataContext, member: ArchiveMember) -> None:
        """Surface decompressed size and whole-member CRC-32 from one seekable index scan.

        ``probe_lzip_index`` returns both values from a single backward trailer walk.
        The CRC is the combine of every per-member trailer CRC-32 with that member's
        uncompressed ``data_size`` (single-member degenerates to the trailer CRC).
        """
        summary = ctx.probe_lzip_index()
        if summary is None:
            return
        member.size, crc32 = summary
        hashes = dict(member.hashes)
        hashes[HashAlgorithm.CRC32] = crc32_digest(crc32)
        member.hashes = hashes


def _alone_props_plausible(props: int) -> bool:
    """Whether ``props`` encodes a valid Alone ``(lc, lp, pb)`` triple."""
    # props = (pb * 5 + lp) * 9 + lc with lc∈[0,8], lp∈[0,4], pb∈[0,4]
    if props > (4 * 5 + 4) * 9 + 8:
        return False
    lc = props % 9
    rest = props // 9
    lp = rest % 5
    pb = rest // 5
    return lc <= 8 and lp <= 4 and pb <= 4


def _alone_header_plausible(prefix: bytes) -> bool:
    """Cheap Alone header gate before a decode probe (rejects zero-filled prefixes)."""
    if len(prefix) < _ALONE_HEADER_SIZE or not _alone_props_plausible(prefix[0]):
        return False
    dict_size = int.from_bytes(prefix[1:5], "little")
    # Real Alone encoders never write dictionary size 0; rejecting it also keeps the
    # zero-filled ISO system area (and similar padding) from decoding as an empty Alone
    # stream before far-magic ISO detection runs.
    if dict_size == 0:
        return False
    return True


class LzmaAloneCodec(_LzmaErrorCodec):
    """Legacy LZMA Alone (``.lzma``) — framed standalone stream, not raw FORMAT_RAW."""

    codec = Codec.LZMA_ALONE
    stream_format = StreamFormat.LZMA_ALONE
    # No exact magic: the properties byte is too weak; recognition is by content probe.

    def open(
        self, source: CodecSource, params: CodecParams, config: StreamConfig
    ) -> BinaryIO:
        # stdlib LZMAFile seeks by re-decompressing from the start; the outer ArchiveStream
        # warns on rewind (see rewind_warning).
        return ensure_binaryio(
            lzma.LZMAFile(source, mode="rb", format=lzma.FORMAT_ALONE)
        )

    def rewind_warning(self, config: StreamConfig) -> RewindWarning | None:
        return RewindWarning("lzma")

    def extract_metadata(self, ctx: MetadataContext, member: ArchiveMember) -> None:
        header = ctx.peek_header(_ALONE_HEADER_SIZE)
        if len(header) < _ALONE_HEADER_SIZE:
            return
        size = int.from_bytes(header[5:13], "little")
        if size != _ALONE_UNKNOWN_SIZE:
            member.size = size

    def content_probe(self, prefix: bytes) -> bool:
        """Recognize LZMA Alone: plausible 13-byte header that then yields decode output."""
        if not _alone_header_plausible(prefix) or not self.available:
            return False
        try:
            with open_codec_stream(
                self.codec, io.BytesIO(prefix[:_PROBE_PREFIX])
            ) as stream:
                out = stream.read(_PROBE_PREFIX)
            # An empty successful read (e.g. usize=0) is not a positive Alone claim.
            return len(out) > 0
        except TruncatedError:
            return True  # started decoding, ran out of the bounded prefix
        except ArchiveyError:
            return False


class _RawLzmaCodec(_LzmaErrorCodec):
    """Raw LZMA1/LZMA2 (FORMAT_RAW + properties); container-only (no standalone stream)."""

    def open(
        self, source: CodecSource, params: CodecParams, config: StreamConfig
    ) -> BinaryIO:
        if params.filters is None:
            raise ValueError(
                "raw LZMA decoding requires filter properties (CodecParams.filters)"
            )
        return ensure_binaryio(
            lzma.LZMAFile(
                source, mode="rb", format=lzma.FORMAT_RAW, filters=params.filters
            )
        )


class LzmaCodec(_RawLzmaCodec):
    codec = Codec.LZMA


class Lzma2Codec(_RawLzmaCodec):
    codec = Codec.LZMA2


class _ZlibErrorCodec(StreamCodec):
    """Shared zlib/deflate error taxonomy for raw deflate and zlib-wrapped deflate."""

    def translate(self, exc: Exception) -> ArchiveyError | None:
        if isinstance(exc, zlib.error):
            text = str(exc)
            if "incomplete" in text or "truncated" in text:
                return TruncatedError(f"deflate stream is truncated: {exc!r}")
            return CorruptionError(f"Error reading deflate stream: {exc!r}")
        if isinstance(exc, EOFError):
            return TruncatedError(f"deflate stream is truncated: {exc!r}")
        return None


class DeflateCodec(_ZlibErrorCodec):
    codec = Codec.DEFLATE

    def open(
        self, source: CodecSource, params: CodecParams, config: StreamConfig
    ) -> BinaryIO:
        if _rapidgzip_enabled(config, available=_rapidgzip is not None):
            if _rapidgzip is None:
                raise PackageNotInstalledError(
                    "The 'rapidgzip' package is required for deflate random access "
                    "(install the 'seekable' extra)."
                )
            # rapidgzip auto-detects raw DEFLATE; pass the source unwrapped. Callers MUST
            # bound the input (container SlicingStream / exact file) — rapidgzip over-reads
            # past EOS looking for a concatenated member.
            return _wrap_accelerated_length(_open_rapidgzip(source), config)
        # Stdlib raw deflate; a backward seek re-decodes from the start (see rewind_warning).
        return ZlibDecompressorStream(source, wbits=-15)

    def translator(self, config: StreamConfig) -> ExceptionTranslator:
        if _deflate_family_uses_accelerator(config):
            return self._translate_accelerator
        return self.translate

    def rewind_warning(self, config: StreamConfig) -> RewindWarning | None:
        return _rapidgzip_rewind_warning("deflate", config)

    def _translate_accelerator(self, exc: Exception) -> ArchiveyError | None:
        return _translate_rapidgzip(exc, "deflate")


class ZlibCodec(_ZlibErrorCodec):
    codec = Codec.ZLIB
    stream_format = StreamFormat.ZLIB
    # No exact magic: zlib's 2-byte header is too unspecific, so it is recognized by a content
    # probe that gates on that header before decoding.

    def open(
        self, source: CodecSource, params: CodecParams, config: StreamConfig
    ) -> BinaryIO:
        if _rapidgzip_enabled(config, available=_rapidgzip is not None):
            if _rapidgzip is None:
                raise PackageNotInstalledError(
                    "The 'rapidgzip' package is required for zlib random access "
                    "(install the 'seekable' extra)."
                )
            # rapidgzip auto-detects zlib-wrapped DEFLATE; no synthetic gzip wrapper.
            return _wrap_accelerated_length(_open_rapidgzip(source), config)
        # Stdlib zlib; a backward seek re-decodes from the start (see rewind_warning).
        return ZlibDecompressorStream(source, wbits=zlib.MAX_WBITS)

    def translator(self, config: StreamConfig) -> ExceptionTranslator:
        if _deflate_family_uses_accelerator(config):
            return self._translate_accelerator
        return self.translate

    def rewind_warning(self, config: StreamConfig) -> RewindWarning | None:
        return _rapidgzip_rewind_warning("zlib", config)

    def _translate_accelerator(self, exc: Exception) -> ArchiveyError | None:
        return _translate_rapidgzip(exc, "zlib")

    def content_probe(self, prefix: bytes) -> bool:
        """Recognize a zlib stream: a known CMF/FLG header (fail-fast) that then decodes."""
        return prefix[:2] in _ZLIB_HEADERS and self._decodes_sample(prefix)


class ZstdCodec(StreamCodec):
    codec = Codec.ZSTD
    stream_format = StreamFormat.ZSTD
    magic = (MagicSignature(0, b"\x28\xb5\x2f\xfd", ArchiveFormat.ZST),)
    requirement = MissingComponent(
        "backports.zstd", "pip install archivey[zstd]", ("zstd",)
    )

    def _backend_present(self) -> bool:
        return _zstd is not None

    def open(
        self, source: CodecSource, params: CodecParams, config: StreamConfig
    ) -> BinaryIO:
        if _zstd is None:
            raise PackageNotInstalledError(
                "The zstd backend is not available: install backports.zstd via the "
                "'zstd' extra (Python < 3.14) or use Python 3.14+ with stdlib "
                "compression.zstd."
            )
        return _zstd.open(source, "rb")

    def translate(self, exc: Exception) -> ArchiveyError | None:
        if _zstd is not None and isinstance(exc, _zstd.ZstdError):
            return CorruptionError(f"Error reading zstd stream: {exc!r}")
        if isinstance(exc, EOFError):
            return TruncatedError(f"zstd stream is truncated: {exc!r}")
        return None

    def rewind_warning(self, config: StreamConfig) -> RewindWarning | None:
        return RewindWarning("zstd")


class Lz4Codec(StreamCodec):
    codec = Codec.LZ4
    stream_format = StreamFormat.LZ4
    magic = (MagicSignature(0, b"\x04\x22\x4d\x18", ArchiveFormat.LZ4),)
    requirement = MissingComponent("lz4", "pip install archivey[lz4]", ("lz4",))

    def _backend_present(self) -> bool:
        return _lz4_frame is not None

    def open(
        self, source: CodecSource, params: CodecParams, config: StreamConfig
    ) -> BinaryIO:
        if _lz4_frame is None:
            raise PackageNotInstalledError(
                "The 'lz4' package is required for lz4 streams (install the 'lz4' extra)."
            )
        # lz4's frame reader seeks by re-decompressing from the start (the outer ArchiveStream
        # warns on a rewind — see rewind_warning).
        return ensure_binaryio(_lz4_frame.open(source, "rb"))

    def translate(self, exc: Exception) -> ArchiveyError | None:
        if isinstance(exc, RuntimeError) and str(exc).startswith("LZ4"):
            return CorruptionError(f"Error reading lz4 stream: {exc!r}")
        if isinstance(exc, EOFError):
            return TruncatedError(f"lz4 stream is truncated: {exc!r}")
        return None

    def rewind_warning(self, config: StreamConfig) -> RewindWarning | None:
        return RewindWarning("lz4")


class BrotliCodec(StreamCodec):
    codec = Codec.BROTLI
    stream_format = StreamFormat.BROTLI
    # Brotli has no signature; the detector recognizes it by decoding a bounded prefix.
    requirement = MissingComponent("brotli", "pip install archivey[7z]", ("brotli",))

    def _backend_present(self) -> bool:
        return _brotli is not None

    def open(
        self, source: CodecSource, params: CodecParams, config: StreamConfig
    ) -> BinaryIO:
        if _brotli is None:
            raise PackageNotInstalledError(
                "The 'brotli' package is required for Brotli streams (install the '7z' extra)."
            )
        # Brotli has no random-access index; a backward seek re-decodes from the start (the
        # outer ArchiveStream warns — see rewind_warning).
        return BrotliDecompressorStream(source)

    def translate(self, exc: Exception) -> ArchiveyError | None:
        # brotli raises its own brotli.error for corrupt data; a truncated stream doesn't
        # raise here (the decompressor just never reports finished), so the base
        # DecompressorStream surfaces that as TruncatedError on its own.
        if _brotli is not None and isinstance(exc, _brotli.error):
            return CorruptionError(f"Error reading brotli stream: {exc!r}")
        return None

    def rewind_warning(self, config: StreamConfig) -> RewindWarning | None:
        return RewindWarning("brotli")

    def content_probe(self, prefix: bytes) -> bool:
        """Recognize a raw Brotli stream — which has no magic — by decoding a bounded prefix."""
        return self._decodes_sample(prefix)


class UnixCompressCodec(StreamCodec):
    codec = Codec.UNIX_COMPRESS
    stream_format = StreamFormat.UNIX_COMPRESS
    magic = (MagicSignature(0, b"\x1f\x9d", ArchiveFormat.Z),)

    def open(
        self, source: CodecSource, params: CodecParams, config: StreamConfig
    ) -> BinaryIO:
        # Native LZW over DecompressorStream: forward decode works on non-seekable
        # sources; CLEAR boundaries become SeekPoints when config.seekable is true.
        return UnixCompressDecompressorStream(source, seekable=config.seekable)

    def translate(self, exc: Exception) -> ArchiveyError | None:
        # Native LZW raises CorruptionError / UnsupportedFeatureError / TruncatedError
        # directly (like xz/lzip). No third-party exception remapping.
        return None


def _parse_ppmd_var_h_properties(properties: bytes | None) -> tuple[int, int]:
    """Parse 7z PPMd var.H coder properties → ``(order, mem_size)``."""

    if properties is None:
        raise ValueError("PPMd requires coder properties (order + mem size)")
    if len(properties) == 5:
        order, mem = struct.unpack("<BL", properties)
    elif len(properties) == 7:
        order, mem, _, _ = struct.unpack("<BLBB", properties)
    else:
        raise ValueError(
            f"unsupported PPMd properties length {len(properties)} (expected 5 or 7)"
        )
    return int(order), int(mem)


class PpmdCodec(StreamCodec):
    codec = Codec.PPMD
    requirement = MissingComponent("pyppmd", "pip install archivey[7z]", ("ppmd",))

    def _backend_present(self) -> bool:
        return _pyppmd is not None

    def open(
        self, source: CodecSource, params: CodecParams, config: StreamConfig
    ) -> BinaryIO:
        if _pyppmd is None:
            raise PackageNotInstalledError(
                "The 'pyppmd' package is required for PPMd streams (install the '7z' extra)."
            )
        # ZIP method 98 supplies order/mem/restore directly (PPMd8). 7z supplies a
        # var.H properties blob (PPMd7).
        if params.ppmd_order is not None:
            if params.ppmd_mem_size is None:
                raise ValueError("ZIP PPMd requires ppmd_order and ppmd_mem_size")
            return PpmdDecompressorStream(
                source,
                order=params.ppmd_order,
                mem_size=params.ppmd_mem_size,
                variant=8,
                restore_method=params.ppmd_restore_method,
                unpack_size=params.unpack_size,
            )
        order, mem_size = _parse_ppmd_var_h_properties(params.properties)
        return PpmdDecompressorStream(
            source,
            order=order,
            mem_size=mem_size,
            unpack_size=params.unpack_size,
        )

    def translate(self, exc: Exception) -> ArchiveyError | None:
        if isinstance(exc, EOFError):
            return TruncatedError(f"PPMd stream is truncated: {exc!r}")
        if isinstance(exc, ValueError):
            return CorruptionError(f"Error reading PPMd stream: {exc!r}")
        if _pyppmd is not None and isinstance(exc, getattr(_pyppmd, "PpmdError", ())):
            return CorruptionError(f"Error reading PPMd stream: {exc!r}")
        # A corrupt PPMd8 payload can surface as SystemError from the C extension.
        if isinstance(exc, SystemError):
            return CorruptionError(f"Error reading PPMd stream: {exc!r}")
        return None


class Deflate64Codec(StreamCodec):
    codec = Codec.DEFLATE64
    requirement = MissingComponent(
        "inflate64", "pip install archivey[7z]", ("deflate64",)
    )

    def _backend_present(self) -> bool:
        return _inflate64 is not None

    def open(
        self, source: CodecSource, params: CodecParams, config: StreamConfig
    ) -> BinaryIO:
        if _inflate64 is None:
            raise PackageNotInstalledError(
                "The 'inflate64' package is required for Deflate64 streams "
                "(install the '7z' extra)."
            )
        return Deflate64DecompressorStream(source)

    def translate(self, exc: Exception) -> ArchiveyError | None:
        if isinstance(exc, EOFError):
            return TruncatedError(f"deflate64 stream is truncated: {exc!r}")
        if isinstance(exc, (ValueError, zlib.error)):
            return CorruptionError(f"Error reading deflate64 stream: {exc!r}")
        return None


# --- the codec registry ----------------------------------------------------------------

# Single source of truth for *openable* codecs. Detection, the single-file reader, and
# the backend registry iterate these. Filter-only ``Codec`` enum members (DELTA / BCJ_*)
# are intentionally absent — they compose into raw LZMA via ``LZMA_FILTER_IDS``, not
# standalone ``StreamCodec.open``.
STREAM_CODECS: tuple[StreamCodec, ...] = (
    StoredCodec(),
    GzipCodec(),
    Bzip2Codec(),
    XzCodec(),
    LzipCodec(),
    LzmaAloneCodec(),
    LzmaCodec(),
    Lzma2Codec(),
    DeflateCodec(),
    ZlibCodec(),
    ZstdCodec(),
    Lz4Codec(),
    BrotliCodec(),
    UnixCompressCodec(),
    PpmdCodec(),
    Deflate64Codec(),
)

# The codecs presented as standalone single-file formats (a subset of STREAM_CODECS).
SINGLE_FILE_CODECS: tuple[StreamCodec, ...] = tuple(
    c for c in STREAM_CODECS if c.single_file_format is not None
)

_BY_CODEC: dict[Codec, StreamCodec] = {c.codec: c for c in STREAM_CODECS}
_BY_STREAM_FORMAT: dict[StreamFormat, StreamCodec] = {
    c.stream_format: c for c in STREAM_CODECS if c.stream_format is not None
}


def stream_codec(codec: Codec) -> StreamCodec:
    """The codec object for ``codec`` (raises ``KeyError`` for a filter-only codec)."""
    return _BY_CODEC[codec]


def stream_codec_for_format(stream_format: StreamFormat) -> StreamCodec:
    """The codec object that decodes a single-file/TAR ``StreamFormat``."""
    return _BY_STREAM_FORMAT[stream_format]


def codec_for_stream_format(stream_format: StreamFormat) -> Codec:
    """Map a single-file/TAR ``StreamFormat`` to its codec."""
    return _BY_STREAM_FORMAT[stream_format].codec


def codec_requirement(codec: Codec) -> MissingComponent | None:
    """The optional-dependency requirement declared by ``codec``, if any."""
    sc = _BY_CODEC.get(codec)
    return sc.requirement if sc is not None else None


def is_codec_available(codec: Codec) -> bool:
    """Whether ``codec``'s decompression backend is importable right now.

    A codec with no ``requirement`` is stdlib-backed and always available; an optional codec
    reports on its backing package's live sentinel. Used by the registry to compute a
    format's tri-state support compositionally over the codecs it can use. Reads the
    sentinels live, so it reflects test monkeypatching.
    """
    sc = _BY_CODEC.get(codec)
    return sc is None or sc.available


@dataclass(frozen=True)
class CodecBackend:
    """A resolved codec backend: its open function (config-bound) and its translator.

    Returned by :func:`resolve_codec` so callers can obtain (and reuse) the backend
    without opening a stream — the "backend dispatch is separable from opening" contract.
    """

    codec: Codec
    config: StreamConfig
    translate: ExceptionTranslator
    rewind_warning: RewindWarning | None
    _open: Callable[[CodecSource, CodecParams, StreamConfig], BinaryIO] = field(
        repr=False
    )

    def open(
        self, source: CodecSource, params: CodecParams = _DEFAULT_PARAMS
    ) -> BinaryIO:
        return self._open(source, params, self.config)


def resolve_codec(
    codec: Codec, config: StreamConfig = DEFAULT_STREAM_CONFIG
) -> CodecBackend:
    """Resolve ``codec`` to its backend (open function + translator) without opening anything.

    The translator must match the *active* backend: when an accelerator
    (``rapidgzip`` / ``indexed_bzip2``) is the chosen backend, its exception taxonomy
    differs from stdlib's, so the codec's :meth:`StreamCodec.translator` selects the right one.
    The ``rewind_warning`` is likewise config-dependent (an active accelerator gives indexed
    random access, so it carries none); it is attached to the ``ArchiveStream`` by
    :func:`open_codec_stream`.

    Raises ``KeyError`` for a filter-only codec (Delta/BCJ), which is composed into a raw
    LZMA chain rather than opened standalone.
    """
    sc = _BY_CODEC[codec]
    return CodecBackend(
        codec=codec,
        config=config,
        translate=sc.translator(config),
        rewind_warning=sc.rewind_warning(config),
        _open=sc.open,
    )


def open_codec_stream(
    codec: Codec,
    source: CodecSource,
    *,
    config: StreamConfig = DEFAULT_STREAM_CONFIG,
    params: CodecParams = _DEFAULT_PARAMS,
    stamp: Callable[[ArchiveyError], None] | None = None,
    collector: "DiagnosticCollector | None" = None,
    seekable: bool | None = None,
) -> ArchiveStream:
    """Open a decompressing stream for ``codec`` with exceptions translated/stamped.

    The returned stream wraps the backend so corrupt/truncated/non-seekable errors surface
    as ``ArchiveyError`` subclasses (never raw codec exceptions).

    ``config.seekable`` gates accelerator ``AUTO`` resolution and native index construction.
    The ArchiveStream seekability hint is separate: pass ``seekable=False`` to force a
    forward-only public handle (as :func:`~archivey.open_stream` does by default). When
    ``seekable`` is omitted the handle stays seekable so format backends that need
    positioning on an outer codec stream (compressed TAR) keep working — member-stream
    seekability is enforced by the reader wrapper instead.
    """
    if not isinstance(source, (str, os.PathLike)):
        # A seekable stream positioned mid-file gets a clean tell()==0 origin (a
        # SlicingStream view), because codec backends address the source with absolute
        # offsets — the seekable XZ/lzip index, stdlib gzip's rewind — and would
        # otherwise read the wrong bytes. Streams at position 0 pass through unchanged
        # (see the stream-position contract in ``format-detection``).
        source = fix_stream_start_position(source)
    # Fill the AUTO size gate when the caller did not already supply a known length
    # (path ``stat``, ``SlicingStream.size``, ``BytesIO``, …). Unknown stays ``None``.
    if config.compressed_input_size is None:
        size = source_byte_size(source)
        if size is not None:
            config = replace(config, compressed_input_size=size)
    # gzip ISIZE makes truncation checkable → allows rapidgzip AUTO with the ISIZE
    # backstop; fill before resolve_codec so translator / rewind_warning agree.
    # Do not promote ISIZE into expected_decompressed_size (mod 2**32 / multi-member).
    if codec is Codec.GZIP:
        config = _config_with_gzip_isize(source, config)
    backend = resolve_codec(codec, config)
    # Default True: internal/format callers may need to seek the codec stream even when
    # ``config.seekable`` is False (no accelerator/index). Public ``open_stream`` passes
    # the caller's ``seekable=`` explicitly.
    stream_seekable = True if seekable is None else seekable
    return ArchiveStream(
        lambda: backend.open(source, params),
        translate=backend.translate,
        stamp=stamp,
        lazy=False,
        seekable=stream_seekable,
        rewind_warning=backend.rewind_warning if stream_seekable else None,
        collector=collector,
    )
