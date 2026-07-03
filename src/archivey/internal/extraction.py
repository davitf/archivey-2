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

import errno
import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO, Callable, Collection, cast

from archivey.exceptions import (
    ArchiveyError,
    ExtractionError,
    FilterRejectionError,
    LinkTargetNotFoundError,
    SymlinkEscapeError,
)
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
        max_bytes: int,
        max_ratio: float,
        ratio_activation_threshold: int = DEFAULT_RATIO_ACTIVATION_THRESHOLD,
        compressed_source_size: int | None = None,
        max_entries: int = DEFAULT_MAX_ENTRIES,
    ) -> None:
        self._max_bytes = max_bytes
        self._max_ratio = max_ratio
        self._ratio_floor = ratio_activation_threshold
        self._compressed_source_size = compressed_source_size
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
        if self._entry_count > self._max_entries:
            raise _AlwaysStopExtractionError(
                f"Entry-count limit reached: {self._entry_count} > {self._max_entries}"
            )

    def count(self, chunk_bytes: int) -> None:
        self._total_bytes += chunk_bytes
        self._member_bytes += chunk_bytes

        # Cumulative byte guard (always-stop).
        if self._total_bytes > self._max_bytes:
            raise _AlwaysStopExtractionError(
                f"Extraction limit reached: {self._total_bytes} bytes > {self._max_bytes}"
            )

        # Per-member ratio: activates on THIS member's output; a per-member failure
        # (skippable under OnError.CONTINUE).
        member = self._member
        cs = member.compressed_size if member is not None else None
        if member is not None and self._member_bytes > self._ratio_floor and cs and cs > 0:
            ratio = self._member_bytes / cs
            if ratio > self._max_ratio:
                raise ExtractionError(
                    f"Decompression ratio {ratio:.0f}:1 exceeds limit "
                    f"{self._max_ratio:.0f}:1 for {member.name!r}"
                )

        # Archive-wide ratio: activates on CUMULATIVE output; only when the outer
        # compressed size is cheaply known. A whole-archive bomb signal (always-stop).
        css = self._compressed_source_size
        if self._total_bytes > self._ratio_floor and css and css > 0:
            if self._total_bytes / css > self._max_ratio:
                raise _AlwaysStopExtractionError(
                    f"Archive-wide decompression ratio "
                    f"{self._total_bytes / css:.0f}:1 exceeds limit "
                    f"{self._max_ratio:.0f}:1"
                )


@dataclass
class _Orphan:
    """A selected hardlink whose (re-readable) source was excluded, awaiting the second
    pass."""

    result_index: int
    original: ArchiveMember
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
        max_extracted_bytes: int = DEFAULT_MAX_EXTRACTED_BYTES,
        max_ratio: float = DEFAULT_MAX_RATIO,
        ratio_activation_threshold: int = DEFAULT_RATIO_ACTIVATION_THRESHOLD,
        max_entries: int = DEFAULT_MAX_ENTRIES,
    ) -> None:
        self._policy = policy
        self._overwrite = overwrite
        self._on_error = on_error
        self._on_progress = on_progress
        self._members = members
        self._filter = filter
        self._max_extracted_bytes = max_extracted_bytes
        self._max_ratio = max_ratio
        self._ratio_floor = ratio_activation_threshold
        self._max_entries = max_entries

    # --- entry point ---------------------------------------------------------------

    def run(self, reader: "BaseArchiveReader", dest: str | Path) -> list[ExtractionResult]:
        dest = Path(dest)
        os.makedirs(dest, exist_ok=True)
        dest_root = dest.resolve()
        forward_only = bool(getattr(reader, "_streaming", False))

        tracker = BombTracker(
            self._max_extracted_bytes,
            self._max_ratio,
            self._ratio_floor,
            reader.compressed_source_size,
            self._max_entries,
        )

        selector = self._normalize_selector()

        all_members = reader.get_members_if_available()
        members_total = len(all_members) if all_members is not None else None
        total_estimate = self._estimate_total_bytes(all_members)

        results: list[ExtractionResult] = []
        # source member_id -> list of on-disk paths holding that source's content.
        source_paths: dict[int, list[Path]] = {}
        orphans: list[_Orphan] = []
        members_done = 0

        for original, stream in reader._iter_with_data():
            try:
                if selector is not None and not selector(original):
                    continue

                tracker.start_member(original)  # entry-count guard + ratio bookkeeping

                transformed = self._transform(original, dest_root)
                if transformed is None:
                    continue  # user filter skipped this member (deliberate exclusion)

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
            except _AlwaysStopExtractionError:
                raise
            except (ArchiveyError, OSError) as exc:
                status = (
                    ExtractionStatus.REJECTED
                    if isinstance(exc, FilterRejectionError)
                    else ExtractionStatus.FAILED
                )
                result = ExtractionResult(original, None, status, exc)
                if results and results[-1].member is original:
                    results[-1] = result
                else:
                    results.append(result)
                if self._on_error is OnError.STOP:
                    raise
                logger.warning(
                    "Skipping %s %r: %s", original.type.value, original.name, exc
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

    def _normalize_selector(self) -> Callable[[ArchiveMember], bool] | None:
        members = self._members
        if members is None:
            return None
        # A predicate is callable; a names/members collection is not.
        if callable(members):
            return cast("Callable[[ArchiveMember], bool]", members)
        collection = cast("Collection[str | ArchiveMember]", members)
        names = {m.name if isinstance(m, ArchiveMember) else m for m in collection}
        return lambda member: member.name in names

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
            return ExtractionResult(original, dest_path, ExtractionStatus.EXTRACTED, None)

        if transformed.type == MemberType.SYMLINK:
            return self._write_symlink(original, transformed, dest_root, dest_path)

        if transformed.type == MemberType.HARDLINK:
            return self._write_hardlink(
                original, transformed, dest_path, source_paths, orphans,
                forward_only, result_index,
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
        if not self._prepare_destination(transformed, dest_path):
            return ExtractionResult(original, None, ExtractionStatus.SKIPPED, None)

        os.makedirs(dest_path.parent, exist_ok=True)
        self._copy_stream_to(stream, dest_path, tracker)
        self._apply_metadata(dest_path, transformed)

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
            return ExtractionResult(original, dest_path, ExtractionStatus.EXTRACTED, None)

        # Source not written yet: the link is orphaned (its source was excluded).
        if forward_only:
            # Forward-only: the source's bytes already streamed past — unrecoverable. Per
            # spec this is a per-member ExtractionError handled by OnError.
            raise ExtractionError(
                f"Hardlink source {source.name!r} for {transformed.name!r} was excluded "
                f"and cannot be recovered on a forward-only stream",
                member_name=transformed.name,
            )
        # Re-readable: resolve in the second pass.
        orphans.append(_Orphan(result_index, original, dest_path, source))
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
        needed = {orphan.source.member_id for orphan in orphans}
        orphans_by_source: dict[int, list[_Orphan]] = {}
        for orphan in orphans:
            orphans_by_source.setdefault(orphan.source.member_id, []).append(orphan)

        # One second forward pass over the (re-readable) source; write each needed source
        # to the first of its selected link paths and os.link the rest.
        for member, stream in reader._iter_with_data():
            if member.member_id not in needed:
                self._close(stream)
                continue
            group = orphans_by_source[member.member_id]
            try:
                self._materialize_orphan_source(
                    member, stream, group, source_paths, tracker, results
                )
            except _AlwaysStopExtractionError:
                raise
            except (ArchiveyError, OSError) as exc:
                for orphan in group:
                    results[orphan.result_index] = ExtractionResult(
                        orphan.original, None, ExtractionStatus.FAILED, exc
                    )
                if self._on_error is OnError.STOP:
                    raise
                logger.warning(
                    "Skipping orphaned hardlink source %r: %s", member.name, exc
                )
            finally:
                self._close(stream)
            needed.discard(member.member_id)

        # Any orphan whose source never reappeared (should not happen for a re-readable
        # source) is a per-member failure.
        for source_id in needed:
            err = ExtractionError(
                "Hardlink source was not found on the second pass"
            )
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
        first = group[0]
        # The excluded source is written to the first selected link's destination path,
        # never to its own name.
        if self._prepare_destination(first.original, first.dest_path):
            os.makedirs(first.dest_path.parent, exist_ok=True)
            self._copy_stream_to(stream, first.dest_path, tracker)
            source_paths.setdefault(source_member.member_id, []).append(first.dest_path)
            results[first.result_index] = ExtractionResult(
                first.original, first.dest_path, ExtractionStatus.EXTRACTED, None
            )
        else:
            results[first.result_index] = ExtractionResult(
                first.original, None, ExtractionStatus.SKIPPED, None
            )

        for orphan in group[1:]:
            if not self._prepare_destination(orphan.original, orphan.dest_path):
                results[orphan.result_index] = ExtractionResult(
                    orphan.original, None, ExtractionStatus.SKIPPED, None
                )
                continue
            os.makedirs(orphan.dest_path.parent, exist_ok=True)
            self._place_link(
                source_paths, source_member.member_id, orphan.dest_path, orphan.original
            )
            results[orphan.result_index] = ExtractionResult(
                orphan.original, orphan.dest_path, ExtractionStatus.EXTRACTED, None
            )

    # --- filesystem helpers --------------------------------------------------------

    def _prepare_destination(self, member: ArchiveMember, dest_path: Path) -> bool:
        """Apply the OverwritePolicy. Returns True to proceed with creation, False to
        skip (SKIP over an existing entry). Raises ExtractionError under ERROR when the
        entry exists. Uses lstat semantics so a dangling symlink counts as existing."""
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

        # REPLACE: unlink-then-create, never write-through. Remove a symlink/file with
        # unlink (so bytes never follow the link to its target); remove a real dir tree.
        if dest_path.is_symlink() or not dest_path.is_dir():
            dest_path.unlink()
        else:
            shutil.rmtree(dest_path)
        return True

    def _copy_stream_to(
        self, stream: BinaryIO | None, path: Path, tracker: BombTracker | None
    ) -> None:
        try:
            with open(path, "wb") as dst:
                if stream is not None:
                    while True:
                        chunk = stream.read(_CHUNK)
                        if not chunk:
                            break
                        if tracker is not None:
                            tracker.count(len(chunk))
                        dst.write(chunk)
        except BaseException:
            # Remove this member's partial output on any failure (bomb guard, write
            # error, KeyboardInterrupt) and re-raise. No log here on purpose: the failure
            # propagates to run()'s per-member handler, which under OnError.CONTINUE emits
            # the WARNING and records the FAILED result, and under OnError.STOP re-raises
            # for the caller — logging here would either duplicate that or fire on a
            # deliberate STOP.
            try:
                os.unlink(path)
            except OSError:
                pass
            raise

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
