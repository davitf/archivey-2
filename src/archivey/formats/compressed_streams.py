import bz2
import gzip
import io
import lzma
import os
import zlib
from typing import (
    TYPE_CHECKING,
    BinaryIO,
    Callable,
    Optional,
    cast,
)

if TYPE_CHECKING:
    from typing_extensions import Buffer
else:
    # Keep the core import-time dependency-free: `Buffer` is only used in
    # annotations. typing_extensions provides it on 3.11 (typing.Buffer is 3.12+)
    # but is an optional/dev dependency, so fall back to a runtime placeholder.
    try:
        from typing_extensions import Buffer
    except ImportError:  # pragma: no cover - bare core install
        Buffer = bytes

from archivey.config import ArchiveyConfig, get_archivey_config
from archivey.internal.archive_stream import ArchiveStream
from archivey.internal.io_helpers import (
    ExceptionTranslatorFn,
    ensure_bufferedio,
    is_seekable,
    is_stream,
)
from archivey.types import StreamFormat

if TYPE_CHECKING:
    import brotli
    import indexed_bzip2
    import lz4.frame
    import pyzstd
    import rapidgzip
    import uncompresspy
    import xz
    import zstandard

else:
    try:
        import lz4.frame
    except ImportError:
        lz4 = None

    try:
        import zstandard
    except ImportError:
        zstandard = None

    try:
        import pyzstd
    except ImportError:
        try:
            # Built-in in Python >= 3.14
            import compression.zstd as pyzstd  # type: ignore[no-redef]
        except ImportError:
            pyzstd = None

    try:
        import rapidgzip
    except ImportError:
        rapidgzip = None

    try:
        import indexed_bzip2
    except ImportError:
        indexed_bzip2 = None

    try:
        import uncompresspy
    except ImportError:
        uncompresspy = None

    try:
        import brotli
    except ImportError:
        brotli = None

    try:
        import xz
    except ImportError:
        xz = None


import logging

from archivey.exceptions import (
    ArchiveCorruptedError,
    ArchiveEOFError,
    ArchiveError,
    ArchiveStreamNotSeekableError,
    PackageNotInstalledError,
)
from archivey.formats.decompressor_stream import (
    BrotliDecompressorStream,
    ZlibDecompressorStream,
)
from archivey.formats.lzip_stream import LzipDecompressorStream
from archivey.formats.xz_stream import XzDecompressorStream
from archivey.internal.io_helpers import ensure_binaryio

logger = logging.getLogger(__name__)


def _translate_gzip_exception(e: Exception) -> Optional[ArchiveError]:
    if isinstance(e, gzip.BadGzipFile):
        return ArchiveCorruptedError(f"Error reading GZIP archive: {repr(e)}")
    if isinstance(e, EOFError):
        return ArchiveEOFError(f"GZIP file is truncated: {repr(e)}")
    return None  # pragma: no cover -- all possible exceptions should have been handled


def open_gzip_stream(path: str | BinaryIO) -> BinaryIO:
    if isinstance(path, (str, bytes, os.PathLike)):
        gz = gzip.open(path, mode="rb")
        underlying_seekable = True
    else:
        assert not path.closed
        gz = gzip.GzipFile(fileobj=ensure_bufferedio(path), mode="rb")
        assert not path.closed
        underlying_seekable = is_seekable(path)

    if not underlying_seekable:
        # GzipFile always returns True for seekable, even if the underlying stream
        # is not seekable.
        gz.seekable = lambda: False

        def _unsupported_seek(offset, whence=io.SEEK_SET):
            raise io.UnsupportedOperation("seek")

        gz.seek = _unsupported_seek

    return ensure_binaryio(gz)


def _translate_rapidgzip_exception(e: Exception) -> Optional[ArchiveError]:
    exc_text = str(e)
    if isinstance(e, RuntimeError) and "IsalInflateWrapper" in exc_text:
        return ArchiveCorruptedError(f"Error reading RapidGZIP archive: {repr(e)}")
    if isinstance(e, ValueError) and "Mismatching CRC32" in exc_text:
        return ArchiveCorruptedError(f"Error reading RapidGZIP archive: {repr(e)}")
    if isinstance(e, ValueError) and "Failed to detect a valid file format" in str(e):
        # If we have opened a gzip stream, the magic bytes are there. So if the library
        # fails to detect a valid format, it's because the file is truncated.
        return ArchiveEOFError(f"Possibly truncated GZIP stream: {repr(e)}")
    if isinstance(e, ValueError) and "has no valid fileno" in exc_text:
        # Rapidgzip tries to look at the underlying stream's fileno if it's not
        # seekable.
        return ArchiveStreamNotSeekableError(
            "rapidgzip does not support non-seekable streams"
        )
    if isinstance(e, io.UnsupportedOperation) and "seek" in exc_text:
        return ArchiveStreamNotSeekableError(
            "rapidgzip does not support non-seekable streams"
        )
    # This happens in some rapidgzip builds, not all.
    if isinstance(e, RuntimeError) and "std::exception" in str(e):
        return ArchiveCorruptedError(
            f"Unknown error reading RapidGZIP archive: {repr(e)}"
        )

    # Found in rapidgzip 0.11.0
    if (
        isinstance(e, ValueError)
        and "End of file encountered when trying to read zero-terminated string"
        in exc_text
    ):
        return ArchiveEOFError(f"Possibly truncated GZIP stream: {repr(e)}")
    return None  # pragma: no cover -- all possible exceptions should have been handled


def open_rapidgzip_stream(path: str | BinaryIO) -> BinaryIO:
    if rapidgzip is None:
        raise PackageNotInstalledError(
            "rapidgzip package is not installed, required for GZIP archives"
        ) from None  # pragma: no cover -- rapidgzip is installed for main tests

    return rapidgzip.open(path, parallelization=0)


def _translate_bz2_exception(e: Exception) -> Optional[ArchiveError]:
    exc_text = str(e)
    if isinstance(e, OSError) and "Invalid data stream" in exc_text:
        return ArchiveCorruptedError(f"BZ2 file is corrupted: {repr(e)}")
    if isinstance(e, EOFError):
        return ArchiveEOFError(f"BZ2 file is truncated: {repr(e)}")
    return None  # pragma: no cover -- all possible exceptions should have been handled


def open_bzip2_stream(path: str | BinaryIO) -> BinaryIO:
    return ensure_binaryio(bz2.open(path))


def _translate_indexed_bzip2_exception(e: Exception) -> Optional[ArchiveError]:
    exc_text = str(e)
    if isinstance(e, RuntimeError) and "Calculated CRC" in exc_text:
        return ArchiveCorruptedError(f"Error reading Indexed BZIP2 archive: {repr(e)}")
    # Unspecified exception in the indexed_bzip2 native code, likely when dealing with
    # corrupted data.
    if isinstance(e, RuntimeError) and exc_text in (
        "std::exception",  # Seen in Linux with non-prebuilt wheels
        "Unknown exception",  # Seen in Windows Github actions tests
    ):
        return ArchiveCorruptedError(f"Error reading Indexed BZIP2 archive: {repr(e)}")
    if isinstance(e, ValueError) and "[BZip2 block data]" in exc_text:
        return ArchiveCorruptedError(f"Error reading Indexed BZIP2 archive: {repr(e)}")
    if isinstance(e, ValueError) and "has no valid fileno" in exc_text:
        # Indexed BZIP2 tries to look at the underlying stream's fileno if it's not
        # seekable.
        return ArchiveStreamNotSeekableError(
            "indexed_bzip2 does not support non-seekable streams"
        )
    if isinstance(e, io.UnsupportedOperation) and "seek" in exc_text:
        return ArchiveStreamNotSeekableError(
            "indexed_bzip2 does not support non-seekable streams"
        )
    return None  # pragma: no cover -- all possible exceptions should have been handled


def open_indexed_bzip2_stream(path: str | BinaryIO) -> BinaryIO:
    if indexed_bzip2 is None:
        raise PackageNotInstalledError(
            "indexed_bzip2 package is not installed, required for BZIP2 archives"
        ) from None  # pragma: no cover -- indexed_bzip2 is installed for main tests

    return indexed_bzip2.open(path, parallelization=0)


def _translate_lzma_exception(e: Exception) -> Optional[ArchiveError]:
    if isinstance(e, lzma.LZMAError):
        return ArchiveCorruptedError(f"Error reading LZMA archive: {repr(e)}")
    if isinstance(e, EOFError):
        return ArchiveEOFError(f"LZMA file is truncated: {repr(e)}")
    return None  # pragma: no cover -- all possible exceptions should have been handled


def open_lzma_stream(path: str | BinaryIO) -> BinaryIO:
    return ensure_binaryio(lzma.open(path))


def _translate_xz_exception(e: Exception) -> Optional[ArchiveError]:
    if isinstance(e, lzma.LZMAError):
        return ArchiveCorruptedError(f"Error reading XZ archive: {repr(e)}")
    if isinstance(e, EOFError):
        return ArchiveEOFError(f"XZ file is truncated: {repr(e)}")
    return None


def open_xz_stream(path: str | BinaryIO) -> BinaryIO:
    return XzDecompressorStream(path)


def _translate_python_xz_exception(e: Exception) -> Optional[ArchiveError]:
    if isinstance(e, xz.XZError):
        return ArchiveCorruptedError(f"Error reading XZ archive: {repr(e)}")
    if isinstance(e, ValueError) and "filename is not seekable" in str(e):
        return ArchiveStreamNotSeekableError(
            "python-xz does not support non-seekable streams"
        )
    if isinstance(e, io.UnsupportedOperation) and "seek to end" in str(e):
        return ArchiveStreamNotSeekableError(
            "python-xz does not support non-seekable streams"
        )
    return None  # pragma: no cover -- all possible exceptions should have been handled


def open_python_xz_stream(path: str | BinaryIO) -> BinaryIO:
    if xz is None:
        raise PackageNotInstalledError(
            "python-xz package is not installed, required for XZ archives"
        ) from None

    return ensure_binaryio(xz.open(path))


class ZstandardReopenOnBackwardsSeekIO(io.RawIOBase, BinaryIO):
    """Wrap a stream that supports seeking backwards, and reopen it if a backwards seek is attempted."""

    def __init__(self, archive_path: str | BinaryIO):
        super().__init__()
        self._archive_path = archive_path
        self._inner = zstandard.open(archive_path)
        self._size = None

    def _reopen_stream(self) -> None:
        self._inner.close()
        logger.warning(
            "Reopening Zstandard stream for backwards seeking: {self._archive_path}"
        )
        if is_stream(self._archive_path):
            self._archive_path.seek(0)
        self._inner = zstandard.open(self._archive_path)

    def seekable(self) -> bool:
        if is_stream(self._archive_path):
            return is_seekable(self._archive_path)
        return True

    def readable(self) -> bool:
        return True

    def writable(self) -> bool:
        return False

    def read(self, n: int = -1) -> bytes:
        return self._inner.read(n)

    def readinto(self, b: Buffer) -> int:
        return self._inner.readinto(b)  # type: ignore[attr-defined]

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        new_pos: int
        if whence == io.SEEK_SET:
            new_pos = offset
        elif whence == io.SEEK_CUR:
            new_pos = self._inner.tell() + offset
        elif whence == io.SEEK_END:
            # Very inefficient, but we don't have a way to get the size of the stream
            # without reading it. This is the way _compression.DecompressReader does it.
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
                self._reopen_stream()
                return self._inner.seek(new_pos)
            raise

    def close(self) -> None:
        self._inner.close()
        super().close()


def _translate_zstandard_exception(e: Exception) -> Optional[ArchiveError]:
    if isinstance(e, zstandard.ZstdError):
        return ArchiveCorruptedError(f"Error reading Zstandard archive: {repr(e)}")
    return None  # pragma: no cover -- all possible exceptions should have been handled


def open_zstandard_stream(path: str | BinaryIO) -> BinaryIO:
    if zstandard is None:
        raise PackageNotInstalledError(
            "zstandard package is not installed, required for Zstandard archives"
        ) from None  # pragma: no cover -- lz4 is installed for main tests

    return ZstandardReopenOnBackwardsSeekIO(path)


def _translate_pyzstd_exception(e: Exception) -> Optional[ArchiveError]:
    if isinstance(e, pyzstd.ZstdError):
        return ArchiveCorruptedError(f"Error reading Zstandard archive: {repr(e)}")
    if isinstance(e, EOFError):
        return ArchiveEOFError(f"Zstandard file is truncated: {repr(e)}")
    return None  # pragma: no cover -- all possible exceptions should have been handled


def open_pyzstd_stream(path: str | BinaryIO) -> BinaryIO:
    if pyzstd is None:
        raise PackageNotInstalledError(
            "pyzstd package is not installed, required for Zstandard archives"
        ) from None  # pragma: no cover -- pyzstd is installed for main tests
    return ensure_binaryio(pyzstd.open(path))


def _translate_lz4_exception(e: Exception) -> Optional[ArchiveError]:
    if isinstance(e, RuntimeError) and str(e).startswith("LZ4"):
        return ArchiveCorruptedError(f"Error reading LZ4 archive: {repr(e)}")
    if isinstance(e, EOFError):
        return ArchiveEOFError(f"LZ4 file is truncated: {repr(e)}")
    return None  # pragma: no cover -- all possible exceptions should have been handled


def open_lz4_stream(path: str | BinaryIO) -> BinaryIO:
    if lz4 is None:
        raise PackageNotInstalledError(
            "lz4 package is not installed, required for LZ4 archives"
        ) from None  # pragma: no cover -- lz4 is installed for main tests

    return ensure_binaryio(cast("lz4.frame.LZ4FrameFile", lz4.frame.open(path)))


def _translate_lzip_exception(e: Exception) -> Optional[ArchiveError]:
    if isinstance(e, lzma.LZMAError):
        return ArchiveCorruptedError(f"Error reading lzip archive: {e}")
    return None


def open_lzip_stream(path: str | BinaryIO) -> BinaryIO:
    return LzipDecompressorStream(path)


def open_zlib_stream(path: str | BinaryIO) -> BinaryIO:
    return ZlibDecompressorStream(path)


def _translate_zlib_exception(e: Exception) -> Optional[ArchiveError]:
    if isinstance(e, zlib.error):
        if "incomplete" in str(e) or "truncated" in str(e):
            return ArchiveEOFError(f"Zlib file is truncated: {repr(e)}")
        return ArchiveCorruptedError(f"Error reading Zlib archive: {repr(e)}")
    if isinstance(e, EOFError):
        return ArchiveEOFError(f"Zlib file is truncated: {repr(e)}")
    return None


def _translate_brotli_exception(e: Exception) -> Optional[ArchiveError]:
    if isinstance(e, brotli.error):
        return ArchiveCorruptedError(f"Error reading Brotli archive: {repr(e)}")
    return None


def open_brotli_stream(path: str | BinaryIO) -> BinaryIO:
    if brotli is None:
        raise PackageNotInstalledError(
            "brotli package is not installed, required for Brotli archives"
        ) from None
    return BrotliDecompressorStream(path)


def _translate_uncompresspy_exception(e: Exception) -> Optional[ArchiveError]:
    if isinstance(e, ValueError) and "must be seekable" in str(e):
        return ArchiveStreamNotSeekableError(
            "uncompresspy does not support non-seekable streams"
        )
    return None


if uncompresspy is not None:

    class UncompresspyStream(uncompresspy.LZWFile):
        def __init__(self, path: str | BinaryIO) -> None:
            super().__init__(path)
            self._total_size = None

        def _find_total_size(self) -> int:
            if self._total_size is not None:
                return self._total_size

            # uncompresspy keeps checkpoints, so we can jump directly to the last known
            # position to avoid re-decompressing data before it.
            current_pos = self.tell()
            if self._checkpoints_uncompressed:
                max_known_pos = self._checkpoints_uncompressed[-1]
                if max_known_pos > current_pos:
                    self.seek(max_known_pos)
                    current_pos = max_known_pos

            while True:
                chunk = self.read(65536)
                if not chunk:
                    break
                current_pos += len(chunk)

            assert current_pos == self.tell()
            self._total_size = current_pos
            return self._total_size

        def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
            # Override the seek method to allow seeking from the end.
            if whence == io.SEEK_END:
                # Find the end of the stream.
                total_size = self._find_total_size()
                return super().seek(total_size + offset)

            return super().seek(offset, whence)


def open_uncompresspy_stream(path: str | BinaryIO) -> BinaryIO:
    if uncompresspy is None:
        raise PackageNotInstalledError(
            "uncompresspy package is not installed, required for Unix compress archives"
        ) from None  # pragma: no cover -- uncompresspy is installed for main tests

    return ensure_binaryio(UncompresspyStream(path))


def get_stream_open_fn(
    format: StreamFormat, config: ArchiveyConfig | None = None
) -> tuple[Callable[[str | BinaryIO], BinaryIO], ExceptionTranslatorFn]:
    if config is None:
        config = get_archivey_config()
    if format == StreamFormat.GZIP:
        if config.use_rapidgzip:
            return open_rapidgzip_stream, _translate_rapidgzip_exception
        return open_gzip_stream, _translate_gzip_exception

    if format == StreamFormat.BZIP2:
        if config.use_indexed_bzip2:
            return open_indexed_bzip2_stream, _translate_indexed_bzip2_exception
        return open_bzip2_stream, _translate_bz2_exception

    if format == StreamFormat.XZ:
        if config.use_python_xz:
            return open_python_xz_stream, _translate_python_xz_exception
        return open_xz_stream, _translate_xz_exception

    if format == StreamFormat.LZ4:
        return open_lz4_stream, _translate_lz4_exception

    if format == StreamFormat.LZIP:
        return open_lzip_stream, _translate_lzip_exception

    if format == StreamFormat.ZLIB:
        return open_zlib_stream, _translate_zlib_exception

    if format == StreamFormat.BROTLI:
        return open_brotli_stream, _translate_brotli_exception

    if format == StreamFormat.ZSTD:
        if config.use_zstandard:
            return open_zstandard_stream, _translate_zstandard_exception
        return open_pyzstd_stream, _translate_pyzstd_exception

    if format == StreamFormat.UNIX_COMPRESS:
        return open_uncompresspy_stream, _translate_uncompresspy_exception

    raise ValueError(f"Unsupported archive format: {format}")  # pragma: no cover


def open_stream(
    format: StreamFormat,
    path_or_stream: str | BinaryIO,
    config: ArchiveyConfig,
) -> BinaryIO:
    logger.debug(
        f"open_stream: format={format} path_or_stream={path_or_stream} config={config}"
    )
    open_fn, exception_translator = get_stream_open_fn(format, config)
    return ArchiveStream(
        open_fn=lambda: open_fn(path_or_stream),
        exception_translator=exception_translator,
        lazy=False,
        archive_path=path_or_stream if isinstance(path_or_stream, str) else None,
        member_name="<stream>",
        seekable=True,
    )
