"""Lifecycle diagnostic collector and emission path.

Owns exact counts, bounded retention (aggregate + member attachments), operation
watermarks, policy resolution, logging/callback delivery, and RAISE escalation.
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections import Counter
from dataclasses import dataclass
from types import MappingProxyType
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
    """Opaque collector position; creating one copies no diagnostics and costs no slots."""

    _sequence: int


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
        self._emitting = False
        # Every emission: (sequence, code) for exact ranged counts.
        self._emission_log: list[tuple[int, DiagnosticCode]] = []
        # Named operation scopes for stream-filtered snapshots.
        self._operations: dict[str, tuple[int, int | None]] = {}

    @property
    def policy(self) -> DiagnosticPolicy:
        return self._policy

    @property
    def max_retained(self) -> int:
        return self._max_retained

    def watermark(self) -> DiagnosticWatermark:
        with self._lock:
            return DiagnosticWatermark(_sequence=self._sequence)

    def begin_operation(self, operation_id: str) -> DiagnosticWatermark:
        """Mark the start of a named operation (e.g. a stream open) for filtered views."""
        with self._lock:
            wm = DiagnosticWatermark(_sequence=self._sequence)
            self._operations[operation_id] = (self._sequence, None)
            return wm

    def end_operation(self, operation_id: str) -> None:
        with self._lock:
            start_end = self._operations.get(operation_id)
            if start_end is None:
                return
            start, _ = start_end
            self._operations[operation_id] = (start, self._sequence)

    def snapshot(self, *, since: DiagnosticWatermark | None = None) -> DiagnosticSummary:
        """Fresh immutable cumulative (or ranged) snapshot."""
        with self._lock:
            if since is None:
                total = self._total_count
                counts = MappingProxyType(dict(self._counts))
                retained = tuple(e.diagnostic for e in self._retained)
                return DiagnosticSummary(
                    total_count=total,
                    counts=counts,
                    retained=retained,
                    dropped_count=total - len(retained),
                )

            start = since._sequence
            ranged = [(seq, code) for seq, code in self._emission_log if seq > start]
            count_map: Counter[DiagnosticCode] = Counter()
            for _, code in ranged:
                count_map[code] += 1
            retained = tuple(
                e.diagnostic for e in self._retained if e.sequence > start
            )
            total = len(ranged)
            return DiagnosticSummary(
                total_count=total,
                counts=MappingProxyType(dict(count_map)),
                retained=retained,
                dropped_count=total - len(retained),
            )

    def snapshot_operation(self, operation_id: str) -> DiagnosticSummary:
        with self._lock:
            bounds = self._operations.get(operation_id)
            if bounds is None:
                return DiagnosticSummary.empty()
            start, end = bounds
            end_seq = end if end is not None else self._sequence
            ranged = [
                (seq, code)
                for seq, code in self._emission_log
                if start < seq <= end_seq
            ]
            count_map: Counter[DiagnosticCode] = Counter()
            for _, code in ranged:
                count_map[code] += 1
            retained = tuple(
                e.diagnostic
                for e in self._retained
                if start < e.sequence <= end_seq
            )
            total = len(ranged)
            return DiagnosticSummary(
                total_count=total,
                counts=MappingProxyType(dict(count_map)),
                retained=retained,
                dropped_count=total - len(retained),
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

        with self._lock:
            if self._emitting:
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
            self._emission_log.append((sequence, code))

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
                self._emitting = True

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
                self._emitting = False

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
