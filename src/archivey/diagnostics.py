"""Public diagnostic value types — structured advisories as queryable data.

See ``openspec/specs/diagnostics`` (and the ``diagnostics-warnings-as-data`` change)
for the lifecycle, retention, and policy contracts.
"""

from __future__ import annotations

import base64
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Literal, TypeVar

from archivey.internal.extraction_types import ExtractionResult

_K = TypeVar("_K")
_V = TypeVar("_V")


def _freeze_mapping(mapping: Mapping[_K, _V] | None) -> Mapping[_K, _V]:
    """Defensive copy into an immutable mapping proxy."""
    if mapping is None:
        return MappingProxyType({})
    return MappingProxyType(dict(mapping))


class DiagnosticCode(str, Enum):
    """Stable machine codes for advisory events."""

    MEMBER_NAME_NORMALIZED = "member_name_normalized"
    FORMAT_EXTENSION_CONFLICT = "format_extension_conflict"
    SCAN_DIRECTORY_VANISHED = "scan_directory_vanished"
    SCAN_ENTRY_VANISHED = "scan_entry_vanished"
    ARCHIVE_EOF_MARKER_MISSING = "archive_eof_marker_missing"
    MEMBER_TIMESTAMP_INVALID = "member_timestamp_invalid"
    SYMLINK_TARGET_UNAVAILABLE = "symlink_target_unavailable"
    DIGEST_UNVERIFIABLE = "digest_unverifiable"
    SEEK_INDEX_DEGRADED = "seek_index_degraded"
    STREAM_REWIND_REDECOMPRESSES = "stream_rewind_redecompresses"
    EXTRACTION_MEMBER_REJECTED = "extraction_member_rejected"
    EXTRACTION_MEMBER_FAILED = "extraction_member_failed"


class DiagnosticSeverity(str, Enum):
    """Severity axis on a diagnostic record.

    Only ``WARNING`` is used initially; the axis remains so a later informational
    taxonomy does not require changing the value shape.
    """

    WARNING = "warning"


class DiagnosticDisposition(str, Enum):
    """Per-code policy disposition for an emitted diagnostic."""

    IGNORE = "ignore"
    COLLECT = "collect"
    RAISE = "raise"


@dataclass(frozen=True)
class NameNormalizationContext:
    kind: Literal["name_normalization"] = "name_normalization"
    archive_name: str | None = None
    member_name: str = ""
    member_id: int | None = None
    raw_name_base64: str | None = None
    presented_name: str = ""
    normalized_name: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "archive_name": self.archive_name,
            "member_name": self.member_name,
            "member_id": self.member_id,
            "raw_name_base64": self.raw_name_base64,
            "presented_name": self.presented_name,
            "normalized_name": self.normalized_name,
        }


@dataclass(frozen=True)
class FormatConflictContext:
    kind: Literal["format_conflict"] = "format_conflict"
    source_name: str | None = None
    extension: str | None = None
    extension_format: str = ""
    detected_format: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "source_name": self.source_name,
            "extension": self.extension,
            "extension_format": self.extension_format,
            "detected_format": self.detected_format,
        }


@dataclass(frozen=True)
class ScanRaceContext:
    kind: Literal["scan_race"] = "scan_race"
    archive_name: str | None = None
    relative_path: str = ""
    entry_kind: Literal["directory", "entry"] = "entry"

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "archive_name": self.archive_name,
            "relative_path": self.relative_path,
            "entry_kind": self.entry_kind,
        }


@dataclass(frozen=True)
class ArchiveEofContext:
    kind: Literal["archive_eof"] = "archive_eof"
    archive_name: str | None = None
    format: str = ""
    expected_marker: str = ""
    expected_bytes: int = 0
    observed_bytes: int = 0
    observed_kind: Literal["absent", "short", "nonzero"] = "absent"

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "archive_name": self.archive_name,
            "format": self.format,
            "expected_marker": self.expected_marker,
            "expected_bytes": self.expected_bytes,
            "observed_bytes": self.observed_bytes,
            "observed_kind": self.observed_kind,
        }


@dataclass(frozen=True)
class MemberTimestampContext:
    kind: Literal["member_timestamp"] = "member_timestamp"
    archive_name: str | None = None
    member_name: str = ""
    member_id: int | None = None
    field: str = ""
    source: str = ""
    value_repr: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "archive_name": self.archive_name,
            "member_name": self.member_name,
            "member_id": self.member_id,
            "field": self.field,
            "source": self.source,
            "value_repr": self.value_repr,
        }


@dataclass(frozen=True)
class SymlinkTargetContext:
    kind: Literal["symlink_target"] = "symlink_target"
    archive_name: str | None = None
    member_name: str = ""
    member_id: int | None = None
    reason: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "archive_name": self.archive_name,
            "member_name": self.member_name,
            "member_id": self.member_id,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class DigestContext:
    kind: Literal["digest"] = "digest"
    archive_name: str | None = None
    member_name: str = ""
    member_id: int | None = None
    algorithm: str = ""
    reason: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "archive_name": self.archive_name,
            "member_name": self.member_name,
            "member_id": self.member_id,
            "algorithm": self.algorithm,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class SeekIndexContext:
    kind: Literal["seek_index"] = "seek_index"
    archive_name: str | None = None
    member_name: str | None = None
    member_id: int | None = None
    codec: str = ""
    scan: str = ""
    error_type: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "archive_name": self.archive_name,
            "member_name": self.member_name,
            "member_id": self.member_id,
            "codec": self.codec,
            "scan": self.scan,
            "error_type": self.error_type,
        }


@dataclass(frozen=True)
class StreamRewindContext:
    kind: Literal["stream_rewind"] = "stream_rewind"
    archive_name: str | None = None
    member_name: str | None = None
    member_id: int | None = None
    codec: str = ""
    from_offset: int = 0
    to_offset: int = 0
    accelerator: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "archive_name": self.archive_name,
            "member_name": self.member_name,
            "member_id": self.member_id,
            "codec": self.codec,
            "from_offset": self.from_offset,
            "to_offset": self.to_offset,
            "accelerator": self.accelerator,
        }


@dataclass(frozen=True)
class ExtractionOutcomeContext:
    kind: Literal["extraction_outcome"] = "extraction_outcome"
    archive_name: str | None = None
    member_name: str = ""
    member_id: int | None = None
    status: Literal["rejected", "failed"] = "failed"
    error_type: str = ""
    failure_group_id: str | None = None
    failure_group_size: int | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "archive_name": self.archive_name,
            "member_name": self.member_name,
            "member_id": self.member_id,
            "status": self.status,
            "error_type": self.error_type,
            "failure_group_id": self.failure_group_id,
            "failure_group_size": self.failure_group_size,
        }


DiagnosticContext = (
    NameNormalizationContext
    | FormatConflictContext
    | ScanRaceContext
    | ArchiveEofContext
    | MemberTimestampContext
    | SymlinkTargetContext
    | DigestContext
    | SeekIndexContext
    | StreamRewindContext
    | ExtractionOutcomeContext
)

_CODE_CONTEXT_KINDS: Mapping[DiagnosticCode, str] = MappingProxyType(
    {
        DiagnosticCode.MEMBER_NAME_NORMALIZED: "name_normalization",
        DiagnosticCode.FORMAT_EXTENSION_CONFLICT: "format_conflict",
        DiagnosticCode.SCAN_DIRECTORY_VANISHED: "scan_race",
        DiagnosticCode.SCAN_ENTRY_VANISHED: "scan_race",
        DiagnosticCode.ARCHIVE_EOF_MARKER_MISSING: "archive_eof",
        DiagnosticCode.MEMBER_TIMESTAMP_INVALID: "member_timestamp",
        DiagnosticCode.SYMLINK_TARGET_UNAVAILABLE: "symlink_target",
        DiagnosticCode.DIGEST_UNVERIFIABLE: "digest",
        DiagnosticCode.SEEK_INDEX_DEGRADED: "seek_index",
        DiagnosticCode.STREAM_REWIND_REDECOMPRESSES: "stream_rewind",
        DiagnosticCode.EXTRACTION_MEMBER_REJECTED: "extraction_outcome",
        DiagnosticCode.EXTRACTION_MEMBER_FAILED: "extraction_outcome",
    }
)


def validate_code_context(code: DiagnosticCode, context: DiagnosticContext) -> None:
    """Reject unregistered or mismatched code→context pairings."""
    expected = _CODE_CONTEXT_KINDS.get(code)
    if expected is None:
        raise ValueError(f"Unknown diagnostic code: {code!r}")
    if context.kind != expected:
        raise ValueError(
            f"Diagnostic code {code.value!r} requires context kind {expected!r}, "
            f"got {context.kind!r}"
        )
    if code is DiagnosticCode.SCAN_DIRECTORY_VANISHED and (
        not isinstance(context, ScanRaceContext) or context.entry_kind != "directory"
    ):
        raise ValueError("SCAN_DIRECTORY_VANISHED requires entry_kind='directory'")
    if code is DiagnosticCode.SCAN_ENTRY_VANISHED and (
        not isinstance(context, ScanRaceContext) or context.entry_kind != "entry"
    ):
        raise ValueError("SCAN_ENTRY_VANISHED requires entry_kind='entry'")
    if code is DiagnosticCode.EXTRACTION_MEMBER_REJECTED and (
        not isinstance(context, ExtractionOutcomeContext) or context.status != "rejected"
    ):
        raise ValueError("EXTRACTION_MEMBER_REJECTED requires status='rejected'")
    if code is DiagnosticCode.EXTRACTION_MEMBER_FAILED and (
        not isinstance(context, ExtractionOutcomeContext) or context.status != "failed"
    ):
        raise ValueError("EXTRACTION_MEMBER_FAILED requires status='failed'")
    if isinstance(context, ExtractionOutcomeContext):
        group_id, group_size = context.failure_group_id, context.failure_group_size
        if (group_id is None) ^ (group_size is None):
            raise ValueError(
                "failure_group_id and failure_group_size must both be set or both None"
            )


@dataclass(frozen=True)
class Diagnostic:
    """One immutable advisory occurrence."""

    occurrence_id: str
    code: DiagnosticCode
    severity: DiagnosticSeverity
    message: str
    context: DiagnosticContext

    def to_dict(self) -> dict[str, object]:
        return {
            "occurrence_id": self.occurrence_id,
            "code": self.code.value,
            "severity": self.severity.value,
            "message": self.message,
            "context": self.context.to_dict(),
        }


@dataclass(frozen=True)
class DiagnosticSummary:
    """Immutable point-in-time snapshot of diagnostic counts and retained detail."""

    total_count: int
    counts: Mapping[DiagnosticCode, int] = field(default_factory=lambda: MappingProxyType({}))
    retained: tuple[Diagnostic, ...] = ()
    dropped_count: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "counts", _freeze_mapping(self.counts))

    @staticmethod
    def empty() -> DiagnosticSummary:
        return DiagnosticSummary(total_count=0, counts={}, retained=(), dropped_count=0)


@dataclass(frozen=True)
class DiagnosticPolicy:
    """Per-code disposition policy; matching is by code only."""

    default: DiagnosticDisposition = DiagnosticDisposition.COLLECT
    overrides: Mapping[DiagnosticCode, DiagnosticDisposition] = field(
        default_factory=lambda: MappingProxyType({})
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "overrides", _freeze_mapping(self.overrides))

    def resolve(self, code: DiagnosticCode) -> DiagnosticDisposition:
        return self.overrides.get(code, self.default)


@dataclass(frozen=True)
class ExtractionReport:
    """Immutable extraction outcome: fixed result tuple plus diagnostic summary.

    ``results`` is a frozen outcome structure. Each :class:`ExtractionResult` is frozen,
    but ``ExtractionResult.member`` refers to the live mutable :class:`ArchiveMember`
    (caller-read-only), whose late-bound metadata and member diagnostics may still be
    filled in place.
    """

    results: tuple[ExtractionResult, ...]
    diagnostics: DiagnosticSummary


OnDiagnostic = Callable[[Diagnostic], None]
"""Optional synchronous callback invoked for COLLECT/RAISE diagnostics."""


def raw_name_to_base64(raw_name: bytes | None) -> str | None:
    """Encode raw archive name bytes for a JSON-safe diagnostic context field."""
    if raw_name is None:
        return None
    return base64.b64encode(raw_name).decode("ascii")


def format_path_name(path: str | Path | None) -> str | None:
    """Stringify a source path for diagnostic context without retaining Path objects."""
    if path is None:
        return None
    return str(path)


__all__ = [
    "ArchiveEofContext",
    "Diagnostic",
    "DiagnosticCode",
    "DiagnosticContext",
    "DiagnosticDisposition",
    "DiagnosticPolicy",
    "DiagnosticSeverity",
    "DiagnosticSummary",
    "DigestContext",
    "ExtractionOutcomeContext",
    "ExtractionReport",
    "FormatConflictContext",
    "MemberTimestampContext",
    "NameNormalizationContext",
    "OnDiagnostic",
    "ScanRaceContext",
    "SeekIndexContext",
    "StreamRewindContext",
    "SymlinkTargetContext",
    "format_path_name",
    "raw_name_to_base64",
    "validate_code_context",
]
