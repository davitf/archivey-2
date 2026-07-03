"""Public extraction value types: the ``ExtractionPolicy`` / ``OverwritePolicy`` /
``OnError`` / ``ExtractionStatus`` enums, the ``ExtractionProgress`` / ``ExtractionResult``
dataclasses, and the ``members`` / ``filter`` type aliases.

These types are part of the public API (re-exported from ``archivey``), but they live
under ``internal/`` so the public ``reader.py`` / ``core.py`` surface and the internal
``ExtractionCoordinator`` can share them without an import cycle. They carry no behavior
beyond being enums / dataclasses; the extraction logic lives in
``internal/extraction.py`` and ``internal/filters.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Collection

from archivey.exceptions import ArchiveyError
from archivey.types import ArchiveMember

# Shared selector / filter aliases, defined in this leaf module so both the public
# reader.py signature and the internal coordinator can import them without a cycle.
#
# ``MemberSelectorArg`` — which members to extract: a collection of names / ArchiveMembers,
# a predicate, or ``None`` (= all). The collection form is normalized to a predicate by the
# coordinator; the public ``stream_members`` selector stays predicate-only (its
# duplicate-name semantics are deferred to Phase 5 — see reader.py).
MemberSelectorArg = (
    Collection["str | ArchiveMember"] | Callable[[ArchiveMember], bool] | None
)
# ``MemberFilter`` — a per-member sanitize/rename hook run after the safety checks and
# policy transform; returns a ``.replace()``d copy, or ``None`` to skip the member.
MemberFilter = Callable[[ArchiveMember], "ArchiveMember | None"]


class ExtractionPolicy(Enum):
    """How much of an archive member's stored permission/ownership metadata to trust.

    The universal path/symlink/special-file safety checks are enforced under **all**
    policies (see ``safe-extraction``); the policy only governs the permission/ownership
    transform applied to a member before it is written.
    """

    STRICT = "strict"  # default; untrusted archives
    STANDARD = "standard"  # moderate trust; e.g. your own older archives
    TRUSTED = "trusted"  # apply stored mode (and uid/gid as root); path safety still enforced


class OverwritePolicy(Enum):
    """What to do when a destination entry already exists where a member would be written.

    ``ERROR`` raises an ``ExtractionError`` for the member, which is then a per-member
    failure governed by the ``OnError`` policy — ``OnError.STOP`` re-raises and halts,
    ``OnError.CONTINUE`` records a ``FAILED`` ``ExtractionResult`` and proceeds. ``SKIP``
    is not an error: it records a ``SKIPPED`` result regardless of ``OnError``.
    """

    ERROR = "error"  # existing entry -> ExtractionError (then handled per OnError)
    SKIP = "skip"  # silently skip existing entries (records SKIPPED)
    REPLACE = "replace"  # unlink the existing entry, then create fresh (never write-through)


class OnError(Enum):
    """What to do when an individual member cannot be extracted."""

    STOP = "stop"  # default: raise the first failure and halt
    CONTINUE = "continue"  # record the failure, clean up, proceed to the next member


class ExtractionStatus(Enum):
    """The outcome recorded for a single member in its :class:`ExtractionResult`."""

    EXTRACTED = "extracted"
    SKIPPED = "skipped"  # pre-existing destination under OverwritePolicy.SKIP
    REJECTED = "rejected"  # blocked by a safety filter (universal or policy check)
    FAILED = "failed"  # error while extracting (corrupt data, ratio bomb, write error)


@dataclass
class ExtractionProgress:
    """Reported once per member through the ``on_progress`` callback."""

    member: ArchiveMember
    bytes_written: int  # cumulative bytes written across the whole call so far
    total_bytes_estimated: int | None  # None if the archive carries no size info
    members_done: int
    members_total: int | None  # None when the count would require a full scan


@dataclass
class ExtractionResult:
    """One entry per member processed, returned from ``extract()`` / ``extract_all()``."""

    member: ArchiveMember
    path: Path | None  # the written path, or None if the member was not written
    status: ExtractionStatus
    # The failure, for FAILED/REJECTED under OnError.CONTINUE; an OSError when the failure
    # is a filesystem read/write error on this member (not translated to an ArchiveyError).
    error: ArchiveyError | OSError | None = None
