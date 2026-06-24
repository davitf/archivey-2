"""The ``open_archive()`` entry point."""

from __future__ import annotations

from pathlib import Path
from typing import BinaryIO

from archivey.internal.detection import FormatInfo, detect_format
from archivey.internal.errors import StreamNotSeekableError
from archivey.internal.reader import ArchiveReader
from archivey.internal.registry import get_registry
from archivey.internal.streams.peekable import PeekableStream
from archivey.internal.streams.streamtools import is_seekable, is_stream
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

    The format is auto-detected from the source's magic bytes (then its extension) unless
    ``format=`` is passed explicitly. A directory path opens as a directory pseudo-archive.
    A non-seekable stream is wrapped in a :class:`PeekableStream` so detection never
    consumes bytes the backend still needs.
    """
    # Import formats package to ensure backends are registered
    import archivey.formats  # noqa: F401

    if isinstance(password, str):
        password = password.encode()

    archive_name = source_name(source)

    # A path source: normalize str -> Path; a directory short-circuits detection.
    open_source: Path | BinaryIO
    if isinstance(source, (str, Path)):
        path = Path(source)
        if path.is_dir():
            format = ArchiveFormat.DIRECTORY
        open_source = path
    else:
        open_source = source

    detected: FormatInfo | None = None
    if format is None:
        # Non-seekable streams must be wrapped before detection so the peeked prefix is
        # replayed to the backend; the same wrapper is then handed over.
        if is_stream(open_source) and not is_seekable(open_source):
            open_source = PeekableStream(open_source)
        detected = detect_format(open_source)
        format = detected.format

    registry = get_registry()
    backend_cls = registry.reader_for_format(format)

    # Fail fast for a seek-requiring backend on a non-seekable source (the access-mode
    # contract: streaming=False does not implicitly buffer).
    if (
        backend_cls.REQUIRES_SEEK
        and is_stream(open_source)
        and not is_seekable(open_source)
    ):
        raise StreamNotSeekableError(
            f"Format {format!r} requires a seekable source, but the given stream is not "
            f"seekable. Buffer it to disk or a BytesIO and reopen.",
            source_format=format,
            archive_name=archive_name,
        )

    # Thread the encoding: an explicit caller encoding wins, else the detector's hint, else
    # None (the backend auto-detects).
    effective_encoding = encoding
    if effective_encoding is None and detected is not None:
        effective_encoding = detected.encoding_hint

    backend = backend_cls()
    return backend.open_read(
        open_source,
        format=format,
        streaming=streaming,
        password=password,
        encoding=effective_encoding,
        archive_name=archive_name,
    )
