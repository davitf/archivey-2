"""Lifecycle diagnostic collector and emission path.

Owns exact counts, bounded retention (aggregate + member attachments), watermark-based
ranged snapshots, policy resolution, logging/callback delivery, and RAISE escalation.

Memory is bounded by design: the only lifetime-retained structure is ``_retained``, which
is capped at ``max_retained``. Exact counts are kept as a small fixed per-code ``Counter``,
and ranged/stream snapshots are computed by differencing a cheap :class:`DiagnosticWatermark`
(a sequence number plus a per-code count snapshot) against the live counters — so nothing
grows with the number of emitted diagnostics or opened streams.
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from archivey.diagnostics import (
    Diagnostic,
    DiagnosticCode,
    DiagnosticContext,
    DiagnosticDisposition,
    DiagnosticPolicy,
    DiagnosticSeverity,
    DiagnosticSummary,
    OnDiagnostic,
    validate_code_context,
)
from archivey.exceptions import DiagnosticRaisedError, UnsupportedOperationError

if TYPE_CHECKING:
    from archivey.config import ArchiveyConfig
    from archivey.types import ArchiveMember

_log = logging.getLogger("archivey.diagnostics")


@dataclass(frozen=True)
class DiagnosticWatermark:
    """Opaque collector position: the sequence number plus per-code counts at capture time.

    Creating one copies no diagnostics and costs no retention slots. Ranged snapshots
    difference two watermarks (or a watermark against "now"), so the collector needs no
    per-emission log to answer "what happened since here".
    """

    _sequence: int
    _total: int
    _counts: Mapping[DiagnosticCode, int]


@dataclass
class _RetainedEntry:
    sequence: int
    diagnostic: Diagnostic


class DiagnosticCollector:
    """One collector per detection / reader / top-level extract / standalone stream."""

    def __init__(
        self,
        *,
        policy: DiagnosticPolicy | None = None,
        max_retained: int = 256,
        on_diagnostic: OnDiagnostic | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        if max_retained < 0:
            raise ValueError("max_retained_diagnostic_references must be >= 0")
        self._policy = policy if policy is not None else DiagnosticPolicy()
        self._max_retained = max_retained
        self._on_diagnostic = on_diagnostic
        self._logger = logger if logger is not None else _log
        self._lock = threading.RLock()
        self._sequence = 0
        self._total_count = 0
        self._counts: Counter[DiagnosticCode] = Counter()
        self._retained: list[_RetainedEntry] = []
        self._slots_used = 0
        # Thread ids currently inside a delivery (log/callback) step. Keyed per thread so
        # legitimate concurrent emits on separate threads do not read as reentrancy, while
        # a callback re-entering emit on its own thread still trips the guard.
        self._emitting_threads: set[int] = set()

    @property
    def policy(self) -> DiagnosticPolicy:
        return self._policy

    @property
    def max_retained(self) -> int:
        return self._max_retained

    def watermark(self) -> DiagnosticWatermark:
        with self._lock:
            return DiagnosticWatermark(
                _sequence=self._sequence,
                _total=self._total_count,
                _counts=dict(self._counts),
            )

    def _summary_between(
        self,
        start_seq: int,
        start_total: int,
        start_counts: Mapping[DiagnosticCode, int],
        end_seq: int,
        end_total: int,
        end_counts: Mapping[DiagnosticCode, int],
    ) -> DiagnosticSummary:
        """Build a summary for the half-open range ``(start_seq, end_seq]``.

        Counts and totals are exact deltas of the two count snapshots; retained detail is
        filtered from the (bounded) retention list by sequence. The caller holds the lock.
        """
        total = end_total - start_total
        count_map: dict[DiagnosticCode, int] = {}
        for code, n in end_counts.items():
            delta = n - start_counts.get(code, 0)
            if delta:
                count_map[code] = delta
        retained = tuple(
            e.diagnostic for e in self._retained if start_seq < e.sequence <= end_seq
        )
        return DiagnosticSummary(
            total_count=total,
            counts=count_map,
            retained=retained,
            dropped_count=total - len(retained),
        )

    def snapshot(
        self, *, since: DiagnosticWatermark | None = None
    ) -> DiagnosticSummary:
        """Fresh immutable cumulative snapshot, or the delta since ``since``."""
        with self._lock:
            if since is None:
                return self._summary_between(
                    0, 0, {}, self._sequence, self._total_count, self._counts
                )
            return self._summary_between(
                since._sequence,
                since._total,
                since._counts,
                self._sequence,
                self._total_count,
                self._counts,
            )

    def emit(
        self,
        *,
        code: DiagnosticCode,
        message: str,
        context: DiagnosticContext,
        severity: DiagnosticSeverity = DiagnosticSeverity.WARNING,
        member: ArchiveMember | None = None,
        attach_to_member: bool = False,
        logger: logging.Logger | None = None,
        escalate_as: type[BaseException] | None = None,
        escalate_message: str | None = None,
        escalate_kwargs: dict[str, object] | None = None,
    ) -> Diagnostic:
        """Emit one diagnostic through the ordered policy matrix.

        Always increments exact counts. Under IGNORE, skips retention/log/callback.
        Under COLLECT/RAISE, retains (budget permitting), logs, and callbacks.
        Under RAISE (or when ``escalate_as`` is set), raises after delivery.

        ``escalate_as`` (e.g. :class:`~archivey.exceptions.TruncatedError` for strict
        EOF) takes precedence over :class:`DiagnosticRaisedError` for the terminal
        exception after the delivery steps that ran for the disposition.
        """
        validate_code_context(code, context)
        disposition = self._policy.resolve(code)
        log = logger if logger is not None else self._logger
        thread_id = threading.get_ident()

        with self._lock:
            if thread_id in self._emitting_threads:
                raise UnsupportedOperationError(
                    "Diagnostic callback/reentrancy: cannot drive another operation on "
                    "the same reader/stream while a diagnostic is being emitted."
                )
            self._sequence += 1
            sequence = self._sequence
            diagnostic = Diagnostic(
                occurrence_id=uuid.uuid4().hex,
                code=code,
                severity=severity,
                message=message,
                context=context,
            )
            self._total_count += 1
            self._counts[code] += 1

            retained_aggregate = False
            if disposition is not DiagnosticDisposition.IGNORE:
                if self._slots_used < self._max_retained:
                    self._retained.append(
                        _RetainedEntry(sequence=sequence, diagnostic=diagnostic)
                    )
                    self._slots_used += 1
                    retained_aggregate = True
                if (
                    retained_aggregate
                    and attach_to_member
                    and member is not None
                    and self._slots_used < self._max_retained
                ):
                    _attach_diagnostic(member, diagnostic)
                    self._slots_used += 1

            should_deliver = disposition is not DiagnosticDisposition.IGNORE
            should_raise_diagnostic = disposition is DiagnosticDisposition.RAISE
            if should_deliver:
                self._emitting_threads.add(thread_id)

        try:
            if should_deliver:
                log.warning("%s", message)
                if self._on_diagnostic is not None:
                    self._on_diagnostic(diagnostic)
            if escalate_as is not None:
                msg = escalate_message if escalate_message is not None else message
                kwargs = escalate_kwargs if escalate_kwargs is not None else {}
                raise escalate_as(msg, **kwargs)  # type: ignore[misc]
            if should_raise_diagnostic:
                raise DiagnosticRaisedError(message, diagnostic=diagnostic)
        finally:
            with self._lock:
                self._emitting_threads.discard(thread_id)

        return diagnostic


def _attach_diagnostic(member: ArchiveMember, diagnostic: Diagnostic) -> None:
    """Append a diagnostic to a member's attached tuple (library retention slot)."""
    current = member._diagnostics
    object.__setattr__(member, "_diagnostics", current + (diagnostic,))


def collector_from_config(config: ArchiveyConfig) -> DiagnosticCollector:
    """Build a collector from an :class:`~archivey.config.ArchiveyConfig`."""
    return DiagnosticCollector(
        policy=config.diagnostic_policy,
        max_retained=config.max_retained_diagnostic_references,
        on_diagnostic=config.on_diagnostic,
    )


def resolve_collector(collector: DiagnosticCollector | None) -> DiagnosticCollector:
    """Return ``collector``, or a throwaway COLLECT-default collector from library defaults.

    Used at emission sites that may not yet have a reader/stream-owned collector threaded
    through (standalone codec streams). Prefer passing the shared collector when available.
    """
    if collector is not None:
        return collector
    from archivey.config import DEFAULT_ARCHIVEY_CONFIG

    return collector_from_config(DEFAULT_ARCHIVEY_CONFIG)


__all__ = [
    "DiagnosticCollector",
    "DiagnosticWatermark",
    "collector_from_config",
    "resolve_collector",
]
