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
# shared ``normalize_member_selector`` helper (also used by ``stream_members``).
MemberSelectorArg = (
    Collection["str | ArchiveMember"] | Callable[[ArchiveMember], bool] | None
)
# ``MemberFilter`` — a per-member sanitize/rename hook run after the safety checks and
# policy transform; returns a ``.replace()``d copy, or ``None`` to skip the member.
MemberFilter = Callable[[ArchiveMember], "ArchiveMember | None"]


class ExtractionPolicy(Enum):
    """How much of an archive member to trust when writing it to the destination.

    The universal path/symlink/special-file safety checks are enforced under **all**
    policies (see ``safe-extraction``). Beyond those, the policy governs two dimensions:
    the permission/ownership transform applied before a member is written, and the
    cross-platform name safety keyed off it — collision determinism (O2), reserved/mangled
    name rejection (O3/O4), and portable-name normalization (O7). ``STRICT`` is
    portable-by-default; ``TRUSTED`` defers to the local OS (faithful bytes, no name
    rejection or rewrite). See
    ``docs/decisions/0013-cross-platform-name-safety-policies.md``.
    """

    STRICT = "strict"  # default; untrusted archives
    STANDARD = "standard"  # moderate trust; e.g. your own older archives
    TRUSTED = (
        "trusted"  # apply stored mode (and uid/gid as root); path safety still enforced
    )


class OverwritePolicy(Enum):
    """What to do when a destination entry already exists where a member would be written.

    ``ERROR`` raises an ``ExtractionError`` for the member, which is then a per-member
    failure governed by the ``OnError`` policy — ``OnError.STOP`` re-raises and halts,
    ``OnError.CONTINUE`` records a ``FAILED`` ``ExtractionResult`` and proceeds. ``SKIP``
    is not an error: it records a ``NOT_OVERWRITTEN`` result regardless of ``OnError``.
    """

    ERROR = "error"  # existing entry -> ExtractionError (then handled per OnError)
    SKIP = "skip"  # silently skip existing entries (records NOT_OVERWRITTEN)
    REPLACE = (
        "replace"  # unlink the existing entry, then create fresh (never write-through)
    )
    RENAME = "rename"  # write a colliding entry under a derived "name (N)" spelling


class OnError(Enum):
    """What to do when an individual member cannot be extracted.

    Governs per-member *failures* only (corrupt/truncated/undecodable data,
    write ``OSError``, overwrite ``ERROR``, etc.). A policy ``BLOCKED`` outcome
    (``FilterRejectionError`` from a universal path-safety check or a policy
    filter) is always recorded and continued, under either value. Aborting the
    whole extraction on the first unsafe member is a separate future opt-in.
    """

    STOP = "stop"  # default: raise the first member failure and halt
    CONTINUE = "continue"  # record the failure, clean up, proceed to the next member


class ExtractionStatus(str, Enum):
    """The outcome recorded for a single member in its :class:`ExtractionResult`."""

    EXTRACTED = "extracted"
    NOT_OVERWRITTEN = "not_overwritten"  # existing dest left under OverwritePolicy.SKIP
    SUPERSEDED = "superseded"  # non-current entry (a later same-name entry overwrites)
    BLOCKED = "blocked"  # blocked by a safety filter (universal or policy check)
    FAILED = "failed"  # error while extracting (corrupt data, ratio bomb, write error)


@dataclass
class ExtractionProgress:
    """Progress snapshot for the ``on_progress`` callback.

    For FILE members the callback MAY fire multiple times as bytes are written
    (about once per copy chunk); each member still gets a terminal report with
    ``member_bytes_written`` equal to the member's size (or the final observed
    byte count when size is unknown). Directories, symlinks, and hardlinks
    produce a single report with ``member_bytes_written == 0``.
    """

    member: ArchiveMember
    bytes_written: int  # cumulative bytes written across the whole call so far
    total_bytes_estimated: int | None  # None if the archive carries no size info
    members_done: int
    members_total: int | None  # None when the count would require a full scan
    member_bytes_written: int  # output bytes written for the current member so far


@dataclass(frozen=True)
class ExtractionResult:
    """One entry per member processed, returned from ``extract()`` / ``extract_all()``.

    Frozen outcome structure (``path`` / ``status`` / ``error`` cannot be replaced after
    construction). ``member`` still refers to the live mutable :class:`ArchiveMember`
    whose late-bound metadata may be filled in place.
    """

    member: ArchiveMember
    path: Path | None  # the written path, or None if the member was not written
    status: ExtractionStatus
    # The failure, for FAILED/BLOCKED under OnError.CONTINUE; an OSError when the failure
    # is a filesystem read/write error on this member (not translated to an ArchiveyError).
    error: ArchiveyError | OSError | None = None
    # The destination the coordinator intended before overwrite/rename resolution. For an
    # ordinary write it equals ``path``; under ``OverwritePolicy.RENAME`` a collided member
    # is written to a derived name, so ``requested_path != path and status == EXTRACTED``
    # marks the rename; a collision resolved by SKIP/ERROR sets ``requested_path`` with
    # ``path=None``. ``None`` for members that never reached destination resolution.
    requested_path: Path | None = None
