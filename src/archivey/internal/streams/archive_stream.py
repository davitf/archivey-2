"""``ArchiveStream`` — wraps a member/codec stream and translates raw exceptions.

This is the carrier for the exception-translation contract (see ``error-handling`` and
CONTRIBUTING). Any exception raised while opening or reading the wrapped stream is routed
through a per-library *translator* (raw third-party exception → ``ArchiveyError`` subclass
or ``None`` to let it propagate), then *stamped* with format/archive/member context.
"""

from __future__ import annotations

import io
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, BinaryIO, Callable, NoReturn

from archivey.exceptions import ArchiveyError
from archivey.internal.logs import streams as logger
from archivey.internal.streams.streamtools import ReadOnlyIOStream, is_seekable

if TYPE_CHECKING:
    from _typeshed import WriteableBuffer

# A translator maps a raw exception to an ArchiveyError, or returns None to signal "not
# mine — let it propagate unchanged" (the catch-all-free rule in CONTRIBUTING).
ExceptionTranslator = Callable[[Exception], ArchiveyError | None]
# A stamp attaches context (format/archive/member) to an already-translated error.
ErrorStamp = Callable[[ArchiveyError], None]


@dataclass(frozen=True)
class RewindWarning:
    """Signals that this codec services a backward seek by re-decompressing from the start.

    Carried by :class:`ArchiveStream` so the public stream handle can warn once, on the first
    rewinding seek, that random access is O(n) here. ``codec_name`` names the format; when an
    ``accelerator`` package (the ``[seekable]`` extra) would provide indexed random access, the
    warning names it. Codecs with a native random-access index (or an active accelerator) carry
    no ``RewindWarning``.
    """

    codec_name: str
    accelerator: str | None = None


def _noop_stamp(_exc: ArchiveyError) -> None:
    return None


class ArchiveStream(ReadOnlyIOStream):
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
        rewind_warning: RewindWarning | None = None,
    ) -> None:
        super().__init__()
        self._open_fn: Callable[[], BinaryIO] | None = open_fn
        self._translate = translate
        self._stamp = stamp if stamp is not None else _noop_stamp
        self._inner: BinaryIO | None = None
        self._open_lock = threading.Lock()
        self._seekable_hint = seekable
        self._rewind_warning = rewind_warning
        self._rewind_warned = False
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
            inner = self._ensure_open()
            before = inner.tell()
            result = inner.seek(offset, whence)
        except Exception as e:  # noqa: BLE001 - re-raised via the translator
            self._fail(e)
        self._maybe_warn_rewind(before, result)
        return result

    def _maybe_warn_rewind(self, before: int, after: int) -> None:
        """Warn once when a backward seek will re-decompress from the start (O(n))."""
        warning = self._rewind_warning
        if warning is None or self._rewind_warned or after >= before:
            return
        self._rewind_warned = True
        if warning.accelerator is not None:
            logger.warning(
                "Seeking backward in a %s stream without a random-access accelerator "
                "re-decompresses from the start (O(n) per rewind). Install the 'seekable' "
                "extra (%s) for indexed random access.",
                warning.codec_name,
                warning.accelerator,
            )
        else:
            logger.warning(
                "Seeking backward in a %s stream re-decompresses from the start (O(n) per "
                "rewind): this codec has no random-access index.",
                warning.codec_name,
            )

    def tell(self, /) -> int:
        if self.closed:
            raise ValueError("I/O operation on closed file.")
        if self._inner is None:
            return 0
        return self._inner.tell()

    def seekable(self) -> bool:
        # readable()/writable()/write() come from ReadOnlyIOStream.
        if self._inner is not None:
            return is_seekable(self._inner)
        return self._seekable_hint

    def close(self) -> None:
        if self.closed:
            return
        # The finally ensures the wrapper is marked closed even when the inner close
        # raises (which _fail re-raises translated): a half-open wrapper would hand out
        # further reads on a dead stream, and a retried close() would fail again
        # instead of no-opping (the guard above makes it a no-op instead).
        try:
            if self._inner is not None:
                try:
                    self._inner.close()
                except Exception as e:  # noqa: BLE001 - re-raised via the translator
                    self._fail(e)
        finally:
            super().close()

    def __repr__(self) -> str:
        return f"<ArchiveStream inner={self._inner!r}>"
