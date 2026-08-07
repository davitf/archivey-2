"""Public diagnostic value types — structured advisories as queryable data.

See ``openspec/specs/diagnostics`` (and the ``diagnostics-warnings-as-data`` change)
for the lifecycle, retention, and policy contracts.

Layout of this module:

1. **Codes / severity / disposition** — stable enums callers match on.
2. **Context payloads** — one frozen dataclass per ``kind``; fields are JSON-safe
   scalars so :meth:`Diagnostic.to_dict` needs no per-class serializers.
3. **Records** — :class:`Diagnostic`, :class:`DiagnosticSummary`, policy, reports.
4. **Helpers** — ``validate_code_context``, ``raw_name_to_base64``, ``format_path_name``
   (used when *emitting* diagnostics; end users mostly read the records).
"""

from __future__ import annotations

import base64
import dataclasses
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal, TypeVar

from archivey.internal.extraction_types import ExtractionResult

if TYPE_CHECKING:
    from archivey.exceptions import ArchiveyError
    from archivey.types import ArchiveMember

_K = TypeVar("_K")
_V = TypeVar("_V")


def _freeze_mapping(mapping: Mapping[_K, _V] | None) -> Mapping[_K, _V]:
    """Defensive copy into an immutable mapping proxy."""
    if mapping is None:
        return MappingProxyType({})
    return MappingProxyType(dict(mapping))


@dataclass(frozen=True)
class _JsonSafeContext:
    """Shared ``to_dict`` for the flat context dataclasses below.

    Subclasses declare their own fields (this base adds none). Every field is a
    JSON-safe scalar (``str`` / ``int`` / ``None`` / ``Literal``), so
    ``dataclasses.asdict`` is enough — no per-class boilerplate.
    """

    def to_dict(self) -> dict[str, object]:
        return dataclasses.asdict(self)


class DiagnosticCode(str, Enum):
    """Stable machine codes for advisory events."""

    MEMBER_NAME_NORMALIZED = "member_name_normalized"
    MEMBER_NAME_ENCODING_INFERRED = "member_name_encoding_inferred"
    MEMBER_NAME_BIDI_CONTROL = "member_name_bidi_control"
    FORMAT_EXTENSION_CONFLICT = "format_extension_conflict"
    EXPLICIT_FORMAT_LISTED_EMPTY = "explicit_format_listed_empty"
    EXTENSION_FORMAT_UNCONFIRMED = "extension_format_unconfirmed"
    EMPTY_ARCHIVE = "empty_archive"
    ENCODING_ARGUMENT_UNUSED = "encoding_argument_unused"
    PASSWORD_ARGUMENT_UNUSED = "password_argument_unused"
    SCAN_DIRECTORY_VANISHED = "scan_directory_vanished"
    SCAN_ENTRY_VANISHED = "scan_entry_vanished"
    ARCHIVE_EOF_MARKER_MISSING = "archive_eof_marker_missing"
    ARCHIVE_TRAILING_DATA = "archive_trailing_data"
    MEMBER_TIMESTAMP_INVALID = "member_timestamp_invalid"
    SYMLINK_TARGET_UNAVAILABLE = "symlink_target_unavailable"
    DIGEST_UNVERIFIABLE = "digest_unverifiable"
    SEEK_INDEX_DEGRADED = "seek_index_degraded"
    STREAM_REWIND_REDECOMPRESSES = "stream_rewind_redecompresses"
    EXTRACTION_MEMBER_BLOCKED = "extraction_member_blocked"
    EXTRACTION_MEMBER_FAILED = "extraction_member_failed"
    EXTRACTION_NAME_COLLISION = "extraction_name_collision"
    EXTRACTION_NAME_SANITIZED = "extraction_name_sanitized"


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
class NameNormalizationContext(_JsonSafeContext):
    """Member path rewritten for display/lookup (separators, ``.``/``..``, etc.)."""

    kind: Literal["name_normalization"] = "name_normalization"
    archive_name: str | None = None
    member_name: str = ""
    member_id: int | None = None
    raw_name_base64: str | None = None
    presented_name: str = ""
    normalized_name: str = ""


@dataclass(frozen=True)
class NameEncodingContext(_JsonSafeContext):
    """Member name bytes decoded with an inferred (not declared) encoding."""

    kind: Literal["name_encoding"] = "name_encoding"
    archive_name: str | None = None
    member_name: str = ""
    member_id: int | None = None
    raw_name_base64: str | None = None
    inferred_encoding: str = ""
    declared_encoding: str = ""


@dataclass(frozen=True)
class MemberNameControlsContext(_JsonSafeContext):
    """Member name carries Unicode bidi formatting controls.

    ``controls`` is the comma-joined ``U+XXXX`` spellings in the order they occur, so a
    caller can tell an *override* (U+202A–202E, U+2066–2069 — the `…gnp.exe` disguise)
    from a *directional mark* (U+061C, U+200E, U+200F, which occur in legitimate Arabic
    and Hebrew filenames) without re-scanning the name.
    """

    kind: Literal["member_name_controls"] = "member_name_controls"
    archive_name: str | None = None
    member_name: str = ""
    member_id: int | None = None
    raw_name_base64: str | None = None
    controls: str = ""


@dataclass(frozen=True)
class UnusedArgumentContext(_JsonSafeContext):
    """An explicit argument the resolved backend cannot act on, accepted anyway.

    Carries no argument *value*: the password variant must never surface candidates
    (``diagnostics`` §"No diagnostic surface SHALL contain passwords"), and the count
    would leak a little of the same thing.
    """

    kind: Literal["unused_argument"] = "unused_argument"
    archive_name: str | None = None
    argument: Literal["encoding", "password"] = "encoding"
    format: str = ""
    reason: str = ""


@dataclass(frozen=True)
class EmptyArchiveContext(_JsonSafeContext):
    """A listing completed without error and contained no members."""

    kind: Literal["empty_archive"] = "empty_archive"
    archive_name: str | None = None
    format: str = ""


@dataclass(frozen=True)
class UnconfirmedFormatContext(_JsonSafeContext):
    """An empty listing under a format the archive's bytes never confirmed.

    ``detected_format`` is what content detection says now — ``None`` when it refuses
    the bytes outright, which is also the only possible value for the ``"extension"``
    variant (the extension fallback runs *because* every content signal declined).
    """

    kind: Literal["unconfirmed_format"] = "unconfirmed_format"
    archive_name: str | None = None
    format: str = ""
    chosen_by: Literal["argument", "extension"] = "argument"
    detected_format: str | None = None


@dataclass(frozen=True)
class FormatConflictContext(_JsonSafeContext):
    """Extension suggested one format; content detection chose another."""

    kind: Literal["format_conflict"] = "format_conflict"
    source_name: str | None = None
    extension: str | None = None
    extension_format: str = ""
    detected_format: str = ""


@dataclass(frozen=True)
class ScanRaceContext(_JsonSafeContext):
    """Directory-archive entry vanished between listing and open (TOCTOU)."""

    kind: Literal["scan_race"] = "scan_race"
    archive_name: str | None = None
    relative_path: str = ""
    entry_kind: Literal["directory", "entry"] = "entry"


@dataclass(frozen=True)
class ArchiveEofContext(_JsonSafeContext):
    """The end of the archive did not look the way the format says it should.

    Two checks share this shape, told apart by ``expected_marker``:

    - ``"two_zero_blocks"`` (``ARCHIVE_EOF_MARKER_MISSING``) — the TAR trailer itself is
      missing, short, or a non-null block.
    - ``"zeros_to_eof"`` (``ARCHIVE_TRAILING_DATA``, ``strict_archive_eof`` only) — the
      trailer was complete but a non-zero byte follows it, so the file carries something
      the listing did not account for. ``observed_bytes`` is that byte's offset past the
      trailer.
    """

    kind: Literal["archive_eof"] = "archive_eof"
    archive_name: str | None = None
    format: str = ""
    expected_marker: str = ""
    expected_bytes: int = 0
    observed_bytes: int = 0
    observed_kind: Literal["absent", "short", "nonzero"] = "absent"


@dataclass(frozen=True)
class MemberTimestampContext(_JsonSafeContext):
    """A stored timestamp field was present but unusable / out of range."""

    kind: Literal["member_timestamp"] = "member_timestamp"
    archive_name: str | None = None
    member_name: str = ""
    member_id: int | None = None
    field: str = ""
    source: str = ""
    value_repr: str = ""


@dataclass(frozen=True)
class SymlinkTargetContext(_JsonSafeContext):
    """Symlink/hardlink target could not be resolved inside the archive."""

    kind: Literal["symlink_target"] = "symlink_target"
    archive_name: str | None = None
    member_name: str = ""
    member_id: int | None = None
    reason: str = ""


@dataclass(frozen=True)
class DigestContext(_JsonSafeContext):
    """Stored digest present but not verifiable with the available data."""

    kind: Literal["digest"] = "digest"
    archive_name: str | None = None
    member_name: str = ""
    member_id: int | None = None
    algorithm: str = ""
    reason: str = ""


@dataclass(frozen=True)
class SeekIndexContext(_JsonSafeContext):
    """Seek index build failed or was skipped; stream may redecompress on rewind."""

    kind: Literal["seek_index"] = "seek_index"
    archive_name: str | None = None
    member_name: str | None = None
    member_id: int | None = None
    codec: str = ""
    scan: str = ""
    error_type: str = ""


@dataclass(frozen=True)
class StreamRewindContext(_JsonSafeContext):
    """A backward seek re-decompressed from an earlier offset (no accelerator)."""

    kind: Literal["stream_rewind"] = "stream_rewind"
    archive_name: str | None = None
    member_name: str | None = None
    member_id: int | None = None
    codec: str = ""
    from_offset: int = 0
    to_offset: int = 0
    accelerator: str | None = None


@dataclass(frozen=True)
class ExtractionOutcomeContext(_JsonSafeContext):
    """Per-member extraction blocked by policy or failed with an error."""

    kind: Literal["extraction_outcome"] = "extraction_outcome"
    archive_name: str | None = None
    member_name: str = ""
    member_id: int | None = None
    status: Literal["blocked", "failed"] = "failed"
    error_type: str = ""
    failure_group_id: str | None = None
    failure_group_size: int | None = None


@dataclass(frozen=True)
class NameCollisionContext(_JsonSafeContext):
    """A member whose casefold/NFC (or exact, under TRUSTED) name key clashed with an
    earlier written member this run — the O2 audit trail. ``prior_path`` is the path the
    earlier member claimed; ``resolution`` records how ``OverwritePolicy`` handled it."""

    kind: Literal["name_collision"] = "name_collision"
    archive_name: str | None = None
    member_name: str = ""
    member_id: int | None = None
    prior_path: str = ""
    resolution: Literal["renamed", "replaced", "skipped", "errored"] = "errored"


@dataclass(frozen=True)
class NameSanitizedContext(_JsonSafeContext):
    """A member whose name was rewritten to a portable spelling under STRICT/STANDARD — a
    trailing dot/space stripped (O3) or a non-representable byte percent-escaped (O7). The
    member still extracts; ``portable_name`` is what landed on disk."""

    kind: Literal["name_sanitized"] = "name_sanitized"
    archive_name: str | None = None
    member_name: str = ""
    member_id: int | None = None
    presented_name: str = ""
    portable_name: str = ""


DiagnosticContext = (
    NameNormalizationContext
    | NameEncodingContext
    | MemberNameControlsContext
    | UnusedArgumentContext
    | EmptyArchiveContext
    | UnconfirmedFormatContext
    | FormatConflictContext
    | ScanRaceContext
    | ArchiveEofContext
    | MemberTimestampContext
    | SymlinkTargetContext
    | DigestContext
    | SeekIndexContext
    | StreamRewindContext
    | ExtractionOutcomeContext
    | NameCollisionContext
    | NameSanitizedContext
)

_CODE_CONTEXT_KINDS: Mapping[DiagnosticCode, str] = MappingProxyType(
    {
        DiagnosticCode.MEMBER_NAME_NORMALIZED: "name_normalization",
        DiagnosticCode.MEMBER_NAME_ENCODING_INFERRED: "name_encoding",
        DiagnosticCode.MEMBER_NAME_BIDI_CONTROL: "member_name_controls",
        DiagnosticCode.FORMAT_EXTENSION_CONFLICT: "format_conflict",
        DiagnosticCode.EXPLICIT_FORMAT_LISTED_EMPTY: "unconfirmed_format",
        DiagnosticCode.EXTENSION_FORMAT_UNCONFIRMED: "unconfirmed_format",
        DiagnosticCode.EMPTY_ARCHIVE: "empty_archive",
        DiagnosticCode.ENCODING_ARGUMENT_UNUSED: "unused_argument",
        DiagnosticCode.PASSWORD_ARGUMENT_UNUSED: "unused_argument",
        DiagnosticCode.SCAN_DIRECTORY_VANISHED: "scan_race",
        DiagnosticCode.SCAN_ENTRY_VANISHED: "scan_race",
        DiagnosticCode.ARCHIVE_EOF_MARKER_MISSING: "archive_eof",
        DiagnosticCode.ARCHIVE_TRAILING_DATA: "archive_eof",
        DiagnosticCode.MEMBER_TIMESTAMP_INVALID: "member_timestamp",
        DiagnosticCode.SYMLINK_TARGET_UNAVAILABLE: "symlink_target",
        DiagnosticCode.DIGEST_UNVERIFIABLE: "digest",
        DiagnosticCode.SEEK_INDEX_DEGRADED: "seek_index",
        DiagnosticCode.STREAM_REWIND_REDECOMPRESSES: "stream_rewind",
        DiagnosticCode.EXTRACTION_MEMBER_BLOCKED: "extraction_outcome",
        DiagnosticCode.EXTRACTION_MEMBER_FAILED: "extraction_outcome",
        DiagnosticCode.EXTRACTION_NAME_COLLISION: "name_collision",
        DiagnosticCode.EXTRACTION_NAME_SANITIZED: "name_sanitized",
    }
)


def validate_code_context(code: DiagnosticCode, context: DiagnosticContext) -> None:
    """Reject unregistered or mismatched code→context pairings.

    Most codes map 1:1 onto a context ``kind`` via ``_CODE_CONTEXT_KINDS``. A few
    codes share a kind and need an extra field check so blocked≠failed, directory
    vanish≠entry vanish, etc. — those guards live below the kind match.
    """
    expected = _CODE_CONTEXT_KINDS.get(code)
    if expected is None:
        raise ValueError(f"Unknown diagnostic code: {code!r}")
    if context.kind != expected:
        raise ValueError(
            f"Diagnostic code {code.value!r} requires context kind {expected!r}, "
            f"got {context.kind!r}"
        )
    # Shared-kind codes: kind alone is not enough.
    if code is DiagnosticCode.SCAN_DIRECTORY_VANISHED and (
        not isinstance(context, ScanRaceContext) or context.entry_kind != "directory"
    ):
        raise ValueError("SCAN_DIRECTORY_VANISHED requires entry_kind='directory'")
    if code is DiagnosticCode.SCAN_ENTRY_VANISHED and (
        not isinstance(context, ScanRaceContext) or context.entry_kind != "entry"
    ):
        raise ValueError("SCAN_ENTRY_VANISHED requires entry_kind='entry'")
    if code is DiagnosticCode.EXTRACTION_MEMBER_BLOCKED and (
        not isinstance(context, ExtractionOutcomeContext) or context.status != "blocked"
    ):
        raise ValueError("EXTRACTION_MEMBER_BLOCKED requires status='blocked'")
    if code is DiagnosticCode.EXTRACTION_MEMBER_FAILED and (
        not isinstance(context, ExtractionOutcomeContext) or context.status != "failed"
    ):
        raise ValueError("EXTRACTION_MEMBER_FAILED requires status='failed'")
    if isinstance(context, ExtractionOutcomeContext):
        # Group metadata is all-or-nothing so partial fills cannot look like a group.
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
    counts: Mapping[DiagnosticCode, int] = field(
        default_factory=lambda: MappingProxyType({})
    )
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

    The report iterates, indexes, and sizes as its ``results`` sequence, so the common
    ``for result in extract(...)`` / ``len(...)`` / ``report[0]`` idioms keep working while
    ``report.diagnostics`` exposes the operation's diagnostic summary.
    """

    results: tuple[ExtractionResult, ...]
    diagnostics: DiagnosticSummary

    def __iter__(self) -> Iterator[ExtractionResult]:
        return iter(self.results)

    def __len__(self) -> int:
        return len(self.results)

    def __getitem__(self, index: int) -> ExtractionResult:
        return self.results[index]


@dataclass(frozen=True)
class MemberListReport:
    """Immutable member-list outcome: recovered members plus listing honesty.

    ``error is None`` means ``members`` is a complete archive listing. A non-``None``
    error means the tuple is the recovered prefix and the error is the terminal
    archive-level damage that stopped listing.

    Like :class:`ExtractionReport`, the report iterates, indexes, and sizes as its
    primary sequence so common ``for member in report`` / ``len(report)`` idioms work.
    """

    members: tuple[ArchiveMember, ...]
    error: ArchiveyError | None
    diagnostics: DiagnosticSummary

    def __iter__(self) -> Iterator[ArchiveMember]:
        return iter(self.members)

    def __len__(self) -> int:
        return len(self.members)

    def __getitem__(self, index: int) -> ArchiveMember:
        return self.members[index]


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
    "EmptyArchiveContext",
    "ExtractionOutcomeContext",
    "ExtractionReport",
    "FormatConflictContext",
    "MemberListReport",
    "MemberNameControlsContext",
    "MemberTimestampContext",
    "NameCollisionContext",
    "NameSanitizedContext",
    "NameEncodingContext",
    "NameNormalizationContext",
    "OnDiagnostic",
    "ScanRaceContext",
    "SeekIndexContext",
    "StreamRewindContext",
    "SymlinkTargetContext",
    "UnconfirmedFormatContext",
    "UnusedArgumentContext",
    "format_path_name",
    "raw_name_to_base64",
    "validate_code_context",
]
