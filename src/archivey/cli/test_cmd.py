"""``test`` / ``t`` verb — full-read integrity check."""

from __future__ import annotations

import sys
from typing import TextIO

from archivey.cli.common import open_for_cli, reject_salvage
from archivey.cli.filters import member_predicate
from archivey.cli.password import resolve_password
from archivey.config import PasswordInput
from archivey.exceptions import ArchiveyError


def run_test(
    *,
    archive: str,
    patterns: list[str],
    exclude: list[str],
    verbose: bool,
    salvage: bool,
    password: str | None,
    track_io: bool,
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
        for member, stream in reader.stream_members(pred):
            if stream is None:
                # Directories / links / non-file: no body to verify.
                ok += 1
                if verbose:
                    print(f"OK   {member.name}", file=err)
                continue
            try:
                with stream:
                    while stream.read(1024 * 1024):
                        pass
                ok += 1
                if verbose:
                    print(f"OK   {member.name}", file=err)
            except ArchiveyError as exc:
                failed += 1
                print(f"FAIL {member.name}: {exc}", file=err)
            except OSError as exc:
                failed += 1
                print(f"FAIL {member.name}: {exc}", file=err)

    print(f"{ok} OK, {failed} failed", file=err)
    return 1 if failed else 0
