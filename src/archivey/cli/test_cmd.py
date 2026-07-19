"""``test`` / ``t`` verb — full-read integrity check."""

from __future__ import annotations

import sys
from typing import TextIO

from archivey.cli.common import open_for_cli, reject_salvage
from archivey.cli.exit_codes import EXIT_FAIL, EXIT_OK
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
    members_total: int | None = None
    with open_for_cli(archive, password=pwd, track_io=track_io, err=err) as reader:
        indexed = reader.members_report_if_available()
        # None on forward-only readers: do not consume the sole pass before streaming.
        members_for_filter = members_for_include_check(reader) if patterns else None
        if patterns and members_for_filter is not None:
            unmatched = unmatched_include_patterns(patterns, members_for_filter)
            if unmatched:
                warn_unmatched_includes(unmatched, err=err)
            if count_selected(members_for_filter, pred) == 0:
                return EXIT_FAIL

        total_bytes: int | None = None
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
        saw_selected = False
        try:
            # Manual iteration so open-time failures (wrong password, corrupt header)
            # count as FAIL and still reach the summary (F4). Once the generator raises,
            # further next() yields StopIteration — remaining members are lost (library
            # limitation for solid / poisoned streams); report them as "not tested" (P8).
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

                saw_selected = True
                if stream is None:
                    # Directories / links / non-file: no body to verify — omit from counts
                    # so "N OK" matches unzip -t style (files only).
                    if verbose:
                        print(f"skip {escape_member_name(member.name)}", file=err)
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
                        print(f"OK   {escape_member_name(member.name)}", file=err)
                except ArchiveyError as exc:
                    failed += 1
                    print(f"FAIL {escape_member_name(member.name)}: {exc}", file=err)
                except OSError as exc:
                    failed += 1
                    print(f"FAIL {escape_member_name(member.name)}: {exc}", file=err)
        finally:
            if on_progress is not None:
                on_progress.close()

        # Streaming + patterns: no pre-scan — empty selection if nothing was yielded.
        if patterns and members_for_filter is None and not saw_selected:
            warn_unmatched_includes(patterns, err=err)
            return EXIT_FAIL

    print(_test_summary(ok=ok, failed=failed, members_total=members_total), file=err)
    return EXIT_FAIL if failed else EXIT_OK


def _test_summary(*, ok: int, failed: int, members_total: int | None) -> str:
    """Format the quiet test summary, including untested remainder when known (P8)."""
    base = f"{ok} OK, {failed} failed"
    if members_total is None:
        return base
    not_tested = members_total - ok - failed
    if not_tested <= 0:
        return base
    return f"{base}, {not_tested} not tested"
