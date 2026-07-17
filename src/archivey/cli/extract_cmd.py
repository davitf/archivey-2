"""``extract`` / ``x`` verb."""

from __future__ import annotations

import sys
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


def smart_dest(
    archive: Path,
    *,
    format: ArchiveFormat,
    members: list[ArchiveMember],
    overwrite: OverwritePolicy,
) -> Path:
    """Compute the anti-tarbomb default destination when ``-d`` is omitted."""
    if format.container == ContainerFormat.RAW_STREAM:
        return Path(".")
    tops = _top_level_names(members)
    if len(tops) <= 1:
        return Path(".")

    stem = _archive_stem(archive, format=format)
    dest = Path(stem)
    if not dest.exists():
        return dest
    if overwrite is OverwritePolicy.RENAME:
        # Match the library's file-rename style: "name (N)" (not "name-N").
        n = 1
        while Path(f"{stem} ({n})").exists():
            n += 1
        return Path(f"{stem} ({n})")
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

    dest_label = "." if target == Path(".") else f"{target}/"
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
        if dest is not None:
            target = Path(dest)
        else:
            # Need a member list for the smart-dest heuristic.
            members = reader.members()
            target = smart_dest(
                archive_path,
                format=reader.format,
                members=members,
                overwrite=overwrite_enum,
            )
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
            _report_extraction(report, target=target, verbose=verbose, err=err)
        finally:
            if on_progress is not None:
                on_progress.close()
    return EXIT_OK
