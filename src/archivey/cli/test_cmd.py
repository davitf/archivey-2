"""``test`` / ``t`` verb — full-read integrity check."""

from __future__ import annotations

import sys
from typing import TextIO

from archivey.cli.common import open_for_cli, reject_salvage
from archivey.cli.filters import member_predicate
from archivey.cli.password import resolve_password
from archivey.cli.progress import ProgressCallback, make_progress_callback
from archivey.config import PasswordInput
from archivey.exceptions import ArchiveyError
from archivey.internal.extraction_types import ExtractionProgress


def run_test(
    *,
    archive: str,
    patterns: list[str],
    exclude: list[str],
    verbose: bool,
    salvage: bool,
    password: str | None,
    track_io: bool,
    hide_progress: bool = False,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    del out  # test writes summaries to stderr only
    reject_salvage(salvage)
    err = err if err is not None else sys.stderr
    pwd: PasswordInput = resolve_password(password)
    pred = member_predicate(patterns, exclude)

    ok = 0
    failed = 0
    with open_for_cli(archive, password=pwd, track_io=track_io, err=err) as reader:
        indexed = reader.get_members_if_available()
        total_bytes: int | None = None
        members_total: int | None = None
        if indexed is not None:
            selected = [m for m in indexed if pred is None or pred(m)]
            file_members = [m for m in selected if m.is_file]
            members_total = len(file_members)
            sizes = [m.size for m in file_members if m.size is not None]
            if len(sizes) == len(file_members):
                total_bytes = sum(sizes)

        on_progress: ProgressCallback | None = make_progress_callback(
            hide_progress=hide_progress, stream=err
        )
        bytes_done = 0
        files_done = 0
        try:
            # Manual iteration so open-time failures (wrong password, corrupt header)
            # count as FAIL and still reach the summary (F4). Once the generator raises,
            # further next() yields StopIteration — remaining members are lost (library
            # limitation for solid / poisoned streams).
            it = iter(reader.stream_members(pred))
            while True:
                try:
                    member, stream = next(it)
                except StopIteration:
                    break
                except ArchiveyError as exc:
                    failed += 1
                    print(f"FAIL: {exc}", file=err)
                    continue
                except OSError as exc:
                    failed += 1
                    print(f"FAIL: {exc}", file=err)
                    continue

                if stream is None:
                    # Directories / links / non-file: no body to verify — omit from counts
                    # so "N OK" matches unzip -t style (files only).
                    if verbose:
                        print(f"skip {member.name}", file=err)
                    continue
                member_written = 0
                try:
                    with stream:
                        while True:
                            chunk = stream.read(1024 * 1024)
                            if not chunk:
                                break
                            n = len(chunk)
                            member_written += n
                            bytes_done += n
                            if on_progress is not None:
                                on_progress(
                                    ExtractionProgress(
                                        member=member,
                                        bytes_written=bytes_done,
                                        total_bytes_estimated=total_bytes,
                                        members_done=files_done,
                                        members_total=members_total,
                                        member_bytes_written=member_written,
                                    )
                                )
                    ok += 1
                    files_done += 1
                    if on_progress is not None:
                        on_progress(
                            ExtractionProgress(
                                member=member,
                                bytes_written=bytes_done,
                                total_bytes_estimated=total_bytes,
                                members_done=files_done,
                                members_total=members_total,
                                member_bytes_written=member_written,
                            )
                        )
                    if verbose:
                        print(f"OK   {member.name}", file=err)
                except ArchiveyError as exc:
                    failed += 1
                    print(f"FAIL {member.name}: {exc}", file=err)
                except OSError as exc:
                    failed += 1
                    print(f"FAIL {member.name}: {exc}", file=err)
        finally:
            if on_progress is not None:
                on_progress.close()

    print(f"{ok} OK, {failed} failed", file=err)
    return 1 if failed else 0
