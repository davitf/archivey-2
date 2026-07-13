"""Format detection: ``detect_format()`` and the ``FormatInfo`` it returns.

Detection is **magic-first** (an exact magic-byte match at the expected offset →
``CERTAIN``) with an extension fallback (``GUESS``). The magic and extension tables are
not hand-maintained here: each registered backend declares its ``MAGIC`` / ``EXTENSIONS``
as data and the detector aggregates them, so a new format becomes detectable by
registering its backend (see ``format-detection`` and ``backend-registry``).

Detection never consumes bytes from the source: paths are opened and closed; seekable
streams are read and restored to their **starting position** (the archive is taken to
begin wherever the stream is positioned when handed in, so a mid-positioned stream —
e.g. an archive embedded in a larger file — detects against the right bytes); a
non-seekable stream must be wrapped in a
:class:`~archivey.internal.streams.peekable.PeekableStream` first (the opener does this),
which detection inspects via ``peek``.

Formats without an exact magic are recognized by a **content probe**: Brotli (no signature
at all) and zlib (a 2-byte header too unspecific to trust, so its probe gates on that
header before decoding). Each probe is a function the backends declare as data — for the
stream codecs, on the codec descriptor — so the detector stays format-agnostic. The
inner-TAR probe, the ISO extended window, and SFX scanning arrive with the backends they
feed in later stages.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO, Callable

from archivey.config import DEFAULT_ARCHIVEY_CONFIG, AcceleratorMode
from archivey.diagnostics import (
    DiagnosticCode,
    DiagnosticSummary,
    FormatConflictContext,
)
from archivey.exceptions import ArchiveyError, FormatDetectionError
from archivey.internal.diagnostics_collector import (
    DiagnosticCollector,
    collector_from_config,
)
from archivey.internal.logs import detection as logger
from archivey.internal.registry import get_registry
from archivey.internal.streams.peekable import DETECTION_LIMIT, PeekableStream
from archivey.internal.streams.streamtools import (
    ReadOnlyIOStream,
    is_seekable,
    read_exact,
    source_name,
)
from archivey.types import (
    ArchiveFormat,
    ContainerFormat,
    MagicSignature,
    StreamFormat,
)

if TYPE_CHECKING:
    from archivey.config import ArchiveyConfig

# Decompressed bytes needed to see a TAR ``ustar`` signature at offset 257 (one 512-byte
# header block covers it).
_INNER_TAR_PROBE_BYTES = 512

# Upper bound on compressed input the inner-TAR probe reads from the source when the peeked
# detection prefix is too short. bzip2 is block-transform (BWT) based: it emits no output
# until a whole block is read, and a block holds up to 900 KB uncompressed (level 9), which
# for incompressible leading data compresses to just over 900 KB. 1 MiB covers a full
# worst-case first block with margin; a stream-oriented codec (gzip/xz/zstd/…) reaches the
# header region from the ordinary prefix and never triggers this larger read.
_INNER_TAR_MAX_PROBE_BYTES = 1 << 20


class DetectionConfidence(Enum):
    CERTAIN = "certain"  # exact magic-byte match at the expected offset
    PROBABLE = "probable"  # structural/content probe (inner-tar probe, SFX scan)
    GUESS = "guess"  # file extension only, no content confirmation


@dataclass(frozen=True)
class FormatInfo:
    """The result of :func:`detect_format` — the detected format plus how sure we are."""

    format: ArchiveFormat
    confidence: DetectionConfidence
    detected_by: str  # "magic", "extension", "content_probe", "sfx_scan"
    encoding_hint: str | None = None
    payload_offset: int = (
        0  # nonzero only for SFX archives (is-SFX == payload_offset > 0)
    )
    diagnostics: DiagnosticSummary = field(default_factory=DiagnosticSummary.empty)


def _peek_prefix(source: str | Path | BinaryIO, length: int) -> bytes:
    """Return the source's next ``length`` bytes without consuming them.

    Paths are opened and closed; a :class:`PeekableStream` is peeked; a seekable stream
    is read from its **current position** and restored to it (the archive starts
    wherever the caller positioned the stream — see the stream-position contract in
    ``format-detection``). A raw non-seekable stream would lose the prefix, so the
    caller (the opener) must wrap it in a ``PeekableStream`` first.
    """
    if isinstance(source, (str, Path)):
        with open(source, "rb") as f:
            return read_exact(f, length)
    if isinstance(source, PeekableStream):
        return source.peek(length)
    if is_seekable(source):
        start = source.tell()
        data = read_exact(source, length)
        source.seek(start)
        return data
    # Raw non-seekable stream used standalone: reading consumes bytes the caller can no
    # longer reach. Detection still works, but the prefix is gone — the opener avoids this
    # by wrapping non-seekable sources in a PeekableStream.
    return read_exact(source, length)


def _match_magic(
    data: bytes,
    magic_entries: list[MagicSignature],
) -> ArchiveFormat | None:
    """Return the format of the first exact magic signature matching ``data``."""
    for entry in magic_entries:
        if data[entry.offset : entry.offset + len(entry.magic)] == entry.magic:
            return entry.format
    return None


def _match_extension(
    name: str | None, extension_map: dict[str, ArchiveFormat]
) -> tuple[ArchiveFormat, str] | None:
    if name is None:
        return None
    lowered = name.lower()
    # Longest extension wins so ".tar.gz" beats ".gz".
    for ext in sorted(extension_map, key=len, reverse=True):
        if lowered.endswith(ext.lower()):
            return extension_map[ext], ext
    return None


class _BoundedPeekReader(ReadOnlyIOStream):
    """A bounded, non-consuming reader over a ``peek_more`` callable.

    ``peek_more(n)`` returns the source's first ``n`` bytes without consuming them
    (idempotent, growing supersets — see :func:`_peek_prefix`). Reads walk an
    internal offset over successive peeks (caching the last buffer so growth stays
    linear), letting a codec pull exactly as much compressed input as it needs and
    never more than ``limit`` (one maximum compressor block).

    Seekable within the bound so codecs that require seek (unix-compress) can still
    probe; seeking never pulls bytes beyond ``limit``, and peeks still grow only on
    demand when a read needs more of the prefix.
    """

    def __init__(self, peek_more: Callable[[int], bytes], limit: int) -> None:
        super().__init__()
        self._peek_more = peek_more
        self._limit = limit
        self._offset = 0
        self._buf = b""

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self, /) -> int:
        return self._offset

    def seek(self, offset: int, whence: int = 0, /) -> int:
        if whence == 0:  # SEEK_SET
            new_pos = offset
        elif whence == 1:  # SEEK_CUR
            new_pos = self._offset + offset
        elif whence == 2:  # SEEK_END — end of the bounded window, not the real source
            new_pos = self._limit + offset
        else:
            raise ValueError(f"invalid whence: {whence!r}")
        if new_pos < 0:
            raise ValueError(f"negative seek position {new_pos}")
        # Cap at the bound; reads past the peeked prefix already return b"".
        self._offset = min(new_pos, self._limit)
        return self._offset

    def read(self, n: int = -1, /) -> bytes:
        end = self._limit if n < 0 else min(self._offset + n, self._limit)
        if end > len(self._buf):
            self._buf = self._peek_more(end)  # a superset of the current buffer
        chunk = self._buf[self._offset : end]
        self._offset += len(chunk)
        return chunk


def _probe_inner_tar(
    stream_format: StreamFormat,
    peek_more: Callable[[int], bytes],
) -> bool:
    """Whether decompressing the source yields a TAR (``ustar`` at offset 257).

    The codec layer decodes the compressed source and the inner ``ustar`` magic confirms a
    tarball wrapped in the compressor. The decoder reads from a bounded, non-consuming view of
    the source (:class:`_BoundedPeekReader` over ``peek_more``), so it pulls exactly as much
    compressed input as it needs to reach the TAR header region and no more: a stream-oriented
    codec (gzip/xz/zstd/…) emits output incrementally and stops after a few KiB, while a
    block-transform codec (bzip2), which emits nothing until a whole block is read, pulls up to
    one maximum block (``_INNER_TAR_MAX_PROBE_BYTES``).

    The peek reader is seekable within that bound so unix-compress (LZW) can probe.
    Accelerators are forced ``OFF``: ``seekable=True`` must not flip AUTO rapidgzip /
    IndexedBzip2File on for a short detection peek (those paths reject incomplete
    sources and can leak raw C++ exceptions on corrupt prefixes). Prefer that over
    the older ``seekable=False`` workaround for keeping accelerators off the probe.

    Returns ``False`` (deferring the determination to open time) when the codec backend is
    absent, the source is not decodable as this codec, or the decoded output carries no TAR
    header.
    """
    # Imported here rather than at module load to avoid a detection<->codecs import cycle.
    from archivey.internal.config import StreamConfig
    from archivey.internal.streams.codecs import (
        codec_for_stream_format,
        is_codec_available,
        open_codec_stream,
    )

    try:
        codec = codec_for_stream_format(stream_format)
    except KeyError:
        return False
    if not is_codec_available(codec):
        return False

    source = _BoundedPeekReader(peek_more, _INNER_TAR_MAX_PROBE_BYTES)
    try:
        with open_codec_stream(
            codec,
            source,
            config=StreamConfig(
                streaming=True,
                seekable=True,
                use_rapidgzip=AcceleratorMode.OFF,
                use_indexed_bzip2=AcceleratorMode.OFF,
            ),
        ) as stream:
            head = stream.read(_INNER_TAR_PROBE_BYTES)
    except (ArchiveyError, OSError, ValueError):
        # Not decodable as this codec, or truncated before a full block -> not an inner tar.
        return False
    return head[257:262] == b"ustar"


def _resolve_single_file_or_tar(
    fmt: ArchiveFormat,
    base_confidence: "DetectionConfidence",
    base_detected_by: str,
    peek_more: Callable[[int], bytes],
) -> FormatInfo:
    """Upgrade a single-file-compressor match to its TAR combo when the payload is a tarball.

    A ``RAW_STREAM`` compressor (``.gz``/``.bz2``/``.xz``/…) is probed for an inner TAR; on a
    hit it becomes ``(TAR, <stream>)`` (e.g. ``TAR_GZ``) reported as ``PROBABLE`` /
    ``content_probe`` (the inner-TAR test is structural, weaker than an exact magic).
    Otherwise the original single-file/container match stands. ``peek_more`` gives the probe
    a bounded, non-consuming view of the source to decode from.
    """
    if (
        fmt.container == ContainerFormat.RAW_STREAM
        and fmt.stream != StreamFormat.UNCOMPRESSED
    ):
        if _probe_inner_tar(fmt.stream, peek_more):
            tar_fmt = ArchiveFormat(ContainerFormat.TAR, fmt.stream)
            return FormatInfo(tar_fmt, DetectionConfidence.PROBABLE, "content_probe")
    return FormatInfo(fmt, base_confidence, base_detected_by)


def _is_deferred_inner_tar(ext_fmt: ArchiveFormat, resolved: ArchiveFormat) -> bool:
    """Whether a TAR-combo extension over a bare-compressor result is a *benign* mismatch.

    ``foo.tar.gz`` (extension → ``TAR_GZ``) reported as bare ``GZ`` is the documented
    deferred case: the inner-TAR probe could not run (codec backend absent) or found no tar,
    so the bare compressor is reported and the inner-TAR determination is left to open time.
    That is not a real conflict, so it must not emit a warning.
    """
    return (
        resolved.container == ContainerFormat.RAW_STREAM
        and ext_fmt.container == ContainerFormat.TAR
        and ext_fmt.stream == resolved.stream
    )


def _warn_on_conflict(
    collector: DiagnosticCollector,
    name: str | None,
    ext_match: tuple[ArchiveFormat, str] | None,
    resolved: ArchiveFormat,
) -> None:
    if ext_match is None:
        return
    ext_fmt, extension = ext_match
    if ext_fmt == resolved or _is_deferred_inner_tar(ext_fmt, resolved):
        return
    message = (
        f"Format conflict for {name!r}: extension suggests {ext_fmt!r} but magic bytes "
        f"indicate {resolved!r}; using the magic-byte result."
    )
    collector.emit(
        code=DiagnosticCode.FORMAT_EXTENSION_CONFLICT,
        message=message,
        context=FormatConflictContext(
            source_name=name,
            extension=extension,
            extension_format=repr(ext_fmt),
            detected_format=repr(resolved),
        ),
        logger=logger,
    )


def detect_format(
    source: str | Path | BinaryIO,
    *,
    config: ArchiveyConfig | None = None,
    collector: DiagnosticCollector | None = None,
) -> FormatInfo:
    """Identify the archive format of ``source`` without fully opening it.

    Returns a :class:`FormatInfo`. Raises :class:`FormatDetectionError` when no magic
    pattern matches and no extension guess is available.

    ``collector``, when provided (e.g. from :func:`archivey.open_archive`), receives
    detection diagnostics into the prospective reader's shared collector. When omitted,
    a finite standalone collector is created from ``config`` (or the library default).
    """
    owned_collector = collector is None
    if owned_collector:
        effective_config = config if config is not None else DEFAULT_ARCHIVEY_CONFIG
        collector = collector_from_config(effective_config)
        detection_wm = None
    else:
        detection_wm = collector.watermark()

    info = _detect_format_body(source, collector)
    diagnostics = (
        collector.snapshot()
        if owned_collector
        else collector.snapshot(since=detection_wm)
    )
    return replace(info, diagnostics=diagnostics)


def _detect_format_body(
    source: str | Path | BinaryIO, collector: DiagnosticCollector
) -> FormatInfo:
    registry = get_registry()
    magic_entries = registry.magic_entries()
    extension_map = registry.extension_map()
    name = source_name(source)
    ext_match = _match_extension(name, extension_map)
    ext_fmt = ext_match[0] if ext_match is not None else None

    # Magic signals split by where they live: "near" ones fit in the default window; "far"
    # ones (ISO's CD001 at 32 769) need an extended peek that is only taken on demand, so the
    # common case never reads 32 KiB just to identify a ZIP/gz/tar in the first few bytes.
    near = [e for e in magic_entries if e.offset + len(e.magic) <= DETECTION_LIMIT]
    far = [e for e in magic_entries if e.offset + len(e.magic) > DETECTION_LIMIT]
    near_needed = max(
        DETECTION_LIMIT, max((e.offset + len(e.magic) for e in near), default=0)
    )
    data = _peek_prefix(source, near_needed)

    # The inner-TAR probe decodes the source through a bounded, non-consuming view built on
    # this callable: stream-oriented codecs reach the header from the first few KiB, while a
    # block codec (bzip2) may need a full block. Each peek is bounded and restores position /
    # buffers in the PeekableStream (like the prefix peek above), so a large-block .tar.bz2 is
    # not mis-reported as bare .bz2.
    def peek_more(length: int) -> bytes:
        return _peek_prefix(source, length)

    # 1. Exact magic in the default window. A single-file compressor is additionally probed
    #    for an inner TAR (so .tar.gz → TAR_GZ, not bare GZ).
    magic_fmt = _match_magic(data, near)
    if magic_fmt is not None:
        info = _resolve_single_file_or_tar(
            magic_fmt, DetectionConfidence.CERTAIN, "magic", peek_more
        )
        _warn_on_conflict(collector, name, ext_match, info.format)
        return info

    # 2. Formats without an exact magic, recognized by a content probe (Brotli decodes a
    #    prefix; zlib gates on its 2-byte header then decodes). A probe is skipped when its
    #    backend is absent, so detection falls through. A matching compressor is likewise
    #    probed for an inner TAR (so .tar.br → TAR_BROTLI).
    for probe_fmt, probe in registry.content_probes():
        if probe(data):
            info = _resolve_single_file_or_tar(
                probe_fmt, DetectionConfidence.PROBABLE, "content_probe", peek_more
            )
            _warn_on_conflict(collector, name, ext_match, info.format)
            return info

    # 3. Far magic (ISO's CD001 at offset 32 769): peek the extended 32 774-byte window on
    #    demand. A stream shorter than the window simply yields no match and falls through —
    #    it is never rejected solely for being too short for the ISO probe.
    if far:
        far_needed = max(e.offset + len(e.magic) for e in far)
        far_data = _peek_prefix(source, far_needed)
        far_fmt = _match_magic(far_data, far)
        if far_fmt is not None:
            _warn_on_conflict(collector, name, ext_match, far_fmt)
            return FormatInfo(far_fmt, DetectionConfidence.CERTAIN, "magic")

    # 4. Extension-only guess.
    if ext_fmt is not None:
        return FormatInfo(ext_fmt, DetectionConfidence.GUESS, "extension")

    raise FormatDetectionError(
        "Could not detect archive format: no magic-byte match and no usable file extension.",
        archive_name=name,
    )
