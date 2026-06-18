"""The ``open_archive()`` entry point."""

from __future__ import annotations

from pathlib import Path
from typing import BinaryIO

from archivey.internal.errors import FormatDetectionError
from archivey.internal.reader import ArchiveReader
from archivey.internal.registry import get_registry
from archivey.internal.types import ArchiveFormat


def source_name(source: str | Path | BinaryIO) -> str | None:
    """Best-effort human-readable name for a source, for error messages and metadata.

    A path-like source yields its string form; a file-like stream yields its ``name``
    attribute when that is a string (``open()`` sets it, ``BytesIO`` does not, and some
    streams expose an integer fd there — both of those yield ``None``).
    """
    if isinstance(source, (str, Path)):
        return str(source)
    name = getattr(source, "name", None)
    return name if isinstance(name, str) else None


def open_archive(
    source: str | Path | BinaryIO,
    *,
    format: ArchiveFormat | None = None,
    streaming: bool = False,
    password: bytes | str | None = None,
    encoding: str | None = None,
) -> ArchiveReader:
    """Open an archive for reading.

    ``streaming=False`` (the default) opens for random access and fails fast at open
    time on a non-seekable source. ``streaming=True`` promises forward-only, single-pass
    access (works on any source, but disables random-access methods).

    If source is a directory path, opens it as a directory pseudo-archive.
    Format detection (Phase 3) is not yet wired; only DIRECTORY is auto-detected here.
    """
    # Import formats package to ensure backends are registered
    import archivey.formats  # noqa: F401

    if isinstance(password, str):
        password = password.encode()

    archive_name = source_name(source)

    if isinstance(source, (str, Path)) and Path(source).is_dir():
        format = ArchiveFormat.DIRECTORY

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
        Path(source) if isinstance(source, str) else source,
        streaming=streaming,
        password=password,
        encoding=encoding,
        archive_name=archive_name,
    )
