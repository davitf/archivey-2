"""Public API entry points."""

from __future__ import annotations

from pathlib import Path
from typing import BinaryIO

from archivey.internal._errors import FormatDetectionError
from archivey.internal._intent import Intent
from archivey.internal._reader import BaseArchiveReader
from archivey.internal._registry import get_registry
from archivey.internal._types import ArchiveFormat


def open_archive(
    source: str | Path | BinaryIO,
    *,
    format: ArchiveFormat | None = None,
    intent: Intent = Intent.AUTO,
    password: bytes | str | None = None,
    encoding: str | None = None,
) -> BaseArchiveReader:
    """Open an archive for reading.

    If source is a directory path, opens it as a directory pseudo-archive.
    Format detection (Phase 3) is not yet wired; only DIRECTORY is auto-detected here.
    """
    # Import formats package to ensure backends are registered
    import archivey.formats  # noqa: F401

    if isinstance(password, str):
        password = password.encode()

    archive_name: str | None = None
    if isinstance(source, (str, Path)):
        path = Path(source)
        archive_name = str(path)
        if path.is_dir():
            format = ArchiveFormat.DIRECTORY  # type: ignore[attr-defined]

    if format is None:
        raise FormatDetectionError(
            "Format detection not yet implemented (Phase 3). "
            "Pass format= explicitly, or open a directory.",
            archive_name=archive_name,
        )

    registry = get_registry()
    backend_cls = registry.reader_for_format(format)
    backend = backend_cls()
    return backend.open_read(
        source if not isinstance(source, str) else Path(source),
        intent=intent,
        password=password,
        encoding=encoding,
    )
