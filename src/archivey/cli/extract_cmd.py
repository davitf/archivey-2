"""``extract`` / ``x`` verb."""

from __future__ import annotations

import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from archivey import (
    ExtractionPolicy,
    ExtractionReport,
    ExtractionStatus,
    OverwritePolicy,
)
from archivey.cli.common import open_for_cli, reject_salvage
from archivey.cli.exit_codes import EXIT_FAIL, EXIT_OK
from archivey.cli.filters import member_predicate
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

    indexed = reader.get_members_if_available()
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


def _collision_dest(name: str, overwrite: OverwritePolicy) -> Path | None:
    """Resolve ``name`` in cwd under ``overwrite``; ``None`` means skip hoist."""
    dest = Path(name)
    if not dest.exists():
        return dest
    if overwrite is OverwritePolicy.RENAME:
        n = 1
        while Path(f"{name} ({n})").exists():
            n += 1
        return Path(f"{name} ({n})")
    if overwrite is OverwritePolicy.SKIP:
        return None
    if overwrite is OverwritePolicy.REPLACE:
        if dest.is_dir():
            shutil.rmtree(dest)
        else:
            dest.unlink()
        return dest
    # ERROR: leave the wrapper intact rather than aborting a successful extract.
    return None


def maybe_hoist_single_root(
    wrapper: Path,
    *,
    overwrite: OverwritePolicy,
    err: TextIO,
) -> Path:
    """If ``wrapper`` holds exactly one top-level entry, move it to cwd (R4/D1).

    Recovers unar-style single-root reuse (and filter-aware D1 for streaming) after
    an always-wrap extract, without a pre-extract metadata pass. Returns the final
    path used for the summary line.
    """
    if wrapper == Path(".") or not wrapper.is_dir():
        return wrapper
    try:
        children = list(wrapper.iterdir())
    except OSError:
        return wrapper
    if len(children) != 1:
        return wrapper
    child = children[0]
    dest = _collision_dest(child.name, overwrite)
    if dest is None:
        print(
            f"left in {wrapper}/ (could not hoist {child.name!r}: destination exists)",
            file=err,
        )
        return wrapper
    try:
        shutil.move(str(child), str(dest))
        wrapper.rmdir()
    except OSError as exc:
        print(f"hoist skipped: {exc}", file=err)
        return wrapper
    label = f"{dest}/" if dest.is_dir() else str(dest)
    print(f"moved to {label}", file=err)
    return dest


def _report_extraction(
    report: ExtractionReport,
    *,
    target: Path,
    verbose: bool,
    err: TextIO,
) -> None:
    """Print rename notices + a closing summary from the library report (F3/D2)."""
    extracted = 0
    renamed = 0
    skipped = 0
    rejected = 0
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
                print(f"extracted: {result.member.name}", file=err)
        elif status is ExtractionStatus.SKIPPED:
            skipped += 1
            # Skips also change outcomes under --overwrite skip; always note.
            where = result.requested_path or result.member.name
            print(f"skipped: {where}", file=err)
        elif status is ExtractionStatus.REJECTED:
            rejected += 1
            detail = f": {result.error}" if result.error is not None else ""
            print(f"rejected: {result.member.name}{detail}", file=err)
        elif status is ExtractionStatus.FAILED:
            failed += 1
            detail = f": {result.error}" if result.error is not None else ""
            print(f"failed: {result.member.name}{detail}", file=err)

    if target == Path("."):
        dest_label = "."
    elif target.is_dir():
        dest_label = f"{target}/"
    else:
        dest_label = str(target)
    print(
        f"{extracted} extracted, {renamed} renamed, {skipped} skipped"
        f"{f', {rejected} rejected' if rejected else ''}"
        f"{f', {failed} failed' if failed else ''}"
        f" → {dest_label}",
        file=err,
    )


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
    archive_path = Path(archive)

    with open_for_cli(archive_path, password=pwd, track_io=track_io, err=err) as reader:
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

        on_progress: ProgressCallback | None = make_progress_callback(
            hide_progress=hide_progress, stream=err
        )
        try:
            try:
                report = reader.extract_all(
                    target,
                    members=pred,
                    policy=policy_enum,
                    overwrite=overwrite_enum,
                    on_progress=on_progress,
                )
            except (ArchiveyError, OSError) as exc:
                # OnError.STOP (library default) re-raises the first member failure.
                # OSError (disk full, perms) must get the same stop notice (F10).
                print(exc, file=err)
                print(
                    "extraction stopped; remaining members were not extracted",
                    file=err,
                )
                return EXIT_FAIL
            if may_hoist:
                target = maybe_hoist_single_root(
                    target, overwrite=overwrite_enum, err=err
                )
            _report_extraction(report, target=target, verbose=verbose, err=err)
        finally:
            if on_progress is not None:
                on_progress.close()
    return EXIT_OK
