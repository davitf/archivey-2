"""``info`` / ``i`` / ``detect`` verb — format detection + archive identity."""

from __future__ import annotations

import sys
from typing import TextIO

from archivey import detect_format, open_archive
from archivey.cli.common import reject_stdin_token
from archivey.cli.format import format_access_summary, format_format_label
from archivey.cli.password import resolve_password
from archivey.config import PasswordInput
from archivey.cost import CostReceipt
from archivey.exceptions import ArchiveyError
from archivey.types import ArchiveFormat


def _format_label(fmt: ArchiveFormat) -> str:
    return format_format_label(fmt)


def _line(key: str, value: object, out: TextIO) -> None:
    print(f"{key + ':':<12} {value}", file=out)


def _print_cost_axes(cost: CostReceipt, out: TextIO) -> None:
    """Verbose breakdown of the three CostReceipt axes (plus solid_block_count)."""
    _line("listing", cost.listing_cost.value, out)
    _line("access_cost", cost.access_cost.value, out)
    _line("stream", cost.stream_capability.value, out)
    blocks = cost.solid_block_count
    _line("solid_blocks", blocks if blocks is not None else "-", out)
    for note in cost.notes:
        _line("cost_note", note, out)


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
            _line("access", format_access_summary(info.cost), out)
            if verbose:
                _print_cost_axes(info.cost, out)
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
