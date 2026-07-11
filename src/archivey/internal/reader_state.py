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
    _released: bool = field(default=False, repr=False)


class ReaderState:
    """Per-reader concurrency/lifecycle bookkeeping.

    Under the default (no ``CONCURRENT``) path this enforces the single-live-stream gate
    and exclusive single-owner operations without shared-handle locks. Declared
    ``CONCURRENT`` activates multi-stream admission while keeping materialization and
    reader-wide ops exclusive.

    Stream I/O (``read``/``seek``/``close`` on an already-open handle) does **not**
    consult the operation-owner gate — only leases + backend (D7).
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
        self.cache_state = CacheState.UNMATERIALIZED
        self.lifecycle = LifecycleState.OPEN
        self._root: OperationToken | None = None
        self._children: set[OperationToken] = set()
        self._workers: set[OperationToken] = set()
        self._live_streams: set[int] = set()
        self._lease_count = 1  # reader itself holds one lease until close
        self._teardown_claimed = False
        self._teardown_error: BaseException | None = None
        self._gate_exempt_depth = 0
        self._internal_open_depth = 0

    @property
    def concurrent(self) -> bool:
        return MemberStreams.CONCURRENT in self.member_streams

    @property
    def seekable(self) -> bool:
        return MemberStreams.SEEKABLE in self.member_streams

    def require_open(self, op: str) -> None:
        with self._lock:
            if self.lifecycle is not LifecycleState.OPEN:
                raise ArchiveyUsageError(
                    f"{op} is not available after the archive reader has been closed."
                )

    def current_root(self) -> OperationToken | None:
        with self._lock:
            return self._root

    def begin_internal_opens(self) -> None:
        with self._lock:
            self._internal_open_depth += 1
            self._gate_exempt_depth += 1

    def end_internal_opens(self) -> None:
        with self._lock:
            self._internal_open_depth = max(0, self._internal_open_depth - 1)
            self._gate_exempt_depth = max(0, self._gate_exempt_depth - 1)

    def acquire_pass(self, name: str) -> OperationToken:
        """Acquire a data-pass token: root when free, else a child under an internal owner."""
        with self._lock:
            self._require_lifecycle_open_locked(name)
            if self._root is not None and self._internal_open_depth > 0:
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
            self._require_lifecycle_open_locked(name)
            if self._root is not None and self._internal_open_depth == 0:
                raise ArchiveyUsageError(
                    f"Cannot call {name!r}: another reader operation "
                    f"({self._root.name!r}) is already active."
                )
            # Under an internal library owner (extract_all), worker opens are admitted as
            # children of that root.
            if self._root is not None and self._internal_open_depth > 0:
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

    def begin_materialization(self) -> None:
        with self._lock:
            self._require_lifecycle_open_locked("materialization")
            if self.cache_state is CacheState.MATERIALIZED:
                return
            if self.cache_state is CacheState.MATERIALIZING:
                raise ArchiveyUsageError(
                    "Cannot start materialization: another materialization is already "
                    "in progress."
                )
            self.cache_state = CacheState.MATERIALIZING

    def complete_materialization(self) -> None:
        with self._lock:
            self.cache_state = CacheState.MATERIALIZED

    def fail_materialization(self) -> None:
        with self._lock:
            if self.cache_state is CacheState.MATERIALIZING:
                self.cache_state = CacheState.UNMATERIALIZED

    def acquire_live_stream(self, stream: ArchiveStream) -> None:
        """Register a public member stream; enforce the single-live-stream gate."""
        with self._lock:
            self._require_lifecycle_open_locked("open()")
            if self.concurrent or self._gate_exempt_depth > 0:
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
        """Mark reader closed and release the reader lease. True → run teardown now."""
        with self._lock:
            if self.lifecycle is not LifecycleState.OPEN:
                return False
            if self._root is not None:
                raise ArchiveyUsageError(
                    "Cannot close the archive reader while another reader operation "
                    f"({self._root.name!r}) is active."
                )
            if self._workers:
                raise ArchiveyUsageError(
                    "Cannot close the archive reader while an open()/read() call is "
                    "still in progress."
                )
            self.lifecycle = LifecycleState.READER_CLOSED
            return self._release_lease_locked()

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

    def complete_teardown(self, error: BaseException | None = None) -> None:
        with self._lock:
            if error is not None and self._teardown_error is None:
                self._teardown_error = error
            self.lifecycle = LifecycleState.TEARDOWN_COMPLETE

    def take_teardown_error(self) -> BaseException | None:
        with self._lock:
            err = self._teardown_error
            self._teardown_error = None
            return err

    def _release_lease_locked(self) -> bool:
        if self._lease_count > 0:
            self._lease_count -= 1
        return (
            self.lifecycle is LifecycleState.READER_CLOSED
            and self._lease_count == 0
            and not self._teardown_claimed
        )

    def _require_lifecycle_open_locked(self, op: str) -> None:
        if self.lifecycle is not LifecycleState.OPEN:
            raise ArchiveyUsageError(
                f"{op} is not available after the archive reader has been closed."
            )
