"""Public entry points: open archives and query format support."""

from __future__ import annotations

from pathlib import Path
from typing import BinaryIO, Callable

from archivey.config import (
    AcceleratorMode,
    ArchiveyConfig,
    DEFAULT_ARCHIVEY_CONFIG,
    ExtractionLimits,
)
from archivey.exceptions import StreamNotSeekableError, UnsupportedOperationError
from archivey.internal.detection import DetectionConfidence, FormatInfo, detect_format
from archivey.internal.extraction_types import (
    ExtractionPolicy,
    ExtractionProgress,
    ExtractionResult,
    OnError,
    OverwritePolicy,
)
from archivey.internal.registry import (
    FormatAvailability,
    FormatSupport,
    MissingComponent,
    format_availability,
    get_registry,
    list_known_formats,
    list_supported_formats,
)
from archivey.internal.streams.peekable import PeekableStream
from archivey.internal.streams.streamtools import (
    fix_stream_start_position,
    is_seekable,
    is_stream,
    source_name,
)
from archivey.reader import ArchiveReader
from archivey.types import ArchiveFormat

__all__ = [
    "DetectionConfidence",
    "FormatAvailability",
    "FormatInfo",
    "FormatSupport",
    "MissingComponent",
    "ArchiveyConfig",
    "ExtractionLimits",
    "AcceleratorMode",
    "DEFAULT_ARCHIVEY_CONFIG",
    "detect_format",
    "extract",
    "format_availability",
    "list_known_formats",
    "list_supported_formats",
    "open_archive",
    "source_name",  # re-exported from streamtools (the single implementation)
]


def open_archive(
    source: str | Path | BinaryIO,
    *,
    format: ArchiveFormat | None = None,
    streaming: bool = False,
    password: bytes | str | None = None,
    encoding: str | None = None,
    config: ArchiveyConfig | None = None,
) -> ArchiveReader:
    """Open an archive for reading.

    ``streaming=False`` (the default) opens for random access and fails fast at open
    time on a non-seekable source. ``streaming=True`` promises forward-only, single-pass
    access (works on any source, but disables random-access methods).

    ``config`` supplies library tuning knobs (accelerator modes, TAR end-of-archive
    strictness via ``strict_archive_eof``, default extraction limits). ``None`` selects
    the module default :data:`~archivey.DEFAULT_ARCHIVEY_CONFIG`.

    The format is auto-detected from the source's magic bytes (then its extension) unless
    ``format=`` is passed explicitly. A directory path opens as a directory pseudo-archive.
    A non-seekable stream is wrapped in a :class:`PeekableStream` so detection never
    consumes bytes the backend still needs.

    A seekable stream source is taken to hold the archive **starting at its current
    position**: detection peeks from there and restores the position, and the opener
    then wraps a mid-positioned stream in a zero-origin view so every backend sees the
    archive begin at ``tell() == 0`` (an archive embedded mid-file works uniformly,
    without manual slicing).
    """
    # Import backends to ensure they are registered
    import archivey.internal.backends  # noqa: F401

    if isinstance(password, str):
        password = password.encode()

    effective_config = config if config is not None else DEFAULT_ARCHIVEY_CONFIG
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

    # A password for a format that has no encryption is API misuse, rejected centrally
    # (backends declare SUPPORTS_PASSWORD as data and never see the argument otherwise).
    if password is not None and not backend_cls.SUPPORTS_PASSWORD:
        raise UnsupportedOperationError(
            f"Format {format!r} does not support passwords (it carries no encryption).",
            source_format=format,
            archive_name=archive_name,
        )

    # Fail fast for a seek-requiring backend on a non-seekable source (the access-mode
    # contract: streaming=False does not implicitly buffer). Per-backend opt-in only:
    # TAR may open non-seekable sources under streaming=True; ZIP/ISO still fail fast.
    if (
        backend_cls.REQUIRES_SEEK
        and not (streaming and backend_cls.SUPPORTS_STREAMING_NON_SEEKABLE)
        and is_stream(open_source)
        and not is_seekable(open_source)
    ):
        raise StreamNotSeekableError(
            f"Format {format!r} requires a seekable source, but the given stream is not "
            f"seekable. Buffer it to disk or a BytesIO and reopen.",
            source_format=format,
            archive_name=archive_name,
        )

    # Normalize the stream origin once for every backend: a seekable stream positioned
    # mid-file is wrapped so the backend sees tell() == 0 at the archive's first byte
    # (the stream-position contract in format-detection). Done after detection, which
    # peeks from and restores the same origin.
    if is_stream(open_source) and is_seekable(open_source):
        open_source = fix_stream_start_position(open_source)

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
        config=effective_config,
    )


def extract(
    source: str | Path | BinaryIO,
    dest: str | Path,
    *,
    policy: ExtractionPolicy = ExtractionPolicy.STRICT,
    overwrite: OverwritePolicy = OverwritePolicy.ERROR,
    on_error: OnError = OnError.STOP,
    format: ArchiveFormat | None = None,
    password: bytes | str | None = None,
    on_progress: Callable[[ExtractionProgress], None] | None = None,
    config: ArchiveyConfig | None = None,
    limits: ExtractionLimits | None = None,
) -> list[ExtractionResult]:
    """Open ``source``, apply safety checks, and write **all** members to ``dest``.

    The one-shot extraction API (see ``safe-extraction``). It deliberately has **no**
    member-selection parameter — selecting a subset requires the member list, which would
    force a reopen; use :meth:`ArchiveReader.extract_all` with ``members=`` on an already
    open reader instead. Extraction is safe-by-default: ``ExtractionPolicy.STRICT`` and
    ``OverwritePolicy.ERROR``, with the decompression-bomb guards active.

    Returns one :class:`~archivey.ExtractionResult` per member processed.
    """
    with open_archive(source, format=format, password=password, config=config) as reader:
        return reader.extract_all(
            dest,
            policy=policy,
            overwrite=overwrite,
            on_error=on_error,
            on_progress=on_progress,
            config=config,
            limits=limits,
        )
