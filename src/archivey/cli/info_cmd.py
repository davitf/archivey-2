"""``info`` / ``i`` / ``detect`` verb — format detection + archive identity."""

from __future__ import annotations

import sys
from typing import TextIO

from archivey import detect_format, open_archive
from archivey.cli.common import reject_stdin_token
from archivey.cli.password import resolve_password
from archivey.config import PasswordInput
from archivey.exceptions import ArchiveyError
from archivey.types import ArchiveFormat


def _format_label(fmt: ArchiveFormat) -> str:
    """Human format name (``zip``, ``7z``, ``tar.gz``) rather than enum spellings."""
    ext = fmt.file_extension()
    if ext:
        return ext
    return fmt.display_name.lower().replace("_", "-")


def _line(key: str, value: object, out: TextIO) -> None:
    print(f"{key + ':':<12} {value}", file=out)


def run_info(
    *,
    archive: str,
    password: str | None,
    track_io: bool,
    verbose: bool,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    reject_stdin_token(archive)
    out = out if out is not None else sys.stdout
    err = err if err is not None else sys.stderr
    if track_io:
        print("track-io: n/a for info (no member-body decode)", file=err)

    detected = detect_format(archive)
    _line("path", archive, out)
    _line("format", _format_label(detected.format), out)
    _line("confidence", detected.confidence.value, out)
    _line("detected_by", detected.detected_by, out)
    if detected.payload_offset:
        _line("sfx_offset", detected.payload_offset, out)

    pwd: PasswordInput = resolve_password(password)
    try:
        with open_archive(archive, password=pwd) as reader:
            info = reader.info
            _line("version", info.format_version or "-", out)
            _line("solid", info.is_solid, out)
            _line("encrypted", info.is_encrypted, out)
            _line("multivolume", info.is_multivolume, out)
            _line(
                "members",
                info.member_count if info.member_count is not None else "-",
                out,
            )
            if info.comment:
                _line("comment", info.comment, out)
            if verbose and info.extra:
                for key, value in sorted(info.extra.items()):
                    _line(f"extra.{key}", value, out)
    except ArchiveyError as exc:
        # Detection succeeded enough to print identity; open failure is still a fail.
        _line("open", exc, err)
        return 1
    return 0
