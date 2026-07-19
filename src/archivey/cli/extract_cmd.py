"""``extract`` / ``x`` verb."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from archivey import (
    ExtractionPolicy,
    ExtractionProgress,
    ExtractionReport,
    ExtractionStatus,
    OnError,
    OverwritePolicy,
)
from archivey.cli.common import open_for_cli, reject_salvage
from archivey.cli.exit_codes import EXIT_FAIL, EXIT_OK, EXIT_POLICY
from archivey.cli.filters import (
    count_selected,
    member_predicate,
    members_for_include_check,
    unmatched_include_patterns,
    warn_unmatched_includes,
)
from archivey.cli.format import escape_member_name
from archivey.cli.password import resolve_password
from archivey.cli.progress import ProgressCallback, make_progress_callback
from archivey.config import PasswordInput
from archivey.exceptions import ArchiveyError
from archivey.reader import ArchiveReader
from archivey.types import ArchiveFormat, ArchiveMember, ContainerFormat


def _archive_stem(path: Path, *, format: ArchiveFormat) -> str:
    """Stem used for the smart enclosing directory.

    Prefer the format's canonical extension (covers ``.tar.Z``, ``.tzst``, …); fall
    back to stripping a final suffix and a remaining ``.tar``. Never return empty
    (a file named exactly ``.tar.gz`` would otherwise become cwd and splatter).
    """
    name = path.name
    ext = format.file_extension()
    if ext:
        suffix = f".{ext}"
        if name.lower().endswith(suffix.lower()):
            stem = name[: -len(suffix)]
            return stem or "archive"
    stem_path = path
    if stem_path.suffix:
        stem_path = stem_path.with_suffix("")
        if stem_path.suffix.lower() == ".tar":
            stem_path = stem_path.with_suffix("")
    return stem_path.name or "archive"


def _top_level_names(members: list[ArchiveMember]) -> set[str]:
    tops: set[str] = set()
    for member in members:
        name = member.name.strip("/")
        if not name:
            continue
        tops.add(name.split("/", 1)[0])
    return tops


def _enclosing_dir(
    archive: Path,
    *,
    format: ArchiveFormat,
    overwrite: OverwritePolicy,
) -> Path:
    """Always-wrap destination used when no cheap member index is available (D1)."""
    stem = _archive_stem(archive, format=format)
    dest = Path(stem)
    if not dest.exists():
        return dest
    if overwrite is OverwritePolicy.RENAME:
        n = 1
        while Path(f"{stem} ({n})").exists():
            n += 1
        return Path(f"{stem} ({n})")
    return dest


def smart_dest(
    archive: Path,
    *,
    format: ArchiveFormat,
    members: list[ArchiveMember],
    overwrite: OverwritePolicy,
) -> Path:
    """Anti-tarbomb dest from an indexed member list (tops may already be filtered)."""
    if format.container == ContainerFormat.RAW_STREAM:
        return Path(".")
    tops = _top_level_names(members)
    if len(tops) <= 1:
        return Path(".")
    return _enclosing_dir(archive, format=format, overwrite=overwrite)


@dataclass(frozen=True)
class _SmartDestPlan:
    """Where to extract, and whether a post-extract single-root hoist may run."""

    target: Path
    may_hoist: bool


def resolve_smart_dest(
    reader: ArchiveReader,
    archive: Path,
    *,
    pred: Callable[[ArchiveMember], bool] | None,
    overwrite: OverwritePolicy,
) -> _SmartDestPlan:
    """Choose the default dest without forcing a streaming metadata pass (D1).

    - Single-file / raw-stream → cwd.
    - Indexed archive → tops on the **filtered** member set (wrap / reuse / cwd).
    - No cheap index (tar, future stdin, …) → always ``./<stem>/``, then
      :func:`maybe_hoist_single_root` may lift a single extracted top entry to cwd.
    """
    fmt = reader.format
    if fmt.container == ContainerFormat.RAW_STREAM:
        return _SmartDestPlan(Path("."), may_hoist=False)

    indexed = reader.members_report_if_available()
    if indexed is None:
        return _SmartDestPlan(
            _enclosing_dir(archive, format=fmt, overwrite=overwrite),
            may_hoist=True,
        )

    members = [m for m in indexed if pred is None or pred(m)]
    return _SmartDestPlan(
        smart_dest(archive, format=fmt, members=members, overwrite=overwrite),
        may_hoist=False,
    )


@dataclass
class _HoistResult:
    """Outcome of :func:`maybe_hoist_single_root` for reporting and exit code."""

    target: Path  # where the content ended up (the wrapper when not hoisted)
    ok: bool = True  # False → collision/failure; caller exits nonzero
    renamed: int = 0
    skipped: int = 0


class _HoistConflict(Exception):
    """A collision the overwrite policy cannot resolve without deleting data."""

    def __init__(self, dest: Path) -> None:
        super().__init__(str(dest))
        self.dest = dest


def _free_name(dest: Path, *, is_dir: bool) -> Path:
    """First ``name (N)`` free on disk — mirrors extraction ``_derive_free_name``
    (counter before the final suffix for files; whole-segment append for dirs)."""
    stem, suffix = (dest.name, "") if is_dir else (dest.stem, dest.suffix)
    n = 1
    while True:
        candidate = dest.parent / f"{stem} ({n}){suffix}"
        if not os.path.lexists(candidate):
            return candidate
        n += 1


def _merge_move(
    src: Path,
    dest: Path,
    overwrite: OverwritePolicy,
    result: _HoistResult,
    err: TextIO,
) -> None:
    """Move ``src`` to ``dest`` with the same per-file semantics as extracting
    directly into ``dest``'s parent: directories merge, file/symlink collisions
    resolve by the overwrite policy. Pre-existing files are never deleted — the
    only removal is our own just-extracted copy under SKIP, which a direct
    extraction would never have written. Symlinks are moved as links and never
    descended into (on either side)."""
    if not os.path.lexists(dest):
        os.rename(src, dest)
        return
    src_is_dir = src.is_dir() and not src.is_symlink()
    dest_is_dir = dest.is_dir() and not dest.is_symlink()
    if src_is_dir and dest_is_dir:
        for entry in sorted(src.iterdir()):
            _merge_move(entry, dest / entry.name, overwrite, result, err)
        src.rmdir()
        return
    if overwrite is OverwritePolicy.RENAME:
        free = _free_name(dest, is_dir=src_is_dir)
        os.rename(src, free)
        result.renamed += 1
        print(f"renamed: {dest} -> {free}", file=err)
        return
    if overwrite is OverwritePolicy.REPLACE and not src_is_dir and not dest_is_dir:
        os.replace(src, dest)  # replaces exactly the file being extracted
        return
    if overwrite is OverwritePolicy.SKIP and not src_is_dir:
        src.unlink()
        result.skipped += 1
        print(f"skipped: {dest}", file=err)
        return
    # ERROR policy — or a dir-vs-file shape that REPLACE/SKIP cannot express
    # without deleting pre-existing data. Stop; the caller keeps the remainder
    # under the wrapper and exits nonzero (direct extraction would have failed
    # on this same collision).
    raise _HoistConflict(dest)


def maybe_hoist_single_root(
    wrapper: Path,
    *,
    overwrite: OverwritePolicy,
    err: TextIO,
) -> _HoistResult:
    """If ``wrapper`` holds exactly one top-level entry, lift it to cwd (R4/D1).

    Recovers unar-style single-root reuse (and filter-aware D1 for streaming)
    after an always-wrap extract, without a pre-extract metadata pass. The final
    layout matches extracting directly into the wrapper's parent: directories
    merge into existing ones and per-file collisions follow the overwrite policy
    (:func:`_merge_move`). When the sole root shares the wrapper's own name
    (``src.tar.gz`` containing ``src/``), the child is flattened in place — the
    wrapper *becomes* the root, so no collision logic applies to it.
    """
    if wrapper == Path(".") or wrapper.is_symlink() or not wrapper.is_dir():
        return _HoistResult(wrapper)
    try:
        children = list(wrapper.iterdir())
    except OSError:
        return _HoistResult(wrapper)
    if len(children) != 1:
        return _HoistResult(wrapper)
    child = children[0]
    dest = wrapper.parent / child.name
    result = _HoistResult(dest)
    try:
        if dest == wrapper:
            if child.is_dir() and not child.is_symlink():
                # Flatten: wrapper/src/* → wrapper/*. The wrapper held only this
                # child, so the moves cannot collide.
                for entry in sorted(child.iterdir()):
                    entry.rename(wrapper / entry.name)
                child.rmdir()
            else:
                # Sole non-dir entry named like the wrapper: step the wrapper
                # aside so the entry can take its place.
                side = _free_name(wrapper, is_dir=True)
                wrapper.rename(side)
                (side / child.name).rename(dest)
                side.rmdir()
        else:
            _merge_move(child, dest, overwrite, result, err)
            wrapper.rmdir()
    except _HoistConflict as conflict:
        print(f"Destination already exists: {conflict.dest}", file=err)
        print(f"hoist stopped; remaining files left in {wrapper}/", file=err)
        return _HoistResult(
            wrapper, ok=False, renamed=result.renamed, skipped=result.skipped
        )
    except OSError as exc:
        print(f"hoist failed: {exc}", file=err)
        print(f"files left in {wrapper}/", file=err)
        return _HoistResult(
            wrapper, ok=False, renamed=result.renamed, skipped=result.skipped
        )
    is_dir = result.target.is_dir() and not result.target.is_symlink()
    label = f"{result.target}{'/' if is_dir else ''}"
    if result.target == wrapper:
        # In-place flatten (src.tar → src/ containing src/): name unchanged.
        print(f"removed wrapper; content at {label}", file=err)
    else:
        print(f"moved to {label}", file=err)
    return result


def _summary_dest_label(target: Path, report: ExtractionReport) -> str:
    """Closing summary destination; prefer the single extracted top when dest is cwd."""
    if target != Path("."):
        if target.is_dir():
            return f"{target}/"
        return str(target)
    tops: set[str] = set()
    for result in report:
        if result.status is not ExtractionStatus.EXTRACTED:
            continue
        name = result.member.name.strip("/")
        if name:
            tops.add(name.split("/", 1)[0])
    if len(tops) == 1:
        only = next(iter(tops))
        on_disk = Path(only)
        if on_disk.is_dir() and not on_disk.is_symlink():
            return f"{only}/"
        return only
    return "."


def _report_extraction(
    report: ExtractionReport,
    *,
    target: Path,
    verbose: bool,
    err: TextIO,
    extra_renamed: int = 0,
    extra_skipped: int = 0,
) -> tuple[int, int]:
    """Print rename notices + a closing summary from the library report (F3/D2).

    ``extra_renamed`` / ``extra_skipped`` fold in collisions resolved during the
    post-extract hoist (the library report covers only the wrapper extraction,
    which is collision-free by construction).

    Returns ``(blocked_count, failed_count)`` for exit-code selection (Q1).
    """
    extracted = 0
    renamed = extra_renamed
    skipped = extra_skipped
    blocked = 0
    failed = 0
    for result in report:
        status = result.status
        if status is ExtractionStatus.EXTRACTED:
            extracted += 1
            was_renamed = (
                result.requested_path is not None
                and result.path is not None
                and result.requested_path != result.path
            )
            if was_renamed:
                renamed += 1
                # Renames change where data lives — always report them.
                print(
                    f"renamed: {result.requested_path} -> {result.path}",
                    file=err,
                )
            elif verbose:
                print(
                    f"extracted: {escape_member_name(result.member.name)}",
                    file=err,
                )
        elif status is ExtractionStatus.NOT_OVERWRITTEN:
            skipped += 1
            # Overwrite-skips change outcomes under --overwrite skip; always note.
            where = result.requested_path or escape_member_name(result.member.name)
            print(f"not overwritten: {where}", file=err)
        elif status is ExtractionStatus.SUPERSEDED:
            skipped += 1  # count superseded entries alongside skipped in summary
            if verbose:
                print(
                    f"superseded: {escape_member_name(result.member.name)}",
                    file=err,
                )
        elif status is ExtractionStatus.BLOCKED:
            blocked += 1
            detail = f": {result.error}" if result.error is not None else ""
            print(
                f"blocked: {escape_member_name(result.member.name)}{detail}",
                file=err,
            )
        elif status is ExtractionStatus.FAILED:
            failed += 1
            detail = f": {result.error}" if result.error is not None else ""
            print(
                f"failed: {escape_member_name(result.member.name)}{detail}",
                file=err,
            )

    dest_label = _summary_dest_label(target, report)
    print(
        f"{extracted} extracted, {renamed} renamed, {skipped} skipped"
        f"{f', {blocked} blocked' if blocked else ''}"
        f"{f', {failed} failed' if failed else ''}"
        f" → {dest_label}",
        file=err,
    )
    return blocked, failed


def _exit_for_outcomes(*, blocked: int, failed: int, hoist_ok: bool) -> int:
    """Map extract outcomes to exit codes (Q8 Option A): FAILED→1, policy-only BLOCKED→3."""
    if not hoist_ok or failed:
        return EXIT_FAIL
    if blocked:
        return EXIT_POLICY
    return EXIT_OK


def run_extract(
    *,
    archive: str,
    dest: str | None,
    patterns: list[str],
    exclude: list[str],
    policy: str,
    overwrite: str,
    salvage: bool,
    password: str | None,
    track_io: bool,
    hide_progress: bool,
    verbose: bool,
    stop_on_error: bool = False,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    del out  # extract reports to stderr; files go to the filesystem
    reject_salvage(salvage)
    err = err if err is not None else sys.stderr
    pwd: PasswordInput = resolve_password(password)
    pred = member_predicate(patterns, exclude)
    policy_enum = ExtractionPolicy(policy)
    overwrite_enum = OverwritePolicy(overwrite)
    on_error = OnError.STOP if stop_on_error else OnError.CONTINUE
    archive_path = Path(archive)

    with open_for_cli(archive_path, password=pwd, track_io=track_io, err=err) as reader:
        # None on forward-only readers: do not consume the sole pass before extract.
        members_for_filter = members_for_include_check(reader) if patterns else None
        if patterns and members_for_filter is not None:
            unmatched = unmatched_include_patterns(patterns, members_for_filter)
            if unmatched:
                warn_unmatched_includes(unmatched, err=err, dest_hint=True)
            if count_selected(members_for_filter, pred) == 0:
                return EXIT_FAIL

        may_hoist = False
        if dest is not None:
            target = Path(dest)
        else:
            plan = resolve_smart_dest(
                reader,
                archive_path,
                pred=pred,
                overwrite=overwrite_enum,
            )
            target = plan.target
            may_hoist = plan.may_hoist
            if target != Path("."):
                print(f"extracting into {target}/", file=err)

        base_progress: ProgressCallback | None = make_progress_callback(
            hide_progress=hide_progress, stream=err
        )
        # Under STOP, extract_all raises without returning a report — track how
        # many members completed before the stop via the progress callback (Q1.5).
        members_completed = 0

        def on_progress(progress: ExtractionProgress) -> None:
            nonlocal members_completed
            members_completed = progress.members_done
            if base_progress is not None:
                base_progress(progress)

        try:
            try:
                report = reader.extract_all(
                    target,
                    members=pred,
                    policy=policy_enum,
                    overwrite=overwrite_enum,
                    on_error=on_error,
                    on_progress=on_progress,
                )
            except (ArchiveyError, OSError) as exc:
                # STOP-path member failure / always-stop (bomb guards,
                # DiagnosticRaisedError): report what was already written, then
                # the stop notice. Exit 1 always on abort (Q8 Option A): exit 3
                # is reserved for a *completed* run with policy blocks and safe
                # members on disk (blocks never abort under STOP).
                print(exc, file=err)
                if members_completed:
                    print(
                        f"{members_completed} member(s) written before the stop",
                        file=err,
                    )
                print(
                    "extraction stopped; remaining members were not extracted",
                    file=err,
                )
                return EXIT_FAIL
            # Streaming + patterns: empty report means nothing matched (no pre-scan).
            if patterns and members_for_filter is None and len(report) == 0:
                warn_unmatched_includes(patterns, err=err, dest_hint=True)
                return EXIT_FAIL
            hoist = _HoistResult(target)
            if may_hoist:
                hoist = maybe_hoist_single_root(
                    target, overwrite=overwrite_enum, err=err
                )
            blocked, failed = _report_extraction(
                report,
                target=hoist.target,
                verbose=verbose,
                err=err,
                extra_renamed=hoist.renamed,
                extra_skipped=hoist.skipped,
            )
            return _exit_for_outcomes(blocked=blocked, failed=failed, hoist_ok=hoist.ok)
        finally:
            if base_progress is not None:
                base_progress.close()
