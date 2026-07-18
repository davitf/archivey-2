"""``ArchiveStream`` — wraps a member/codec stream and translates raw exceptions.

This is the carrier for the exception-translation contract (see ``error-handling`` and
CONTRIBUTING). Any exception raised while opening or reading the wrapped stream is routed
through a per-library *translator* (raw third-party exception → ``ArchiveyError`` subclass
or ``None`` to let it propagate), then *stamped* with format/archive/member context.

Member digest/length verification (formerly a separate ``VerifyingStream`` layer) is
optional state on this handle — see ``expected_hashes`` / ``expected_size`` — so a
member is served by one public stream that collapses nested codec ``ArchiveStream``s
and hashes/bounds in the same ``read()``.
"""

from __future__ import annotations

import io
import sys
import threading
import weakref
from dataclasses import dataclass
from typing import TYPE_CHECKING, BinaryIO, Callable, Mapping, NoReturn

from archivey.diagnostics import (
    DiagnosticCode,
    DiagnosticSummary,
    StreamRewindContext,
)
from archivey.exceptions import ArchiveyError, ArchiveyUsageError
from archivey.internal.diagnostics_collector import resolve_collector
from archivey.internal.logs import streams as logger
from archivey.internal.streams.streamtools import ReadOnlyIOStream, is_seekable
from archivey.internal.streams.verify import MemberVerifier, build_member_verifier
from archivey.types import HashAlgorithm

if TYPE_CHECKING:
    from _typeshed import WriteableBuffer

    from archivey.internal.diagnostics_collector import DiagnosticCollector
    from archivey.types import ArchiveMember

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
        expected_hashes: Mapping[HashAlgorithm, bytes] | None = None,
        expected_size: int | None = None,
        digest_transforms: Mapping[HashAlgorithm, Callable[[bytes], bytes]]
        | None = None,
        verify_member: ArchiveMember | None = None,
        archive_name: str | None = None,
        verifier: MemberVerifier | None = None,
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
        # Fused member verification (None when there is nothing to check). Prefer an
        # already-built ``verifier`` (collapse adoption); otherwise build from knobs.
        # ``expected_size`` here is the verify bound only — bare ``size=`` (fsspec
        # attribute) must not enable length checks on TAR/ISO/directory handles.
        if verifier is not None:
            self._verifier: MemberVerifier | None = verifier
        else:
            self._verifier = build_member_verifier(
                expected_hashes,
                expected_size=expected_size,
                collector=collector,
                member=verify_member,
                archive_name=archive_name,
                digest_transforms=digest_transforms,
            )
        self._finalizer: weakref.finalize | None = None
        if not lazy:
            self._ensure_open()

    def _attach_finalizer(self) -> None:
        """Safety-net finalizer: release the lease if the caller never closed us.

        Never raises; reports via ``sys.unraisablehook`` if release fails.

        Shutdown caveat: the release path takes ``ReaderState``'s lock. At interpreter
        exit, ``weakref.finalize``'s atexit hook runs while daemon threads are frozen —
        a daemon thread that died *inside* a reader-state critical section leaves the
        lock held forever and this finalizer would then hang shutdown. The window is a
        few bytecodes wide and requires daemon threads driving a reader at exit; noted
        so a future refactor doesn't widen it (e.g. by making the finalizer wait on a
        condition).

        Ordering note: this must be called AFTER ``_on_close`` is assigned — the
        callback captures ``self._on_close`` at attach time, not at fire time.
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
                    "Cannot open this member stream: it is already being opened by "
                    "another caller, or a previous open attempt failed. Concurrent "
                    "operations on a single stream object require caller synchronization."
                )
            open_fn = self._open_fn
            self._open_fn = None  # claim: only one caller proceeds to open_fn
        try:
            opened: BinaryIO = open_fn()
            # Lazy ``stream_members`` open_fn often returns another ``ArchiveStream``
            # (from ``_open_member`` → ``_wrap_member_stream``, or a codec stream under
            # that). Collapse here so the public handle is a single wrapper — but adopt
            # the nested translator/stamp/rewind_warning so codec errors stay typed.
            while isinstance(opened, ArchiveStream):
                opened = self._collapse_nested(opened)
        except Exception as e:  # noqa: BLE001 - re-raised via the translator
            # _open_fn stays None (claimed above) so a retry raises rather than
            # re-entering a half-open backend.
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

    def _collapse_nested(self, nested: ArchiveStream) -> BinaryIO:
        """Reduce a nested ``ArchiveStream`` to a bytes stream for this handle.

        If ``nested`` is still lazy, its opener is taken and invoked (this handle is
        opening *now*, so deferral transfers rather than being forced early). If it is
        already open, its inner is stolen. Either way the nested wrapper is neutralized
        and will not close the result.

        Codec streams (``open_codec_stream``) carry a library-specific translator;
        member wrappers often wrap those again. Adopting nested ``translate`` /
        ``stamp`` / ``rewind_warning`` keeps BadGzipFile / zlib.error / etc. typed
        after the collapse.
        """
        if nested.closed:
            raise ValueError("I/O operation on closed file.")

        nested_translate = nested._translate
        nested_stamp = nested._stamp
        outer_translate = self._translate
        outer_stamp = self._stamp

        def composed_translate(exc: Exception) -> ArchiveyError | None:
            translated = nested_translate(exc)
            if translated is not None:
                return translated
            return outer_translate(exc)

        def composed_stamp(err: ArchiveyError) -> None:
            nested_stamp(err)
            outer_stamp(err)

        self._translate = composed_translate
        self._stamp = composed_stamp
        if self._rewind_warning is None and nested._rewind_warning is not None:
            self._rewind_warning = nested._rewind_warning
        # Adopt fused verification from the nested member wrap (lazy stream_members
        # outer has none; the inner ``_open_member`` wrap carries the knobs).
        if self._verifier is None and nested._verifier is not None:
            self._verifier = nested._verifier
            nested._verifier = None

        with nested._open_lock:
            open_fn = nested._open_fn
            inner = nested._inner
            nested._open_fn = None
            nested._inner = None
        nested._detach_finalizer()
        nested._on_close = None
        # Mark closed without touching stolen opener/inner.
        super(ArchiveStream, nested).close()

        if inner is not None:
            return inner
        if open_fn is not None:
            opened = open_fn()
            while isinstance(opened, ArchiveStream):
                opened = self._collapse_nested(opened)
            return opened
        raise ArchiveyUsageError(
            "Cannot collapse nested ArchiveStream: it has no opener and no inner stream."
        )

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
        verifier = self._verifier
        try:
            if verifier is not None:
                return verifier.read(inner, n)
            return inner.read(n)
        except Exception as e:  # noqa: BLE001 - re-raised via the translator
            self._fail(e)

    def readinto(self, b: "WriteableBuffer", /) -> int:
        # When verifying, route through read() so digest/bounds stay consistent
        # (same contract as VerifyingStream / ReadOnlyIOStream.readinto).
        if self._verifier is not None:
            mv = memoryview(b).cast("B")
            data = self.read(len(mv))
            mv[: len(data)] = data
            return len(data)
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
        verifier = self._verifier
        if verifier is not None:
            verifier.note_seek(result)
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
            inner = self._inner
            verifier = self._verifier
            if inner is not None:
                try:
                    if verifier is not None:
                        # finish_on_close closes the inner (and runs deferred verify).
                        verifier.finish_on_close(inner)
                    else:
                        inner.close()
                except Exception as e:  # noqa: BLE001 - re-raised via the translator
                    try:
                        self._fail(e)
                    except Exception as translated:  # noqa: BLE001 - may be ArchiveyError
                        close_exc = translated
            # Never-opened lazy handle: skip verify (solid unread members must not
            # probe / force positioning).
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
