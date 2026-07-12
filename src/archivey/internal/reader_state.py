"""Operation ownership, live-stream gate, and lifecycle leases for readers."""

from __future__ import annotations

import enum
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from archivey.exceptions import ArchiveyUsageError, ConcurrentAccessError
from archivey.types import MemberStreams

if TYPE_CHECKING:
    from archivey.internal.open_site import OpenSite
    from archivey.internal.streams.archive_stream import ArchiveStream


class CacheState(enum.Enum):
    UNMATERIALIZED = "unmaterialized"
    MATERIALIZING = "materializing"
    MATERIALIZED = "materialized"


class LifecycleState(enum.Enum):
    OPEN = "open"
    READER_CLOSED = "reader_closed"
    TEARDOWN_RUNNING = "teardown_running"
    TEARDOWN_COMPLETE = "teardown_complete"


@dataclass(eq=False)
class OperationToken:
    """Unforgeable root, child, or short-lived worker operation-owner token."""

    name: str
    parent: OperationToken | None = None
    kind: str = "root"  # "root" | "child" | "worker"
    # The thread that acquired the token. Used to detect same-thread re-entry (a
    # diagnostic/progress callback driving the reader that is mid-operation on this very
    # thread), which must raise a usage error instead of deadlocking on a wait for itself.
    thread_id: int = field(default_factory=threading.get_ident, repr=False)
    _released: bool = field(default=False, repr=False)


class ReaderState:
    """Per-reader concurrency/lifecycle bookkeeping.

    Under the default (no ``CONCURRENT``) path this enforces the single-live-stream gate
    and exclusive single-owner operations without shared-handle locks. Declared
    ``CONCURRENT`` activates multi-stream admission and coordinates first-touch
    materialization (wait/share) plus draining ``close()`` (wait for in-flight worker
    calls). Distinct reader-wide passes and same-stream access remain single-owner /
    caller-synchronized.
    """

    def __init__(
        self,
        *,
        member_streams: MemberStreams,
        open_site: OpenSite | None,
    ) -> None:
        self.member_streams = member_streams
        self.open_site = open_site
        self._lock = threading.RLock()
        self._materialization_cv = threading.Condition(self._lock)
        self._workers_cv = threading.Condition(self._lock)
        self._close_cv = threading.Condition(self._lock)
        self.cache_state = CacheState.UNMATERIALIZED
        self.lifecycle = LifecycleState.OPEN
        self._closing = False
        self._root: OperationToken | None = None
        self._children: set[OperationToken] = set()
        self._workers: set[OperationToken] = set()
        self._live_streams: set[int] = set()
        self._lease_count = 1  # reader itself holds one lease until close
        self._teardown_claimed = False
        # Library-internal open windows (extract_all's coordinator, first-touch link
        # reads), keyed BY THREAD: the exemption from the live-stream gate and from
        # worker rejection applies only to the thread that entered the window. A plain
        # reader-wide depth counter would silently admit a *foreign* thread's open()
        # during extract_all on a non-CONCURRENT reader — and on backends without a
        # shared-handle lock the interleaved seek+read pairs then serve wrong member
        # bytes instead of the loud error this gate exists to raise (deep review N3).
        self._internal_open_threads: dict[int, int] = {}
        # The thread that currently owns the materialization election, for same-thread
        # re-entry detection (deep review N4a).
        self._materializing_thread: int | None = None

    @property
    def concurrent(self) -> bool:
        return MemberStreams.CONCURRENT in self.member_streams

    @property
    def seekable(self) -> bool:
        return MemberStreams.SEEKABLE in self.member_streams

    def require_open(self, op: str) -> None:
        with self._lock:
            self._require_admissible_locked(op)

    def current_root(self) -> OperationToken | None:
        with self._lock:
            return self._root

    def begin_internal_opens(self) -> None:
        tid = threading.get_ident()
        with self._lock:
            self._internal_open_threads[tid] = (
                self._internal_open_threads.get(tid, 0) + 1
            )

    def end_internal_opens(self) -> None:
        tid = threading.get_ident()
        with self._lock:
            depth = self._internal_open_threads.get(tid, 0) - 1
            if depth > 0:
                self._internal_open_threads[tid] = depth
            else:
                self._internal_open_threads.pop(tid, None)

    def _internal_opens_active_locked(self) -> bool:
        """Whether the CURRENT thread is inside a library-internal open window."""
        return self._internal_open_threads.get(threading.get_ident(), 0) > 0

    def acquire_pass(self, name: str) -> OperationToken:
        """Acquire a data-pass token: root when free, else a child under an internal owner."""
        with self._lock:
            self._require_admissible_locked(name)
            if self._root is not None and self._internal_opens_active_locked():
                child = OperationToken(name=name, parent=self._root, kind="child")
                self._children.add(child)
                return child
            if self._root is not None:
                raise ArchiveyUsageError(
                    f"Cannot start {name!r}: another reader operation "
                    f"({self._root.name!r}) is already active."
                )
            if self._workers:
                raise ArchiveyUsageError(
                    f"Cannot start {name!r}: a concurrent open()/read() call is still "
                    "in progress."
                )
            token = OperationToken(name=name, kind="root")
            self._root = token
            return token

    def release_pass(self, token: OperationToken) -> None:
        with self._lock:
            if token._released:
                return
            token._released = True
            if token.parent is not None or token.kind == "child":
                self._children.discard(token)
                return
            if self._root is token:
                self._root = None
            self._children.clear()

    def enter_child(self, root: OperationToken, name: str) -> OperationToken:
        with self._lock:
            if root._released or self._root is not root:
                raise ArchiveyUsageError(
                    f"Cannot enter child scope {name!r}: root operation is not active."
                )
            child = OperationToken(name=name, parent=root, kind="child")
            self._children.add(child)
            return child

    def release_child(self, child: OperationToken) -> None:
        with self._lock:
            if child._released:
                return
            child._released = True
            self._children.discard(child)

    def acquire_worker(self, name: str) -> OperationToken:
        """Short-lived token for random ``open()`` / ``read()`` (D7).

        Rejected while a reader-wide root pass is active (unless under library-internal
        opens). Idle open streams (leases only) do not block workers. Stream I/O after
        this token is released does not re-check the gate.
        """
        with self._lock:
            self._require_admissible_locked(name)
            internal = self._internal_opens_active_locked()
            if self._root is not None and not internal:
                raise ArchiveyUsageError(
                    f"Cannot call {name!r}: another reader operation "
                    f"({self._root.name!r}) is already active."
                )
            # Under an internal library owner (extract_all), the OWNING THREAD's worker
            # opens are admitted as children of that root; other threads are rejected
            # above like during any root pass.
            if self._root is not None and internal:
                token = OperationToken(name=name, parent=self._root, kind="child")
                self._children.add(token)
                return token
            token = OperationToken(name=name, kind="worker")
            self._workers.add(token)
            return token

    def release_worker(self, token: OperationToken) -> None:
        with self._lock:
            if token._released:
                return
            token._released = True
            if token.kind == "child" or token.parent is not None:
                self._children.discard(token)
                return
            self._workers.discard(token)
            if not self._workers:
                self._workers_cv.notify_all()

    def begin_materialization(self) -> bool:
        """Elect a materialization owner or wait for a published snapshot.

        Returns ``True`` if the caller must perform materialization (and later call
        :meth:`complete_materialization` or :meth:`fail_materialization`). Returns
        ``False`` if the immutable snapshot is already published (including after
        waiting under ``CONCURRENT``).

        Under ``MemberStreams.CONCURRENT``, a second caller that overlaps an in-progress
        materialization blocks on a condition until the snapshot is published or the
        attempt fails (then re-elects). Without ``CONCURRENT``, overlapping materialization
        still raises :class:`~archivey.exceptions.ArchiveyUsageError`. Uncontended paths
        take the elect-and-run branch with no wait.
        """
        with self._lock:
            while True:
                self._require_admissible_locked("materialization")
                if self.cache_state is CacheState.MATERIALIZED:
                    return False
                if self.cache_state is CacheState.MATERIALIZING:
                    if self._materializing_thread == threading.get_ident():
                        # Same-thread re-entry: a diagnostic/progress callback fired
                        # during materialization called back into the reader. Waiting
                        # would deadlock this thread on a notify only it can send
                        # (deep review N4a); a non-CONCURRENT reader would raise the
                        # generic message below, which misdiagnoses the situation.
                        raise ArchiveyUsageError(
                            "Reader re-entered while it is materializing its member "
                            "list on this same thread (e.g. from a diagnostic or "
                            "progress callback). Callbacks must not call back into "
                            "the reader that triggered them."
                        )
                    if not self.concurrent:
                        raise ArchiveyUsageError(
                            "Cannot start materialization: another materialization is "
                            "already in progress."
                        )
                    self._materialization_cv.wait()
                    continue
                # UNMATERIALIZED — elect this caller.
                self.cache_state = CacheState.MATERIALIZING
                self._materializing_thread = threading.get_ident()
                return True

    def complete_materialization(self) -> None:
        with self._lock:
            self.cache_state = CacheState.MATERIALIZED
            self._materializing_thread = None
            self._materialization_cv.notify_all()

    def fail_materialization(self) -> None:
        with self._lock:
            if self.cache_state is CacheState.MATERIALIZING:
                self.cache_state = CacheState.UNMATERIALIZED
                self._materializing_thread = None
                self._materialization_cv.notify_all()

    def acquire_live_stream(self, stream: ArchiveStream) -> None:
        """Register a public member stream; enforce the single-live-stream gate."""
        with self._lock:
            self._require_lifecycle_open_locked("open()")
            if self.concurrent or self._internal_opens_active_locked():
                self._live_streams.add(id(stream))
                self._lease_count += 1
                return
            if self._live_streams:
                site = self.open_site
                loc = site.location if site is not None else "<unknown>"
                raise ConcurrentAccessError(
                    "A member stream is already open on this reader. Close it before "
                    "opening another, or reopen the archive with "
                    f"member_streams=MemberStreams.CONCURRENT "
                    f"(this archive was opened without MemberStreams.CONCURRENT at {loc})."
                )
            self._live_streams.add(id(stream))
            self._lease_count += 1

    def release_live_stream(self, stream: ArchiveStream) -> bool:
        """Release a stream lease. Returns True if the caller should run teardown."""
        with self._lock:
            sid = id(stream)
            if sid not in self._live_streams:
                return False
            self._live_streams.discard(sid)
            return self._release_lease_locked()

    def mark_reader_closed(self) -> bool:
        """Mark reader closed and release the reader lease. True → run teardown now.

        Under ``MemberStreams.CONCURRENT``, blocks until in-flight worker ``open()`` /
        ``read()`` / ``get()`` / ``members()`` calls return, then transitions to closed.
        Escaped idle member streams keep their lifecycle leases and do not block close.
        A worker that never returns is a caller bug (same as any lock); there is no
        artificial timeout.

        Without ``CONCURRENT``, overlapping worker calls still raise
        :class:`~archivey.exceptions.ArchiveyUsageError`. Concurrent double-``close()`` is
        idempotent: one thread drains and closes; others wait for that transition and
        return without running teardown again.
        """
        with self._lock:
            # Another closer is draining, or close already finished.
            if self._closing or self.lifecycle is not LifecycleState.OPEN:
                while self.lifecycle is LifecycleState.OPEN and self._closing:
                    self._close_cv.wait()
                return False
            if self._root is not None:
                raise ArchiveyUsageError(
                    "Cannot close the archive reader while another reader operation "
                    f"({self._root.name!r}) is active."
                )
            if self._workers and not self.concurrent:
                raise ArchiveyUsageError(
                    "Cannot close the archive reader while an open()/read() call is "
                    "still in progress."
                )
            tid = threading.get_ident()
            if any(worker.thread_id == tid for worker in self._workers):
                # This thread is closing from INSIDE one of its own worker calls (a
                # diagnostic/progress callback calling close()). Draining would wait
                # forever for a worker that cannot return until close() does (deep
                # review N4b).
                raise ArchiveyUsageError(
                    "Cannot close the archive reader from inside one of its own "
                    "open()/read()/members() calls (e.g. from a diagnostic or "
                    "progress callback). Callbacks must not close the reader that "
                    "triggered them."
                )
            self._closing = True
            try:
                while self._workers:
                    self._workers_cv.wait()
                self.lifecycle = LifecycleState.READER_CLOSED
                self._close_cv.notify_all()
                return self._release_lease_locked()
            except BaseException:
                self._closing = False
                self._close_cv.notify_all()
                raise

    def claim_teardown(self) -> bool:
        with self._lock:
            if self._teardown_claimed:
                return False
            if self.lifecycle not in (
                LifecycleState.READER_CLOSED,
                LifecycleState.TEARDOWN_RUNNING,
            ):
                return False
            if self._lease_count > 0:
                return False
            self._teardown_claimed = True
            self.lifecycle = LifecycleState.TEARDOWN_RUNNING
            return True

    def complete_teardown(self) -> None:
        """Mark teardown finished. A teardown failure is the caller's to propagate
        (``_maybe_teardown`` raises it, alone or in an ``ExceptionGroup``); the state
        machine keeps no copy — a stored-but-never-surfaced error would be a silent
        swallow path."""
        with self._lock:
            self.lifecycle = LifecycleState.TEARDOWN_COMPLETE

    def _release_lease_locked(self) -> bool:
        if self._lease_count > 0:
            self._lease_count -= 1
        return (
            self.lifecycle is LifecycleState.READER_CLOSED
            and self._lease_count == 0
            and not self._teardown_claimed
        )

    def _require_lifecycle_open_locked(self, op: str) -> None:
        """Lifecycle is still OPEN (including during draining close)."""
        if self.lifecycle is not LifecycleState.OPEN:
            raise ArchiveyUsageError(
                f"{op} is not available after the archive reader has been closed."
            )

    def _require_admissible_locked(self, op: str) -> None:
        """Reject new public admissions after close started or finished."""
        if self.lifecycle is not LifecycleState.OPEN or self._closing:
            raise ArchiveyUsageError(
                f"{op} is not available after the archive reader has been closed."
            )
