"""Public entry points: open archives and query format support."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO, Callable

from archivey.config import (
    DEFAULT_ARCHIVEY_CONFIG,
    AcceleratorMode,
    ArchiveyConfig,
    ExtractionLimits,
    PasswordInput,
)
from archivey.diagnostics import ExtractionReport
from archivey.exceptions import (
    ArchiveyUsageError,
    StreamNotSeekableError,
    UnsupportedFeatureError,
    UnsupportedFormatError,
    UnsupportedOperationError,
)
from archivey.internal.config import stream_config_from_archivey
from archivey.internal.detection import DetectionConfidence, FormatInfo, detect_format
from archivey.internal.diagnostics_collector import collector_from_config
from archivey.internal.extraction_types import (
    ExtractionPolicy,
    ExtractionProgress,
    OnError,
    OverwritePolicy,
)
from archivey.internal.open_site import capture_open_site
from archivey.internal.password import _PasswordCandidates
from archivey.internal.registry import (
    FormatAvailability,
    FormatSupport,
    MissingComponent,
    format_availability,
    get_registry,
    list_known_formats,
    list_supported_formats,
)
from archivey.internal.streams.archive_stream import ArchiveStream
from archivey.internal.streams.codecs import codec_for_stream_format, open_codec_stream
from archivey.internal.streams.peekable import PeekableStream
from archivey.internal.streams.streamtools import (
    fix_stream_start_position,
    is_seekable,
    is_stream,
    source_name,
)
from archivey.internal.volumes import OpenSourceInput, resolve_source
from archivey.reader import ArchiveReader
from archivey.types import ArchiveFormat, ContainerFormat, MemberStreams, StreamFormat

if TYPE_CHECKING:
    from archivey.internal.diagnostics_collector import DiagnosticCollector

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
    "open_stream",
    "source_name",  # re-exported from streamtools (the single implementation)
]


def _raise_multi_volume_not_supported(
    fmt: ArchiveFormat, archive_name: str | None
) -> None:
    if fmt.container == ContainerFormat.RAR:
        raise UnsupportedFeatureError(
            f"Multi-volume {fmt.container.value} archives are not supported yet "
            f"(lands in Phase 7).",
            source_format=fmt,
            archive_name=archive_name,
        )
    raise UnsupportedFeatureError(
        f"Format {fmt!r} does not support multi-volume archives.",
        source_format=fmt,
        archive_name=archive_name,
    )


def open_archive(
    source: OpenSourceInput,
    *,
    format: ArchiveFormat | None = None,
    streaming: bool = False,
    member_streams: MemberStreams = MemberStreams(0),
    password: PasswordInput = None,
    encoding: str | None = None,
    config: ArchiveyConfig | None = None,
) -> ArchiveReader:
    """Open an archive for reading.

    ``streaming=False`` (the default) opens for random access and fails fast at open
    time on a non-seekable source. ``streaming=True`` promises forward-only, single-pass
    access (works on any source, but disables random-access methods).

    ``member_streams`` declares stream capabilities beyond the default forward-only,
    single-live-stream contract:

    - ``MemberStreams.CONCURRENT`` — multiple member streams may be open at once
      (including post-materialization worker fan-out).
    - ``MemberStreams.SEEKABLE`` — member streams are seekable where the backend can
      provide positioning.

    Without those flags, a second overlapping ``open()`` raises
    ``ConcurrentAccessError``, and ``seek()`` raises ``io.UnsupportedOperation``.
    Declared concurrency does **not** gate solid open-order cost — see ``AccessCost`` /
    ``stream_members()``.

    ``streaming=True`` combined with ``MemberStreams.CONCURRENT`` is rejected
    (``ArchiveyUsageError``): a forward-only pass cannot fan out.

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

    ``source`` may be an ordered sequence of paths or binary streams that together form
    a multi-volume archive (volume joining lands in Phase 7). A length-1 sequence is
    treated as a single source.

    ``password`` accepts a single value, an ordered sequence of candidate passwords, or
    a provider callable. List the most likely password first — especially for 7z, where
    each wrong candidate pays an expensive key derivation.

    With multiple candidates (or a provider), formats whose per-open password check is
    weak may need a confirmation read before a candidate is accepted. For traditional
    ZipCrypto this is usually cheap: compressed members are confirmed from a bounded
    decompressed prefix. **STORED** ZipCrypto members are the niche exception — roughly
    1/256 of wrong passwords pass the one-byte open check, and with no decompressor to
    reject garbage the reader must scan the member once (CRC over every surviving
    candidate in parallel) to decide. That full pass is rare in practice (multiple
    passwords *and* a colliding wrong candidate *and* a STORED member) but can matter
    for very large stored members.
    """
    # Import backends to ensure they are registered
    import archivey.internal.backends  # noqa: F401

    open_site = capture_open_site()

    if streaming and MemberStreams.CONCURRENT in member_streams:
        raise ArchiveyUsageError(
            "open_archive(streaming=True) cannot be combined with "
            "MemberStreams.CONCURRENT: a forward-only pass has one progressive decoder "
            "and cannot fan out concurrent member streams."
        )

    passwords = _PasswordCandidates.from_input(password)

    effective_config = config if config is not None else DEFAULT_ARCHIVEY_CONFIG
    # The reader's diagnostic collector is created here, before detection, so automatic
    # detection and the reader share one budget/occurrence order (see diagnostics design)
    # and ``reader.diagnostics`` covers the whole open — which is what one-shot extract()
    # reads back, no cross-call collector plumbing needed.
    collector = collector_from_config(effective_config)
    resolved = resolve_source(source)
    open_source = resolved.open_source
    archive_name = resolved.archive_name

    # A path source: a directory short-circuits detection.
    if isinstance(open_source, Path) and open_source.is_dir():
        format = ArchiveFormat.DIRECTORY

    detected: FormatInfo | None = None
    if format is None:
        # Non-seekable streams must be wrapped before detection so the peeked prefix is
        # replayed to the backend; the same wrapper is then handed over.
        if is_stream(open_source) and not is_seekable(open_source):
            open_source = PeekableStream(open_source)
        detected = detect_format(open_source, collector=collector)
        format = detected.format

    if resolved.volume_count > 1 and format.container != ContainerFormat.SEVEN_Z:
        _raise_multi_volume_not_supported(format, archive_name)

    registry = get_registry()
    backend_cls = registry.reader_for_format(format)

    # A password for a format that has no encryption is API misuse, rejected centrally
    # (backends declare SUPPORTS_PASSWORD as data and never see the argument otherwise).
    if passwords.has_passwords() and not backend_cls.SUPPORTS_PASSWORD:
        raise UnsupportedOperationError(
            f"Format {format!r} does not support passwords (it carries no encryption).",
            source_format=format,
            archive_name=archive_name,
        )

    # Fail fast on a non-seekable source (the access-mode contract: streaming=False
    # promises repeatable random access, which a single forward pass cannot honor, and
    # the library never implicitly buffers). Under streaming=True the source is usable
    # only when the backend can walk its format front-to-back (TAR, single-file codecs);
    # a trailing-index format (ZIP central directory, ISO descriptors) cannot.
    if is_stream(open_source) and not is_seekable(open_source):
        if not streaming:
            raise StreamNotSeekableError(
                f"Random access (streaming=False) requires a seekable source. Open with "
                f"streaming=True for a single forward pass over this {format!r} stream, "
                f"or buffer it to disk or a BytesIO and reopen.",
                source_format=format,
                archive_name=archive_name,
            )
        if not backend_cls.SUPPORTS_STREAMING_NON_SEEKABLE:
            raise StreamNotSeekableError(
                f"Format {format!r} cannot be read from a non-seekable source even in "
                f"streaming mode (its index/metadata is not at the front of the stream). "
                f"Buffer it to disk or a BytesIO and reopen.",
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
        passwords=passwords,
        encoding=effective_encoding,
        archive_name=archive_name,
        config=effective_config,
        collector=collector,
        member_streams=member_streams,
        open_site=open_site,
    )


def open_stream(
    source: str | Path | BinaryIO,
    *,
    format: StreamFormat | ArchiveFormat | None = None,
    seekable: bool = False,
    config: ArchiveyConfig | None = None,
) -> ArchiveStream:
    """Open a single-file compressed stream and return the decompressed bytes.

    This is the compressed-streams entry point for a bare ``.gz`` / ``.bz2`` / ``.xz`` /
    … payload (no archive container). Concurrency is not a concept here — the call
    returns exactly one stream — so seekability is a boolean, not
    :class:`~archivey.MemberStreams` flags.

    ``seekable=False`` (the default) returns a forward-only stream: ``seekable()`` is
    ``False``, ``seek()`` raises ``io.UnsupportedOperation``, and no seek index or
    accelerator is instantiated. Pass ``seekable=True`` to opt into the
    seekable-decompressor-streams contract (native indexes, demand-driven accelerator
    ``AUTO``, loud slow rewinds on the non-accelerated path).

    ``format`` accepts a :class:`~archivey.StreamFormat`, a raw-stream
    :class:`~archivey.ArchiveFormat` (e.g. ``ArchiveFormat.GZ``), or ``None`` to
    auto-detect. A container format (ZIP, TAR, …) is rejected — use
    :func:`open_archive` for those.
    """
    import archivey.internal.backends  # noqa: F401

    effective_config = config if config is not None else DEFAULT_ARCHIVEY_CONFIG
    collector = collector_from_config(effective_config)

    if isinstance(source, (str, Path)):
        path = Path(source)
        if not path.is_file():
            raise FileNotFoundError(f"Compressed stream not found: {path}")
        open_source: Path | BinaryIO = path
        source_is_seekable = True
    else:
        if not is_stream(source):
            raise TypeError(
                f"open_stream source must be a path or binary stream, got {type(source)!r}"
            )
        source_is_seekable = is_seekable(source)
        if not source_is_seekable:
            open_source = PeekableStream(source)
        else:
            open_source = fix_stream_start_position(source)

    if seekable and not source_is_seekable:
        raise StreamNotSeekableError(
            "open_stream(seekable=True) requires a seekable source. Buffer the stream "
            "to disk or a BytesIO and reopen, or open with seekable=False for a "
            "forward-only pass."
        )

    stream_format = _resolve_stream_format(format, open_source, collector)
    if stream_format is StreamFormat.UNCOMPRESSED:
        raise UnsupportedFormatError(
            "open_stream requires a compressed stream format "
            f"(got {stream_format!r}); use open_archive for uncompressed containers."
        )

    codec = codec_for_stream_format(stream_format)
    stream_config = stream_config_from_archivey(
        effective_config,
        streaming=False,
        seekable=seekable and source_is_seekable,
    )
    codec_source: str | BinaryIO = (
        str(open_source) if isinstance(open_source, Path) else open_source
    )
    return open_codec_stream(
        codec,
        codec_source,
        config=stream_config,
        collector=collector,
        seekable=seekable and source_is_seekable,
    )


def _resolve_stream_format(
    format: StreamFormat | ArchiveFormat | None,
    open_source: Path | BinaryIO,
    collector: DiagnosticCollector,
) -> StreamFormat:
    if isinstance(format, StreamFormat):
        return format
    if isinstance(format, ArchiveFormat):
        if format.container is not ContainerFormat.RAW_STREAM:
            raise ArchiveyUsageError(
                f"open_stream does not accept container format {format!r}; "
                "pass a StreamFormat or a raw-stream ArchiveFormat "
                "(e.g. ArchiveFormat.GZ), or use open_archive."
            )
        return format.stream

    detected = detect_format(open_source, collector=collector)
    if detected.format.container is not ContainerFormat.RAW_STREAM:
        raise UnsupportedFormatError(
            f"Detected {detected.format!r}, which is not a single-file compressed "
            "stream. Use open_archive for archive containers."
        )
    return detected.format.stream


def extract(
    source: OpenSourceInput,
    dest: str | Path,
    *,
    policy: ExtractionPolicy = ExtractionPolicy.STRICT,
    overwrite: OverwritePolicy = OverwritePolicy.ERROR,
    on_error: OnError = OnError.STOP,
    format: ArchiveFormat | None = None,
    password: PasswordInput = None,
    encoding: str | None = None,
    on_progress: Callable[[ExtractionProgress], None] | None = None,
    config: ArchiveyConfig | None = None,
    limits: ExtractionLimits | None = None,
) -> ExtractionReport:
    """Open ``source``, apply safety checks, and write **all** members to ``dest``.

    The one-shot extraction API (see ``safe-extraction``). It deliberately has **no**
    member-selection parameter — selecting a subset requires the member list, which would
    force a reopen; use :meth:`ArchiveReader.extract_all` with ``members=`` on an already
    open reader instead. Extraction is safe-by-default: ``ExtractionPolicy.STRICT`` and
    ``OverwritePolicy.ERROR``, with the decompression-bomb guards active.

    A **non-seekable** stream source (a pipe, a socket) is opened in streaming mode
    automatically: extraction is a single forward pass, so it needs no random access, and
    failing fast would reject a source it can perfectly well consume. A seekable source
    keeps random-access mode — that preserves the re-readable second pass that recovers a
    hardlink whose target failed or preceded it in archive order.

    Returns an :class:`~archivey.ExtractionReport` whose diagnostic summary spans
    detection, open, and extraction for this call.
    """
    # Peek at the resolved open target only to pick the access mode; open_archive
    # re-resolves the original source itself (resolution is cheap and idempotent).
    resolved_target = resolve_source(source).open_source
    streaming = is_stream(resolved_target) and not is_seekable(resolved_target)

    with open_archive(
        source,
        format=format,
        streaming=streaming,
        password=password,
        encoding=encoding,
        config=config,
    ) as reader:
        # The reader already carries `config` (passed to open_archive above), so
        # extract_all falls back to it — no need to forward `config` a second time.
        report = reader.extract_all(
            dest,
            policy=policy,
            overwrite=overwrite,
            on_error=on_error,
            on_progress=on_progress,
            limits=limits,
        )
        # `reader.diagnostics` is the reader's cumulative snapshot. Because this reader was
        # opened fresh for this one-shot call, that already spans detection, open, and
        # extraction exactly once — a superset of extract_all's extraction-only delta — so
        # the one-shot report is assembled entirely from the public surface.
        return ExtractionReport(
            results=report.results,
            diagnostics=reader.diagnostics,
        )
