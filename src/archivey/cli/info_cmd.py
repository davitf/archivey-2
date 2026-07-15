"""``info`` / ``i`` / ``detect`` verb — format + identity summary."""

from __future__ import annotations

import sys
from typing import TextIO

from archivey import detect_format, open_archive
from archivey.cli.common import reject_stdin_token
from archivey.cli.password import resolve_password
from archivey.config import PasswordInput
from archivey.exceptions import ArchiveyError


def run_info(
    *,
    archive: str,
    password: str | None,
    track_io: bool,
    verbose: bool,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    del track_io  # info does not read member bodies; track-io is a no-op here
    reject_stdin_token(archive)
    out = out if out is not None else sys.stdout
    err = err if err is not None else sys.stderr

    detected = detect_format(archive)
    print(f"path:      {archive}", file=out)
    print(f"format:    {detected.format!r}", file=out)
    print(f"confidence:{detected.confidence.value}", file=out)
    print(f"detected_by:{detected.detected_by}", file=out)
    if detected.payload_offset:
        print(f"sfx_offset:{detected.payload_offset}", file=out)

    pwd: PasswordInput = resolve_password(password)
    try:
        with open_archive(archive, password=pwd) as reader:
            info = reader.info
            print(f"version:   {info.format_version or '-'}", file=out)
            print(f"solid:     {info.is_solid}", file=out)
            print(f"encrypted: {info.is_encrypted}", file=out)
            print(f"multivolume:{info.is_multivolume}", file=out)
            print(
                f"members:   {info.member_count if info.member_count is not None else '-'}",
                file=out,
            )
            if info.comment:
                print(f"comment:   {info.comment}", file=out)
            if verbose and info.extra:
                for key, value in sorted(info.extra.items()):
                    print(f"extra.{key}:{value}", file=out)
    except ArchiveyError as exc:
        # Detection succeeded enough to print identity; open failure is still a fail.
        print(f"open:      {exc}", file=err)
        return 1
    return 0
