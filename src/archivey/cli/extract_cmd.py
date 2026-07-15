"""``extract`` / ``x`` verb."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TextIO

from archivey import ExtractionPolicy, OverwritePolicy
from archivey.cli.common import open_for_cli, reject_salvage
from archivey.cli.filters import member_predicate
from archivey.cli.password import resolve_password
from archivey.cli.progress import make_progress_callback
from archivey.config import PasswordInput
from archivey.types import ArchiveFormat, ArchiveMember, ContainerFormat


def _archive_stem(path: Path) -> str:
    name = path.name
    lower = name.lower()
    for suffix in (
        ".tar.gz",
        ".tar.bz2",
        ".tar.xz",
        ".tar.zst",
        ".tar.lz",
        ".tar.lz4",
        ".tgz",
        ".tbz2",
        ".txz",
    ):
        if lower.endswith(suffix):
            return name[: -len(suffix)]
    return path.stem or "archive"


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

    stem = _archive_stem(archive)
    dest = Path(stem)
    if not dest.exists():
        return dest
    if overwrite is OverwritePolicy.RENAME:
        n = 1
        while Path(f"{stem}-{n}").exists():
            n += 1
        return Path(f"{stem}-{n}")
    return dest


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
    del out, verbose
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

        on_progress = make_progress_callback(hide_progress=hide_progress, stream=err)
        reader.extract_all(
            target,
            members=pred,
            policy=policy_enum,
            overwrite=overwrite_enum,
            on_progress=on_progress,
        )
    return 0
