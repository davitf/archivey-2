"""Provides I/O helper classes, including exception translation and lazy opening."""

import io
import logging
import threading
from typing import (
    Any,
    BinaryIO,
    Callable,
    NoReturn,
    Optional,
)

from archivey.exceptions import ArchiveError
from archivey.internal.io_helpers import is_seekable
from archivey.internal.utils import ensure_not_none

logger = logging.getLogger(__name__)


class ArchiveStream(io.RawIOBase, BinaryIO):
    """
    Wraps an I/O stream to translate specific exceptions from an underlying library
    into ArchiveError subclasses.
    """

    def __init__(
        self,
        open_fn: Callable[[], BinaryIO],
        exception_translator: Callable[[Exception], Optional[ArchiveError]],
        lazy: bool,
        archive_path: str | None,
        member_name: str,
        seekable: bool,
    ):
        """
        Initialize the ArchiveStream wrapper.

        Exactly one of `inner` or `open_fn` must be provided.

        Args:
            open_fn: A callable that, when called, returns the binary I/O stream to be
                used.
            exception_translator: A callable that takes an Exception instance
                (raised by the `inner` stream) and returns an Optional[ArchiveError].
                - If it returns an ArchiveError instance, that error is raised,
                  chaining the original exception.
                - If it returns None, the original exception is re-raised.
                The translator should be specific in the exceptions it handles and
                avoid catching generic `Exception`.
            lazy: If True, the stream will be opened lazily when the first read or
                seek operation is performed. If False, the stream will be opened
                immediately.
            archive_path: The path of the archive this stream belongs to.
            member_name: The name of the member this stream belongs to.
            seekable: A boolean hint indicating whether the underlying stream is
                expected to be seekable. `seekable()` will return this value
                without actually opening the stream.
        """

        super().__init__()

        logger.debug(
            f"ArchiveStream.__init__: open_fn={open_fn} exception_translator={exception_translator} lazy={lazy} archive_path={archive_path} member_name={member_name} seekable={seekable}"
        )
        self._translate = exception_translator

        self._inner: BinaryIO | None = None
        self._open_fn = open_fn
        self._open_lock = threading.Lock()

        self.archive_path = archive_path
        self.member_name = member_name
        self._seekable = seekable

        if not lazy:
            self._ensure_open()

    def _ensure_open(self) -> BinaryIO:
        if self.closed:
            raise ValueError("I/O operation on closed file.")
        if self._inner is not None:
            return self._inner

        with self._open_lock:
            try:
                self._inner = ensure_not_none(self._open_fn)()
                self._open_fn = None

            except Exception as e:  # noqa: BLE001
                self._translate_exception(e)

        return self._inner

    def _translate_exception(self, e: Exception) -> NoReturn:
        if isinstance(e, ArchiveError):
            if e.archive_path is None:
                e.archive_path = self.archive_path
            if e.member_name is None:
                e.member_name = self.member_name
            raise e

        translated = self._translate(e)
        if translated is not None:
            translated.archive_path = self.archive_path
            translated.member_name = self.member_name
            logger.debug(
                "Translated exception: %r -> %r",
                e,
                translated,
            )

            raise translated from e

        if not isinstance(e, ArchiveError):
            logger.error("Unknown exception when reading IO: %r", e, exc_info=e)
        raise e

    def read(self, n: int = -1) -> bytes:
        # Some rarfile streams don't actually prevent reading after closing, so we
        # enforce that here.
        try:
            return self._ensure_open().read(n)
        except Exception as e:  # noqa: BLE001
            self._translate_exception(e)

    def _readinto_fallback(self, b: bytearray | memoryview) -> int:
        data = self.read(len(b))
        b[: len(data)] = data
        return len(data)

    def readinto(self, b: bytearray | memoryview) -> int:
        # BinaryIO objects don't necessarily have readinto (specifically, XZFile from
        # python-xz doesn't), so we fall back to read() if needed.
        if not hasattr(self._ensure_open(), "readinto"):
            return self._readinto_fallback(b)

        try:
            return self._ensure_open().readinto(b)  # type: ignore[attr-defined]
        except Exception as e:  # noqa: BLE001
            self._translate_exception(e)

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        if self.seekable():
            logger.debug(
                f"ArchiveStream for {self.archive_path}:{self.member_name} seek({offset}, {whence}) (prev_pos={self.tell()}) (inner={self._inner})"
            )
        else:
            logger.debug(
                f"ArchiveStream for {self.archive_path}:{self.member_name} seek({offset}, {whence}) (not seekable) (inner={self._inner})"
            )

        try:
            return self._ensure_open().seek(offset, whence)
        except Exception as e:  # noqa: BLE001
            self._translate_exception(e)

    def tell(self) -> int:
        if self.closed:
            raise ValueError("I/O operation on closed file.")
        if self._inner is None:
            return 0
        return self._ensure_open().tell()

    def readable(self) -> bool:
        return True

    def writable(self) -> bool:
        return False

    def seekable(self) -> bool:
        return is_seekable(self._inner) if self._inner is not None else self._seekable

    def write(self, b: Any) -> int:
        raise NotImplementedError("ArchiveStream is not writable.")

    def writelines(self, lines: Any) -> None:
        raise NotImplementedError("ArchiveStream is not writable.")

    def close(self) -> None:
        logger.debug(f"ArchiveStream.close: inner={self._inner}")
        if self._inner is not None:
            try:
                self._inner.close()
            except Exception as e:  # noqa: BLE001
                self._translate_exception(e)

        super().close()

    def __str__(self) -> str:
        return f"<ArchiveStream {self.archive_path}:{self.member_name})>"

    def __repr__(self) -> str:
        return f"<ArchiveStream archive_path={self.archive_path!r} member_name={self.member_name!r} inner={self._inner!r}>"
