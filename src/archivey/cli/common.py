"""Shared open / track-io helpers for CLI verbs."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TextIO

from archivey import open_archive
from archivey.cli.errors import CliError
from archivey.config import PasswordInput
from archivey.internal.base_reader import BaseArchiveReader
from archivey.internal.measurement import enable_measurement
from archivey.reader import ArchiveReader


def reject_stdin_token(archive: str) -> None:
    """Fail fast when ``-`` is used (stdin archives reserved, not supported)."""
    if archive == "-":
        raise CliError(
            "stdin archives are not supported yet (the '-' token is reserved)"
        )


def reject_salvage(salvage: bool) -> None:
    if salvage:
        raise CliError("--salvage is not implemented yet")


@contextmanager
def open_for_cli(
    archive: str | Path,
    *,
    password: PasswordInput = None,
    track_io: bool = False,
    err: TextIO | None = None,
) -> Iterator[ArchiveReader]:
    """Open an archive, optionally wrapping the call in measurement for ``--track-io``."""
    reject_stdin_token(str(archive))
    err = err if err is not None else sys.stderr
    if track_io:
        with enable_measurement():
            with open_archive(archive, password=password) as reader:
                yield reader
                _report_track_io(reader, err)
    else:
        with open_archive(archive, password=password) as reader:
            yield reader


def _report_track_io(reader: ArchiveReader, err: TextIO) -> None:
    if not isinstance(reader, BaseArchiveReader):
        print("track-io: counters unavailable for this reader", file=err)
        return
    print(
        "track-io:"
        f" bytes_decompressed={reader.bytes_decompressed}"
        f" compressed_bytes_consumed={reader.compressed_bytes_consumed}"
        f" source_seek_count={reader.source_seek_count}",
        file=err,
    )
