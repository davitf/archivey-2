"""The extraction coordinator and decompression-bomb tracker.

``ExtractionCoordinator`` is a **pull-based sink**: it drives the reader
(``get_members_if_available()``, ``_iter_with_data()``, ``compressed_source_size``) and
selects an algorithm, rather than a push-model helper that buffers deferred link state.
Per member it runs the universal safety check on the original, applies the policy
transform (and optional user filter) to a transient copy, and writes FILE / DIR / SYMLINK
/ HARDLINK, tracking bomb limits and per-member results.

Hardlinks (TAR ordering guarantees the source precedes its links) are resolved by one
**core** algorithm: a sequential forward pass with a conditional second pass. When a
filter/selector orphans a selected link (its source excluded), a re-readable (seekable)
source is recovered in a single second pass; a forward-only source is unrecoverable and
fails per ``OnError``. See ``safe-extraction`` / ``format-tar`` for the normative spec.

The optional *planned single pass* optimization (staging an excluded source during the
first pass when a free member list exists) is deliberately not implemented here — it is an
optimization over this core, not a correctness requirement.
"""

from __future__ import annotations

import contextlib
import errno
import logging
import os
import shutil
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO, Callable, Literal

from archivey.config import ExtractionLimits
from archivey.diagnostics import DiagnosticCode, ExtractionOutcomeContext
from archivey.exceptions import (
    ArchiveyError,
    DiagnosticRaisedError,
    ExtractionError,
    FilterRejectionError,
    LinkTargetNotFoundError,
    SymlinkEscapeError,
)
from archivey.internal.diagnostics_collector import DiagnosticCollector
from archivey.internal.extraction_types import (
    ExtractionPolicy,
    ExtractionProgress,
    ExtractionResult,
    ExtractionStatus,
    MemberFilter,
    MemberSelectorArg,
    OnError,
    OverwritePolicy,
)
from archivey.internal.filters import POLICY_TRANSFORMS, check_universal
from archivey.internal.selection import normalize_member_selector
from archivey.types import ArchiveMember, MemberType

if TYPE_CHECKING:
    from archivey.internal.base_reader import BaseArchiveReader

logger = logging.getLogger("archivey.extraction")

_CHUNK = 1024 * 1024  # 1 MiB copy chunk

# Defaults (see the safe-extraction spec); callers override via extract()/extract_all().
DEFAULT_MAX_EXTRACTED_BYTES = 2 * 2**30  # 2 GiB
DEFAULT_MAX_RATIO = 1000.0
DEFAULT_RATIO_ACTIVATION_THRESHOLD = 5 * 2**20  # 5 MiB
DEFAULT_MAX_ENTRIES = 1_048_576  # 2**20


class _AlwaysStopExtractionError(ExtractionError):
    """A cumulative/global bomb guard: halts extraction even under ``OnError.CONTINUE``.

    A subclass of ``ExtractionError`` so callers catching ``ExtractionError`` still catch
    it; the coordinator uses the type to distinguish it from a *skippable* per-member
    ratio failure.
    """


class BombTracker:
    """Cumulative byte / per-member ratio / archive-wide ratio / entry-count guards.

    Constructed once per extraction call. ``start_member()`` is called (with the
    **original** member) before each member; ``count()`` is called with each chunk
    written.
    """

    def __init__(
        self,
        max_bytes: int | None,
        max_ratio: float | None,
        ratio_activation_threshold: int = DEFAULT_RATIO_ACTIVATION_THRESHOLD,
        max_entries: int | None = DEFAULT_MAX_ENTRIES,
        *,
        source: "BaseArchiveReader | None" = None,
    ) -> None:
        self._max_bytes = max_bytes
        self._max_ratio = max_ratio
        self._ratio_floor = ratio_activation_threshold
        # Archive-wide ratio denominators, taken from the reader so the coordinator just
        # hands over the source: the static outer compressed size when it is cheaply known
        # (captured once), otherwise a LIVE sample of the compressed bytes consumed from the
        # source (read fresh on each count(), since it grows as extraction proceeds).
        self._source = source
        self._compressed_source_size = (
            source.compressed_source_size if source is not None else None
        )
        self._max_entries = max_entries
        self._entry_count = 0
        self._total_bytes = 0  # cumulative across all members
        self._member_bytes = 0  # output bytes for the current member
        self._member: ArchiveMember | None = None

    @property
    def total_bytes(self) -> int:
        return self._total_bytes

    def start_member(self, member: ArchiveMember) -> None:
        # Called with the ORIGINAL member (not a filter copy), so compressed_size and any
        # late-bound fields are accurate. Increments the entry-count guard, which — like
        # the cumulative byte guard — halts even under OnError.CONTINUE.
        self._member = member
        self._member_bytes = 0
        self._entry_count += 1
        if self._max_entries is not None and self._entry_count > self._max_entries:
            raise _AlwaysStopExtractionError(
                f"Entry-count limit reached: {self._entry_count} > {self._max_entries}"
            )

    def count(self, chunk_bytes: int) -> None:
        self._total_bytes += chunk_bytes
        self._member_bytes += chunk_bytes

        # Cumulative byte guard (always-stop).
        if self._max_bytes is not None and self._total_bytes > self._max_bytes:
            raise _AlwaysStopExtractionError(
                f"Extraction limit reached: {self._total_bytes} bytes > {self._max_bytes}"
            )

        # Per-member ratio: activates on THIS member's output; a per-member failure
        # (skippable under OnError.CONTINUE).
        member = self._member
        cs = member.compressed_size if member is not None else None
        if (
            self._max_ratio is not None
            and member is not None
            and self._member_bytes > self._ratio_floor
            and cs
            and cs > 0
        ):
            ratio = self._member_bytes / cs
            if ratio > self._max_ratio:
                raise ExtractionError(
                    f"Decompression ratio {ratio:.0f}:1 exceeds limit "
                    f"{self._max_ratio:.0f}:1 for {member.name!r}"
                )

        # Archive-wide ratio: activates on CUMULATIVE output; a whole-archive bomb signal
        # (always-stop). Uses the static outer compressed size when it is cheaply known,
        # otherwise a LIVE denominator — the compressed bytes consumed from the source so
        # far — which works for a streaming/pipe source whose total size is unknown. The two
        # are mutually exclusive (static when known, live otherwise), so the ratio is never
        # counted twice.
        css = self._compressed_source_size
        if self._max_ratio is not None and self._total_bytes > self._ratio_floor:
            if css and css > 0:
                if self._total_bytes / css > self._max_ratio:
                    raise _AlwaysStopExtractionError(
                        f"Archive-wide decompression ratio "
                        f"{self._total_bytes / css:.0f}:1 exceeds limit "
                        f"{self._max_ratio:.0f}:1"
                    )
            elif self._source is not None:
                consumed = self._source.compressed_bytes_consumed
                if (
                    consumed
                    and consumed > 0
                    and self._total_bytes / consumed > self._max_ratio
                ):
                    raise _AlwaysStopExtractionError(
                        f"Live decompression ratio "
                        f"{self._total_bytes / consumed:.0f}:1 exceeds limit "
                        f"{self._max_ratio:.0f}:1"
                    )


@dataclass
class _Orphan:
    """A selected hardlink whose source was not yet on disk, awaiting the second pass.

    ``transformed`` is the policy/filter-transformed copy from the first pass: it supplies
    the on-disk identity (mode, timestamps) when the source's content is materialized at
    this link's path (see the ``safe-extraction`` "copy supplies the identity" rule).
    """

    result_index: int
    original: ArchiveMember
    transformed: ArchiveMember
    dest_path: Path
    source: ArchiveMember


class ExtractionCoordinator:
    """Drives a single forward pass over a reader's members, writing them safely to disk."""

    def __init__(
        self,
        *,
        policy: ExtractionPolicy = ExtractionPolicy.STRICT,
        overwrite: OverwritePolicy = OverwritePolicy.ERROR,
        on_error: OnError = OnError.STOP,
        on_progress: Callable[[ExtractionProgress], None] | None = None,
        members: MemberSelectorArg = None,
        filter: MemberFilter | None = None,
        limits: ExtractionLimits | None = None,
    ) -> None:
        self._policy = policy
        self._overwrite = overwrite
        self._on_error = on_error
        self._on_progress = on_progress
        self._members = members
        self._filter = filter
        self._limits = limits if limits is not None else ExtractionLimits()
        # Set for the duration of ``run()``; diagnostic emission reads these.
        self._diagnostics_collector: DiagnosticCollector
        self._archive_name: str | None = None

    # --- entry point ---------------------------------------------------------------

    def run(
        self, reader: "BaseArchiveReader", dest: str | Path
    ) -> list[ExtractionResult]:
        dest = Path(dest)
        self._ensure_dest_root(dest)
        dest_root = dest.resolve()
        forward_only = reader._streaming
        self._diagnostics_collector = reader._diagnostics_collector
        self._archive_name = reader._archive_name

        tracker = BombTracker(
            self._limits.max_extracted_bytes,
            self._limits.max_ratio,
            self._limits.ratio_activation_threshold,
            self._limits.max_entries,
            source=reader,
        )

        selector = normalize_member_selector(self._members)

        # Progress totals cover what this call will actually attempt: when a member list
        # is free (an upfront index) and a selector is given, totals count only the
        # selected members — so members_done can reach members_total and the byte
        # estimate matches the selected output. The user `filter` runs only during
        # extraction and cannot be pre-applied, so members it skips still count as
        # processed below. Streaming readers with no free list report None totals.
        all_members = reader.get_members_if_available()
        if all_members is not None and selector is not None:
            all_members = [m for m in all_members if selector(m)]
        members_total = len(all_members) if all_members is not None else None
        total_estimate = self._estimate_total_bytes(all_members)

        # The pass is driven through the public stream_members(), which applies the
        # selection (skipped members never surface here — they are invisible to progress
        # and results, matching the totals above). When the totals pre-filtered the free
        # member list, that list is reused as an identity selector, so a user predicate
        # runs once per member (on the index) rather than once per pre-filter plus once
        # per yield; a stateful predicate still sees each member a single time. Without
        # a free list the predicate itself is passed through.
        stream_selector = (
            None
            if selector is None
            else all_members
            if all_members is not None
            else selector
        )

        results: list[ExtractionResult] = []
        # source member_id -> list of on-disk paths holding that source's content.
        source_paths: dict[int, list[Path]] = {}
        orphans: list[_Orphan] = []
        members_done = 0

        for original, stream in reader.stream_members(stream_selector):
            try:
                transformed = self._transform(original, dest_root)
                if transformed is not None:
                    # Entry-count guard + ratio bookkeeping. Counted only once the
                    # selector and user filter have accepted the member (and the
                    # universal check inside _transform has passed), immediately before
                    # writing begins — so selector-skipped, filter-skipped, and rejected
                    # members create nothing on disk and do not count toward max_entries
                    # (resolved 2026-07 decision).
                    tracker.start_member(original)

                    result_index = len(results)
                    results.append(
                        ExtractionResult(original, None, ExtractionStatus.FAILED, None)
                    )
                    results[result_index] = self._write_member(
                        original,
                        transformed,
                        stream,
                        dest,
                        dest_root,
                        tracker,
                        source_paths,
                        orphans,
                        forward_only,
                        result_index,
                    )
                # A filter-skipped member (transformed is None) records no result, but
                # still counts as processed for progress below — it is one of the
                # selected members this pass walked over.
            except (_AlwaysStopExtractionError, DiagnosticRaisedError):
                raise
            except (ArchiveyError, OSError) as exc:
                # A name the universal filter accepted (it is fsencodable) but that the
                # *destination filesystem* refuses at write time — non-UTF-8 bytes on a
                # UTF-8-enforcing FS such as APFS/macOS raise OSError EILSEQ ("Illegal
                # byte sequence") — is not a generic I/O failure. Surface it as a typed
                # extraction error so callers get "this name is not representable here"
                # instead of a bare OSError. (Rewriting such names to an always-portable
                # spelling so the write succeeds is the separate, policy-gated
                # threat-model O7 follow-up.)
                error: ArchiveyError | OSError = exc
                if isinstance(exc, OSError) and exc.errno == errno.EILSEQ:
                    error = ExtractionError(
                        "Member name cannot be represented on the destination "
                        f"filesystem: {original.name!r}"
                    )
                    error.__cause__ = exc
                status = (
                    ExtractionStatus.REJECTED
                    if isinstance(error, FilterRejectionError)
                    else ExtractionStatus.FAILED
                )
                result = ExtractionResult(original, None, status, error)
                if results and results[-1].member is original:
                    results[-1] = result
                else:
                    results.append(result)
                if self._on_error is OnError.STOP:
                    raise error
                code = (
                    DiagnosticCode.EXTRACTION_MEMBER_REJECTED
                    if status is ExtractionStatus.REJECTED
                    else DiagnosticCode.EXTRACTION_MEMBER_FAILED
                )
                outcome: Literal["rejected", "failed"] = (
                    "rejected" if status is ExtractionStatus.REJECTED else "failed"
                )
                self._diagnostics_collector.emit(
                    code=code,
                    message=(
                        f"Skipping {original.type.value} {original.name!r}: {error}"
                    ),
                    context=ExtractionOutcomeContext(
                        archive_name=self._archive_name,
                        member_name=original.name,
                        member_id=original._member_id,
                        status=outcome,
                        error_type=type(error).__name__,
                    ),
                    logger=logger,
                )
            finally:
                self._close(stream)

            members_done += 1
            self._report_progress(
                original, tracker, total_estimate, members_done, members_total
            )

        # Core second pass: resolve orphaned hardlinks whose (re-readable) source was
        # excluded. Only populated for a seekable source; forward-only orphans already
        # failed at the link during the main pass.
        if orphans:
            self._resolve_orphans(reader, source_paths, orphans, tracker, results)

        return results

    # --- selection / transform -----------------------------------------------------

    def _transform(
        self, original: ArchiveMember, dest_root: Path
    ) -> ArchiveMember | None:
        """Universal check on the original, then policy transform and user filter on a
        transient copy. Returns the copy to write, or ``None`` if the user filter skipped
        the member. Raises a ``FilterRejectionError`` on a universal violation."""
        check_universal(original, dest_root)
        transformed = POLICY_TRANSFORMS[self._policy](original)
        if self._filter is not None:
            transformed = self._filter(transformed)
            if transformed is None:
                return None
            # A caller filter can rename/relink; re-run the universal check on the result.
            check_universal(transformed, dest_root)
        return transformed

    # --- per-member write ----------------------------------------------------------

    def _write_member(
        self,
        original: ArchiveMember,
        transformed: ArchiveMember,
        stream: BinaryIO | None,
        dest: Path,
        dest_root: Path,
        tracker: BombTracker,
        source_paths: dict[int, list[Path]],
        orphans: list[_Orphan],
        forward_only: bool,
        result_index: int,
    ) -> ExtractionResult:
        dest_path = dest / transformed.name

        if transformed.type == MemberType.DIRECTORY:
            if not self._prepare_destination(transformed, dest_path):
                return ExtractionResult(original, None, ExtractionStatus.SKIPPED, None)
            os.makedirs(dest_path, exist_ok=True)
            self._apply_metadata(dest_path, transformed)
            return ExtractionResult(
                original, dest_path, ExtractionStatus.EXTRACTED, None
            )

        if transformed.type == MemberType.SYMLINK:
            return self._write_symlink(original, transformed, dest_root, dest_path)

        if transformed.type == MemberType.HARDLINK:
            return self._write_hardlink(
                original,
                transformed,
                dest_path,
                source_paths,
                orphans,
                forward_only,
                result_index,
            )

        if transformed.type == MemberType.FILE:
            return self._write_file(
                original, transformed, stream, dest_path, tracker, source_paths
            )

        # MemberType.OTHER is rejected by check_universal; nothing else should reach here.
        raise ExtractionError(
            f"Unsupported member type {transformed.type!r} for {transformed.name!r}"
        )

    def _write_file(
        self,
        original: ArchiveMember,
        transformed: ArchiveMember,
        stream: BinaryIO | None,
        dest_path: Path,
        tracker: BombTracker,
        source_paths: dict[int, list[Path]],
    ) -> ExtractionResult:
        if not self._prepare_destination(transformed, dest_path, atomic=True):
            return ExtractionResult(original, None, ExtractionStatus.SKIPPED, None)

        os.makedirs(dest_path.parent, exist_ok=True)
        self._write_file_atomic(stream, dest_path, transformed, tracker)

        # Record this FILE's path under the ORIGINAL member id so later hardlinks whose
        # link_target_member is this member can os.link against it.
        source_paths.setdefault(original.member_id, []).append(dest_path)
        return ExtractionResult(original, dest_path, ExtractionStatus.EXTRACTED, None)

    def _write_symlink(
        self,
        original: ArchiveMember,
        transformed: ArchiveMember,
        dest_root: Path,
        dest_path: Path,
    ) -> ExtractionResult:
        if not self._prepare_destination(transformed, dest_path):
            return ExtractionResult(original, None, ExtractionStatus.SKIPPED, None)

        target = transformed.link_target
        if target is None:
            raise LinkTargetNotFoundError(
                f"Symlink {transformed.name!r} has no target",
                member_name=transformed.name,
            )

        os.makedirs(dest_path.parent, exist_ok=True)
        # A symlink is target-independent: create it even if the target was filtered out,
        # appears later, or lies outside the archive — it may dangle. Only the escape
        # check below constrains it. An os.symlink failure (unsupported FS) propagates as
        # a per-member OnError failure; no copy-the-target fallback.
        os.symlink(target, dest_path)

        # Re-validate the symlink target AFTER creating it, resolving through the real
        # filesystem. check_universal already rejected an absolute or escaping target at
        # planning time, but that check resolves the target lexically against the tree as it
        # looked *then*. The authoritative question — where does this link actually point? —
        # can only be answered against the filesystem as it is now, because an *earlier*
        # extracted member may have planted a symlink on one of this target's path
        # components that redirects it outside dest (a "chained symlink": e.g. member 1 is
        # `sub -> /tmp/evil`, member 2 is `sub/link -> x`, so `sub/link` resolves to
        # `/tmp/evil/x`). Path.resolve() follows those on-disk links, so it catches the
        # escape the planning check cannot see. We can't do this "just before" creating the
        # link because there is no link to resolve until it exists; and resolving the *bare
        # target string* would only repeat check_universal. So: create, resolve, and unlink
        # if it escaped. A cyclic/adversarial link makes resolve() raise ELOOP/RuntimeError,
        # which we also treat as an escape (fail safe rather than crash). This is the third
        # of the three defense-in-depth layers named in the `safe-extraction` spec
        # ("Symlink Escape Re-Validated at Extraction Time"); layers 1-2 are in
        # check_universal.
        try:
            resolved = (dest_path.parent / target).resolve()
            escaped = not (resolved == dest_root or resolved.is_relative_to(dest_root))
        except (OSError, RuntimeError):
            escaped = True
        if escaped:
            try:
                dest_path.unlink()
            except OSError:
                pass
            raise SymlinkEscapeError(
                f"Symlink target escapes destination: "
                f"{transformed.name!r} -> {target!r}",
                member_name=transformed.name,
            )

        return ExtractionResult(original, dest_path, ExtractionStatus.EXTRACTED, None)

    def _write_hardlink(
        self,
        original: ArchiveMember,
        transformed: ArchiveMember,
        dest_path: Path,
        source_paths: dict[int, list[Path]],
        orphans: list[_Orphan],
        forward_only: bool,
        result_index: int,
    ) -> ExtractionResult:
        source = original.link_target_member
        if source is None:
            raise LinkTargetNotFoundError(
                f"Hardlink target {original.link_target!r} not found for "
                f"{transformed.name!r}",
                member_name=transformed.name,
            )

        if source.member_id in source_paths:
            if not self._prepare_destination(transformed, dest_path):
                return ExtractionResult(original, None, ExtractionStatus.SKIPPED, None)
            os.makedirs(dest_path.parent, exist_ok=True)
            self._place_link(source_paths, source.member_id, dest_path, transformed)
            return ExtractionResult(
                original, dest_path, ExtractionStatus.EXTRACTED, None
            )

        # Source not on disk yet: either it was excluded by the selector/filter, or (in a
        # crafted/non-TAR-ordered archive) it simply appears later in archive order. The
        # second pass distinguishes the two: a source written later in this same pass is
        # just linked against; a truly excluded one is re-read and materialized.
        if forward_only:
            # Forward-only: the source's bytes already streamed past — unrecoverable. Per
            # spec this is a per-member ExtractionError handled by OnError.
            raise ExtractionError(
                f"Hardlink source {source.name!r} for {transformed.name!r} was excluded "
                f"and cannot be recovered on a forward-only stream",
                member_name=transformed.name,
            )
        # Re-readable: resolve in the second pass.
        orphans.append(_Orphan(result_index, original, transformed, dest_path, source))
        return ExtractionResult(original, None, ExtractionStatus.FAILED, None)

    # --- orphan (second pass) ------------------------------------------------------

    def _resolve_orphans(
        self,
        reader: "BaseArchiveReader",
        source_paths: dict[int, list[Path]],
        orphans: list[_Orphan],
        tracker: BombTracker,
        results: list[ExtractionResult],
    ) -> None:
        orphans_by_source: dict[int, list[_Orphan]] = {}
        for orphan in orphans:
            orphans_by_source.setdefault(orphan.source.member_id, []).append(orphan)

        # A source that was written LATER in the first pass (a link preceding its source in
        # archive order) is already on disk: just link against it — re-reading its bytes
        # here would create an independent inode and double-count against the bomb limits.
        needed: set[int] = set()
        for source_id, group in orphans_by_source.items():
            if source_id in source_paths:
                self._link_orphan_group(group, source_paths, source_id, results)
            else:
                needed.add(source_id)
        if not needed:
            return

        # One second forward pass over the (re-readable) source; write each needed source's
        # content to the first writable link path and os.link the rest. Driven through the
        # public stream_members() with the needed sources as an identity selector, so
        # unneeded members are never surfaced (or opened — the streams are lazy).
        needed_sources = [
            orphans_by_source[source_id][0].source for source_id in needed
        ]
        for member, stream in reader.stream_members(needed_sources):
            group = orphans_by_source[member.member_id]
            try:
                self._materialize_orphan_source(
                    member, stream, group, source_paths, tracker, results
                )
            except (_AlwaysStopExtractionError, DiagnosticRaisedError):
                raise
            except (ArchiveyError, OSError) as exc:
                for orphan in group:
                    results[orphan.result_index] = ExtractionResult(
                        orphan.original, None, ExtractionStatus.FAILED, exc
                    )
                if self._on_error is OnError.STOP:
                    raise
                group_id = uuid.uuid4().hex
                group_size = len(group)
                message = f"Skipping orphaned hardlink source {member.name!r}: {exc}"
                for orphan in group:
                    self._diagnostics_collector.emit(
                        code=DiagnosticCode.EXTRACTION_MEMBER_FAILED,
                        message=message,
                        context=ExtractionOutcomeContext(
                            archive_name=self._archive_name,
                            member_name=orphan.original.name,
                            member_id=orphan.original._member_id,
                            status="failed",
                            error_type=type(exc).__name__,
                            failure_group_id=group_id,
                            failure_group_size=group_size,
                        ),
                        logger=logger,
                    )
            finally:
                self._close(stream)
            needed.discard(member.member_id)
            if not needed:
                break  # every orphaned source is materialized; stop opening members

        # Any orphan whose source never reappeared (should not happen for a re-readable
        # source) is a per-member failure.
        for source_id in needed:
            err = ExtractionError("Hardlink source was not found on the second pass")
            for orphan in orphans_by_source[source_id]:
                results[orphan.result_index] = ExtractionResult(
                    orphan.original, None, ExtractionStatus.FAILED, err
                )
            if self._on_error is OnError.STOP:
                raise err

    def _materialize_orphan_source(
        self,
        source_member: ArchiveMember,
        stream: BinaryIO | None,
        group: list[_Orphan],
        source_paths: dict[int, list[Path]],
        tracker: BombTracker,
        results: list[ExtractionResult],
    ) -> None:
        # Count the recovered source bytes toward the cumulative/ratio guards too.
        tracker.start_member(source_member)

        # The excluded source's content is written to the FIRST link whose destination the
        # OverwritePolicy allows writing (a SKIP over an existing entry moves on to the next
        # link), never to the source's own name — atomically (temp + os.replace), same as a
        # normal FILE, so a failure while re-reading doesn't clobber an existing entry. The
        # link's transformed copy supplies the on-disk mode/timestamps: hardlinks share one
        # inode, so the metadata must be applied to the file that carries the content.
        writer: _Orphan | None = None
        remaining: list[_Orphan] = []
        for orphan in group:
            if writer is not None:
                remaining.append(orphan)
            elif self._prepare_destination(
                orphan.transformed, orphan.dest_path, atomic=True
            ):
                writer = orphan
            else:
                results[orphan.result_index] = ExtractionResult(
                    orphan.original, None, ExtractionStatus.SKIPPED, None
                )
        if writer is None:
            return  # every link's destination already exists under SKIP: nothing to write

        os.makedirs(writer.dest_path.parent, exist_ok=True)
        self._write_file_atomic(stream, writer.dest_path, writer.transformed, tracker)
        source_paths.setdefault(source_member.member_id, []).append(writer.dest_path)
        results[writer.result_index] = ExtractionResult(
            writer.original, writer.dest_path, ExtractionStatus.EXTRACTED, None
        )
        self._link_orphan_group(
            remaining, source_paths, source_member.member_id, results
        )

    def _link_orphan_group(
        self,
        group: list[_Orphan],
        source_paths: dict[int, list[Path]],
        source_id: int,
        results: list[ExtractionResult],
    ) -> None:
        """Link each orphan in ``group`` against the source content already on disk
        (recorded under ``source_id``), applying the OverwritePolicy per link and
        recording per-link results; per-link failures follow ``OnError``."""
        for orphan in group:
            try:
                if not self._prepare_destination(orphan.transformed, orphan.dest_path):
                    results[orphan.result_index] = ExtractionResult(
                        orphan.original, None, ExtractionStatus.SKIPPED, None
                    )
                    continue
                os.makedirs(orphan.dest_path.parent, exist_ok=True)
                self._place_link(
                    source_paths, source_id, orphan.dest_path, orphan.transformed
                )
            except (_AlwaysStopExtractionError, DiagnosticRaisedError):
                raise
            except (ArchiveyError, OSError) as exc:
                results[orphan.result_index] = ExtractionResult(
                    orphan.original, None, ExtractionStatus.FAILED, exc
                )
                if self._on_error is OnError.STOP:
                    raise
                self._diagnostics_collector.emit(
                    code=DiagnosticCode.EXTRACTION_MEMBER_FAILED,
                    message=f"Skipping hardlink {orphan.original.name!r}: {exc}",
                    context=ExtractionOutcomeContext(
                        archive_name=self._archive_name,
                        member_name=orphan.original.name,
                        member_id=orphan.original._member_id,
                        status="failed",
                        error_type=type(exc).__name__,
                    ),
                    logger=logger,
                )
                continue
            results[orphan.result_index] = ExtractionResult(
                orphan.original, orphan.dest_path, ExtractionStatus.EXTRACTED, None
            )

    # --- filesystem helpers --------------------------------------------------------

    def _ensure_dest_root(self, dest: Path) -> None:
        """Ensure ``dest`` is a directory to extract into, creating it if absent.

        A dest that resolves to a directory — a real directory or a symlink pointing at
        one — is reused, and members land inside the resolved target (``run`` resolves
        ``dest`` before writing). This matches ``tar -C``/``unzip -d`` and leaves the
        caller's symlink in place; the dest root is trusted (unlike archive-internal
        symlinks, which are never written through).

        A dest that exists as anything else — a regular file, a symlink to a file, a
        dangling symlink — is a hard error regardless of ``OverwritePolicy``: we never
        delete it. Extraction is not an invitation to remove a path the caller pointed at
        by mistake (e.g. a CLI given a file argument where a directory was meant).
        """
        if dest.is_dir():  # real directory or symlink resolving to one: reuse / follow
            return
        # ``lexists`` (not ``exists``) so a dangling symlink is caught here rather than
        # surfacing as a raw FileExistsError from ``mkdir`` below.
        if os.path.lexists(dest):
            raise ExtractionError(f"Destination exists and is not a directory: {dest}")
        dest.mkdir(parents=True, exist_ok=True)

    def _prepare_destination(
        self, member: ArchiveMember, dest_path: Path, *, atomic: bool = False
    ) -> bool:
        """Apply the OverwritePolicy. Returns True to proceed with creation, False to
        skip (SKIP over an existing entry). Raises ExtractionError under ERROR when the
        entry exists. Uses lstat semantics so a dangling symlink counts as existing.

        ``atomic=True`` is used for FILE writes, which land via ``os.replace()`` over the
        destination (see ``_write_file_atomic``): under REPLACE this leaves an existing
        **file or symlink** in place for that atomic swap (so the old data survives until
        the new file is fully written, and a symlink is replaced, never written through),
        removing only an existing **directory** first — ``os.replace`` cannot overwrite a
        directory with a file. ``atomic=False`` (DIR / SYMLINK / HARDLINK) keeps the plain
        unlink-then-create."""
        exists = os.path.lexists(dest_path)
        if not exists:
            return True

        # A real directory being (re)created as a directory is fine under any policy.
        if (
            member.type == MemberType.DIRECTORY
            and dest_path.is_dir()
            and not dest_path.is_symlink()
        ):
            return True

        if self._overwrite is OverwritePolicy.ERROR:
            raise ExtractionError(
                f"Destination already exists: {dest_path}", member_name=member.name
            )
        if self._overwrite is OverwritePolicy.SKIP:
            return False

        # REPLACE: never write-through a symlink. For an atomic FILE write, os.replace
        # handles a file/symlink target atomically, so only a real directory must be
        # removed up front. Otherwise unlink a symlink/file (bytes never follow the link)
        # and rmtree a real directory tree.
        if dest_path.is_dir() and not dest_path.is_symlink():
            shutil.rmtree(dest_path)
        elif not atomic:
            dest_path.unlink()
        return True

    def _write_file_atomic(
        self,
        stream: BinaryIO | None,
        dest_path: Path,
        member: ArchiveMember | None,
        tracker: BombTracker | None,
    ) -> None:
        """Write a FILE by streaming into a temp sibling, applying ``member``'s metadata
        (when given), then ``os.replace()``-ing it onto ``dest_path`` — atomic, so a
        mid-stream failure never truncates or removes an existing destination (only the temp
        is discarded), and the target name never appears half-written. The temp lives in the
        destination directory so the rename stays on one filesystem. ``member`` is ``None``
        when materializing an orphaned hardlink source's content (it applies no metadata; the
        links each carry their own)."""
        # mkstemp hands back an already-open fd; write straight into it (no close+reopen).
        fd, tmp_name = tempfile.mkstemp(dir=dest_path.parent, prefix=".archivey-tmp-")
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as dst:
                self._copy_to_fileobj(stream, dst, tracker)
            if member is not None:
                self._apply_metadata(tmp, member)
            os.replace(tmp, dest_path)
        except BaseException:
            # os.replace consumes the temp on success; on any earlier failure remove it so
            # no .archivey-tmp-* file is left behind (the existing destination is untouched).
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise

    @staticmethod
    def _copy_to_fileobj(
        stream: BinaryIO | None, dst: BinaryIO, tracker: BombTracker | None
    ) -> None:
        """Copy ``stream`` into the already-open ``dst`` in chunks, counting each toward the
        bomb tracker. Cleanup of a partial ``dst`` on failure is the caller's job."""
        if stream is None:
            # A FILE reaching the writer with no data stream is a backend bug, not a valid
            # empty file (a zero-byte FILE still yields a real, empty stream). Silently
            # writing an empty file here would mask that bug, so raise instead. Both FILE
            # write paths (the main pass and the orphaned-source second pass) funnel through
            # here, so this one guard covers them; it surfaces as a per-member failure via
            # the coordinator's OnError handling, attributed to the member being written.
            raise ExtractionError(
                "FILE member has no data stream to extract (backend returned stream=None)"
            )
        while True:
            chunk = stream.read(_CHUNK)
            if not chunk:
                break
            if tracker is not None:
                tracker.count(len(chunk))
            dst.write(chunk)

    def _place_link(
        self,
        source_paths: dict[int, list[Path]],
        source_id: int,
        new_path: Path,
        member: ArchiveMember,
    ) -> None:
        """Create ``new_path`` as a hardlink to the source's content, trying each recorded
        on-disk path in turn; on all-cross-device (EXDEV), copy from an existing path.
        Appends ``new_path`` so a later same-device link can reuse it."""
        existing = source_paths[source_id]
        copied = False
        for candidate in existing:
            try:
                os.link(candidate, new_path)
                break
            except OSError as exc:
                if exc.errno == errno.EXDEV:
                    continue
                raise
        else:
            # Every recorded path is cross-device: fall back to a copy from the first.
            shutil.copy2(existing[0], new_path)
            copied = True
        existing.append(new_path)
        if copied:
            self._apply_metadata(new_path, member)

    def _apply_metadata(self, path: Path, member: ArchiveMember) -> None:
        """Best-effort mode / mtime / ownership. Failures are swallowed (best-effort)."""
        if member.mode is not None:
            try:
                os.chmod(path, member.mode)
            except OSError:
                pass
        if member.modified is not None:
            try:
                ts = member.modified.timestamp()
                os.utime(path, (ts, ts))
            except (OSError, ValueError, OverflowError):
                pass
        # Ownership only under TRUSTED as root (STRICT/STANDARD never chown).
        if (
            self._policy is ExtractionPolicy.TRUSTED
            and member.uid is not None
            and member.gid is not None
            and hasattr(os, "geteuid")
            and os.geteuid() == 0
        ):
            try:
                os.chown(path, member.uid, member.gid)
            except OSError:
                pass

    # --- progress / misc -----------------------------------------------------------

    def _estimate_total_bytes(
        self, all_members: list[ArchiveMember] | None
    ) -> int | None:
        if all_members is None:
            return None
        if any(m.is_file and m.size is None for m in all_members):
            return None
        return sum(m.size or 0 for m in all_members if m.is_file)

    def _report_progress(
        self,
        member: ArchiveMember,
        tracker: BombTracker,
        total_estimate: int | None,
        members_done: int,
        members_total: int | None,
    ) -> None:
        if self._on_progress is None:
            return
        self._on_progress(
            ExtractionProgress(
                member=member,
                bytes_written=tracker.total_bytes,
                total_bytes_estimated=total_estimate,
                members_done=members_done,
                members_total=members_total,
            )
        )

    @staticmethod
    def _close(stream: BinaryIO | None) -> None:
        if stream is not None:
            try:
                stream.close()
            except Exception:  # noqa: BLE001 - best-effort close; nothing left to do
                pass
