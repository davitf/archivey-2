"""``ArchiveStream`` — wraps a member/codec stream and translates raw exceptions.

This is the carrier for the exception-translation contract (see ``error-handling`` and
CONTRIBUTING). Any exception raised while opening or reading the wrapped stream is routed
through a per-library *translator* (raw third-party exception → ``ArchiveyError`` subclass
or ``None`` to let it propagate), then *stamped* with format/archive/member context.
"""

from __future__ import annotations

import io
import threading
from typing import TYPE_CHECKING, Any, BinaryIO, Callable, NoReturn

from archivey.internal.errors import ArchiveyError
from archivey.internal.logs import streams as logger
from archivey.internal.streams.compat import is_seekable

if TYPE_CHECKING:
    from _typeshed import WriteableBuffer

# A translator maps a raw exception to an ArchiveyError, or returns None to signal "not
# mine — let it propagate unchanged" (the catch-all-free rule in CONTRIBUTING).
ExceptionTranslator = Callable[[Exception], ArchiveyError | None]
# A stamp attaches context (format/archive/member) to an already-translated error.
ErrorStamp = Callable[[ArchiveyError], None]


def _noop_stamp(_exc: ArchiveyError) -> None:
    return None


class ArchiveStream(io.RawIOBase, BinaryIO):
    """Translate exceptions from an underlying binary stream into ``ArchiveyError``s.

    The wrapped stream may be opened lazily (on first use) so callers can hand out a
    handle cheaply; ``seekable()`` answers from the ``seekable`` hint until the stream is
    actually opened.
    """

    def __init__(
        self,
        open_fn: Callable[[], BinaryIO],
        *,
        translate: ExceptionTranslator,
        stamp: ErrorStamp | None = None,
        lazy: bool = False,
        seekable: bool = True,
    ) -> None:
        super().__init__()
        self._open_fn: Callable[[], BinaryIO] | None = open_fn
        self._translate = translate
        self._stamp = stamp if stamp is not None else _noop_stamp
        self._inner: BinaryIO | None = None
        self._open_lock = threading.Lock()
        self._seekable_hint = seekable
        if not lazy:
            self._ensure_open()

    def _ensure_open(self) -> BinaryIO:
        if self.closed:
            raise ValueError("I/O operation on closed file.")
        if self._inner is not None:
            return self._inner
        with self._open_lock:
            if self._inner is None:
                open_fn = self._open_fn
                assert open_fn is not None
                try:
                    self._inner = open_fn()
                except Exception as e:  # noqa: BLE001 - re-raised via the translator
                    self._fail(e)
                self._open_fn = None
        return self._inner

    def _fail(self, e: Exception) -> NoReturn:
        """Translate + stamp ``e`` and raise, or re-raise it unchanged."""
        if isinstance(e, ArchiveyError):
            self._stamp(e)
            raise e
        translated = self._translate(e)
        if translated is not None:
            self._stamp(translated)
            logger.debug("Translated exception: %r -> %r", e, translated)
            raise translated from e
        raise e

    def read(self, n: int = -1, /) -> bytes:
        try:
            return self._ensure_open().read(n)
        except Exception as e:  # noqa: BLE001 - re-raised via the translator
            self._fail(e)

    def readinto(self, b: "WriteableBuffer", /) -> int:
        inner = self._ensure_open()
        readinto = getattr(inner, "readinto", None)
        if readinto is None:
            mv = memoryview(b).cast("B")
            data = self.read(len(mv))
            mv[: len(data)] = data
            return len(data)
        try:
            return readinto(b)
        except Exception as e:  # noqa: BLE001 - re-raised via the translator
            self._fail(e)

    def seek(self, offset: int, whence: int = io.SEEK_SET, /) -> int:
        try:
            return self._ensure_open().seek(offset, whence)
        except Exception as e:  # noqa: BLE001 - re-raised via the translator
            self._fail(e)

    def tell(self, /) -> int:
        if self.closed:
            raise ValueError("I/O operation on closed file.")
        if self._inner is None:
            return 0
        return self._inner.tell()

    def readable(self) -> bool:
        return True

    def writable(self) -> bool:
        return False

    def seekable(self) -> bool:
        if self._inner is not None:
            return is_seekable(self._inner)
        return self._seekable_hint

    def write(self, data: Any, /) -> int:
        raise io.UnsupportedOperation("ArchiveStream is not writable")

    def close(self) -> None:
        if self._inner is not None:
            try:
                self._inner.close()
            except Exception as e:  # noqa: BLE001 - re-raised via the translator
                self._fail(e)
        super().close()

    def __repr__(self) -> str:
        return f"<ArchiveStream inner={self._inner!r}>"
