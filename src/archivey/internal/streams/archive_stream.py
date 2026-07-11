"""``ArchiveStream`` — wraps a member/codec stream and translates raw exceptions.

This is the carrier for the exception-translation contract (see ``error-handling`` and
CONTRIBUTING). Any exception raised while opening or reading the wrapped stream is routed
through a per-library *translator* (raw third-party exception → ``ArchiveyError`` subclass
or ``None`` to let it propagate), then *stamped* with format/archive/member context.
"""

from __future__ import annotations

import io
import sys
import threading
import weakref
from dataclasses import dataclass
from typing import TYPE_CHECKING, BinaryIO, Callable, NoReturn

from archivey.diagnostics import (
    DiagnosticCode,
    DiagnosticSummary,
    StreamRewindContext,
)
from archivey.exceptions import ArchiveyError, ArchiveyUsageError
from archivey.internal.diagnostics_collector import resolve_collector
from archivey.internal.logs import streams as logger
from archivey.internal.streams.streamtools import ReadOnlyIOStream, is_seekable

if TYPE_CHECKING:
    from _typeshed import WriteableBuffer

    from archivey.internal.diagnostics_collector import DiagnosticCollector

# A translator maps a raw exception to an ArchiveyError, or returns None to signal "not
# mine — let it propagate unchanged" (the catch-all-free rule in CONTRIBUTING).
ExceptionTranslator = Callable[[Exception], ArchiveyError | None]
# A stamp attaches context (format/archive/member) to an already-translated error.
ErrorStamp = Callable[[ArchiveyError], None]
# Optional close hook (e.g. reader live-stream / lease release).
CloseHook = Callable[[], None]


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
        size: int | None = None,
        collector: DiagnosticCollector | None = None,
        on_close: CloseHook | None = None,
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
        self._size = size
        self._diagnostics_collector = collector
        self._on_close = on_close
        # A stream's diagnostics are everything emitted from its open onward: capture the
        # collector position here and difference against "now" on each query. No per-stream
        # bookkeeping is retained collector-side.
        self._diagnostics_watermark = (
            collector.watermark() if collector is not None else None
        )
        self._finalizer: weakref.finalize | None = None
        if not lazy:
            self._ensure_open()

    def _attach_finalizer(self) -> None:
        """Safety-net finalizer: release the lease if the caller never closed us.

        Never raises; reports via ``sys.unraisablehook`` if release fails.
        """
        if self._finalizer is not None:
            return
        on_close = self._on_close

        def _finalize() -> None:
            try:
                if on_close is not None:
                    on_close()
            except Exception as exc:  # noqa: BLE001 - finalizers must not raise
                from types import SimpleNamespace
                from typing import cast

                try:
                    # ``sys.unraisablehook`` expects UnraisableHookArgs; SimpleNamespace
                    # matches the runtime shape (exc_type/value/traceback/err_msg/object).
                    hook_args = cast(
                        sys.UnraisableHookArgs,
                        SimpleNamespace(
                            exc_type=type(exc),
                            exc_value=exc,
                            exc_traceback=exc.__traceback__,
                            err_msg="ArchiveStream finalizer failed",
                            object=None,
                        ),
                    )
                    sys.unraisablehook(hook_args)
                except Exception:  # noqa: BLE001 - never raise from a finalizer
                    pass

        # Hold only the close hook; do not keep the stream alive.
        self._finalizer = weakref.finalize(self, _finalize)

    def _detach_finalizer(self) -> None:
        finalizer = self._finalizer
        self._finalizer = None
        if finalizer is not None:
            finalizer.detach()

    @property
    def diagnostics(self) -> DiagnosticSummary:
        """Diagnostic snapshot for events emitted since this stream opened, or empty."""
        collector = self._diagnostics_collector
        watermark = self._diagnostics_watermark
        if collector is None or watermark is None:
            return DiagnosticSummary.empty()
        return collector.snapshot(since=watermark)

    @property
    def size(self) -> int | None:
        """Total decompressed byte length when cheaply known, else ``None``.

        The fsspec-style ``size`` convention (see ``source_byte_size``): the creator may
        supply it up front (a member stream knows ``member.size`` from the archive
        metadata), else an opened inner decompressor with a cheap ``try_get_size()``
        (index/trailer scan, no decompression) is consulted. Lets a nested
        ``open_archive(reader.open("inner.zip"))`` learn its source size — e.g. for the
        extraction bomb tracker — without an expensive end-seek. A lazy, still-unopened
        stream reports ``None`` rather than opening itself just to answer.
        """
        if self._size is not None:
            return self._size
        inner = self._inner
        if inner is None:
            return None
        try_get_size = getattr(inner, "try_get_size", None)
        if callable(try_get_size):
            result = try_get_size()
            return result if isinstance(result, int) else None
        inner_size = getattr(inner, "size", None)
        if isinstance(inner_size, int) and not isinstance(inner_size, bool):
            return inner_size
        return None

    def _ensure_open(self) -> BinaryIO:
        if self.closed:
            raise ValueError("I/O operation on closed file.")
        if self._inner is not None:
            return self._inner
        # Claim the right to call open_fn under the lock, then invoke open_fn
        # *outside* it so a backend lock acquired inside open_fn (TAR/ISO shared
        # handle) never nests under stream-state. Publish the result under the lock.
        open_fn: Callable[[], BinaryIO] | None
        with self._open_lock:
            if self._inner is not None:
                return self._inner
            if self._open_fn is None:
                # Another caller claimed open and failed, or close raced us.
                if self.closed:
                    raise ValueError("I/O operation on closed file.")
                raise ArchiveyUsageError(
                    "Cannot open this member stream: lazy initialization already failed."
                )
            open_fn = self._open_fn
            self._open_fn = None  # claim: only one caller proceeds to open_fn
        try:
            opened = open_fn()
        except Exception as e:  # noqa: BLE001 - re-raised via the translator
            with self._open_lock:
                # Leave _open_fn None so a retry does not re-enter a half-open backend.
                pass
            self._fail(e)
        with self._open_lock:
            if self.closed:
                try:
                    opened.close()
                except Exception:  # noqa: BLE001 - best-effort; stream already closing
                    pass
                raise ValueError("I/O operation on closed file.")
            self._inner = opened
            return self._inner

    def _fail(self, e: Exception) -> NoReturn:
        """Translate + stamp ``e`` and raise, or re-raise it unchanged."""
        if isinstance(e, ArchiveyError):
            self._stamp(e)
            raise e
        if isinstance(e, ValueError) and "closed file" in str(e):
            # The *inner* stream hit a closed handle underneath it — typically the
            # caller closed their supplied BinaryIO early. Mapped here, before the
            # per-library translator, so a backend's generic ValueError mapping cannot
            # claim it. The wrapper's own read-after-close never reaches _fail (plain
            # ValueError from _ensure_open).
            translated_closed = ArchiveyUsageError(
                "Cannot read this member stream: its underlying caller-owned source "
                "has been closed."
            )
            logger.debug("Translated exception: %r -> %r", e, translated_closed)
            raise translated_closed from e
        translated = self._translate(e)
        if translated is not None:
            self._stamp(translated)
            logger.debug("Translated exception: %r -> %r", e, translated)
            raise translated from e
        raise e

    def read(self, n: int = -1, /) -> bytes:
        # _ensure_open is outside the try: its read-after-close ValueError is the
        # wrapper's own (plain file semantics, not translated), and a lazy open failure
        # is already routed through _fail inside it.
        inner = self._ensure_open()
        try:
            return inner.read(n)
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
        if not self._seekable_hint:
            raise io.UnsupportedOperation("seek")
        inner = self._ensure_open()  # outside the try, same as read()
        try:
            before = inner.tell()
            result = inner.seek(offset, whence)
        except Exception as e:  # noqa: BLE001 - re-raised via the translator
            self._fail(e)
        self._maybe_warn_rewind(before, result)
        return result

    def _maybe_warn_rewind(self, before: int, after: int) -> None:
        """Emit once when a backward seek will re-decompress from the start (O(n))."""
        warning = self._rewind_warning
        if warning is None or self._rewind_warned or after >= before:
            return
        self._rewind_warned = True
        if warning.accelerator is not None:
            message = (
                f"Seeking backward in a {warning.codec_name} stream without a "
                f"random-access accelerator re-decompresses from the start "
                f"(O(n) per rewind). Install the 'seekable' extra "
                f"({warning.accelerator}) for indexed random access."
            )
        else:
            message = (
                f"Seeking backward in a {warning.codec_name} stream re-decompresses "
                f"from the start (O(n) per rewind): this codec has no random-access index."
            )
        resolve_collector(self._diagnostics_collector).emit(
            code=DiagnosticCode.STREAM_REWIND_REDECOMPRESSES,
            message=message,
            context=StreamRewindContext(
                codec=warning.codec_name,
                from_offset=before,
                to_offset=after,
                accelerator=warning.accelerator,
            ),
            logger=logger,
        )

    def tell(self, /) -> int:
        if self.closed:
            raise ValueError("I/O operation on closed file.")
        if self._inner is None:
            return 0
        return self._inner.tell()

    def seekable(self) -> bool:
        # readable()/writable()/write() come from ReadOnlyIOStream.
        # Undeclared SEEKABLE forces forward-only even when the inner handle could seek
        # (directory uniformity / declared-capabilities contract).
        if not self._seekable_hint:
            return False
        if self._inner is not None:
            return is_seekable(self._inner)
        return True

    def close(self) -> None:
        if self.closed:
            return
        # The finally ensures the wrapper is marked closed even when the inner close
        # raises (which is re-raised translated): a half-open wrapper would hand out
        # further reads on a dead stream, and a retried close() would fail again
        # instead of no-opping (the guard above makes it a no-op instead).
        # Catch ``Exception`` (not ``BaseException``): KeyboardInterrupt/SystemExit must
        # still propagate; dual-failure grouping is for ordinary close/teardown errors.
        close_exc: Exception | None = None
        try:
            if self._inner is not None:
                try:
                    self._inner.close()
                except Exception as e:  # noqa: BLE001 - re-raised via the translator
                    try:
                        self._fail(e)
                    except Exception as translated:  # noqa: BLE001 - may be ArchiveyError
                        close_exc = translated
        finally:
            super().close()
            self._detach_finalizer()
            on_close = self._on_close
            self._on_close = None
            teardown_exc: Exception | None = None
            if on_close is not None:
                try:
                    on_close()
                except Exception as e:  # noqa: BLE001 - combine with close failure below
                    teardown_exc = e
            if close_exc is not None and teardown_exc is not None:
                raise ExceptionGroup(
                    "member-stream close and archive teardown both failed",
                    [close_exc, teardown_exc],
                )
            if close_exc is not None:
                raise close_exc
            if teardown_exc is not None:
                raise teardown_exc

    def __repr__(self) -> str:
        return f"<ArchiveStream inner={self._inner!r}>"
