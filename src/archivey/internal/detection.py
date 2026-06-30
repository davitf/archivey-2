"""Format detection: ``detect_format()`` and the ``FormatInfo`` it returns.

Detection is **magic-first** (an exact magic-byte match at the expected offset →
``CERTAIN``) with an extension fallback (``GUESS``). The magic and extension tables are
not hand-maintained here: each registered backend declares its ``MAGIC`` / ``EXTENSIONS``
as data and the detector aggregates them, so a new format becomes detectable by
registering its backend (see ``format-detection`` and ``backend-registry``).

Detection never consumes bytes from the source: paths are opened and closed; seekable
streams are read and rewound to position 0; a non-seekable stream must be wrapped in a
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

import io
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import BinaryIO

from archivey.internal.errors import ArchiveyError, FormatDetectionError
from archivey.internal.logs import detection as logger
from archivey.internal.registry import get_registry
from archivey.internal.streams.peekable import DETECTION_LIMIT, PeekableStream
from archivey.internal.streams.streamtools import is_seekable, read_exact
from archivey.internal.types import (
    ArchiveFormat,
    ContainerFormat,
    MagicSignature,
    StreamFormat,
)

# Decompressed bytes needed to see a TAR ``ustar`` signature at offset 257 (one 512-byte
# header block covers it).
_INNER_TAR_PROBE_BYTES = 512


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
    payload_offset: int = 0  # nonzero only for SFX archives (is-SFX == payload_offset > 0)


def _source_extension_name(source: object) -> str | None:
    """The filename to match an extension against, or ``None`` when the source is anonymous."""
    if isinstance(source, (str, Path)):
        return str(source)
    name = getattr(source, "name", None)
    return name if isinstance(name, str) else None


def _peek_prefix(source: str | Path | BinaryIO, length: int) -> bytes:
    """Return the source's first ``length`` bytes without consuming them.

    Paths are opened and closed; a :class:`PeekableStream` is peeked; a seekable stream is
    read and rewound to position 0. A raw non-seekable stream would lose the prefix, so the
    caller (the opener) must wrap it in a ``PeekableStream`` first.
    """
    if isinstance(source, (str, Path)):
        with open(source, "rb") as f:
            return read_exact(f, length)
    if isinstance(source, PeekableStream):
        return source.peek(length)
    if is_seekable(source):
        data = read_exact(source, length)
        source.seek(0)
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
) -> ArchiveFormat | None:
    if name is None:
        return None
    lowered = name.lower()
    # Longest extension wins so ".tar.gz" beats ".gz".
    for ext in sorted(extension_map, key=len, reverse=True):
        if lowered.endswith(ext.lower()):
            return extension_map[ext]
    return None


def _probe_inner_tar(prefix: bytes, stream_format: StreamFormat) -> bool:
    """Whether decompressing ``prefix`` yields a TAR (``ustar`` at offset 257).

    The codec layer decodes a bounded prefix; the inner ``ustar`` magic confirms a tarball
    wrapped in the compressor. Returns ``False`` (deferring the determination to open time)
    when the codec backend is absent or the prefix does not decode to a TAR header.
    """
    # Imported here rather than at module load to avoid a detection<->codecs import cycle.
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
    try:
        with open_codec_stream(codec, io.BytesIO(prefix)) as stream:
            head = stream.read(_INNER_TAR_PROBE_BYTES)
    except (ArchiveyError, OSError, ValueError):
        return False
    return head[257:262] == b"ustar"


def _resolve_single_file_or_tar(
    data: bytes,
    fmt: ArchiveFormat,
    base_confidence: "DetectionConfidence",
    base_detected_by: str,
) -> FormatInfo:
    """Upgrade a single-file-compressor match to its TAR combo when the payload is a tarball.

    A ``RAW_STREAM`` compressor (``.gz``/``.bz2``/``.xz``/…) is probed for an inner TAR; on a
    hit it becomes ``(TAR, <stream>)`` (e.g. ``TAR_GZ``) reported as ``PROBABLE`` /
    ``content_probe`` (the inner-TAR test is structural, weaker than an exact magic).
    Otherwise the original single-file/container match stands.
    """
    if fmt.container == ContainerFormat.RAW_STREAM and fmt.stream != StreamFormat.UNCOMPRESSED:
        if _probe_inner_tar(data, fmt.stream):
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
    name: str | None, ext_fmt: ArchiveFormat | None, resolved: ArchiveFormat
) -> None:
    if (
        ext_fmt is not None
        and ext_fmt != resolved
        and not _is_deferred_inner_tar(ext_fmt, resolved)
    ):
        logger.warning(
            "Format conflict for %r: extension suggests %r but magic bytes indicate "
            "%r; using the magic-byte result.",
            name,
            ext_fmt,
            resolved,
        )


def detect_format(source: str | Path | BinaryIO) -> FormatInfo:
    """Identify the archive format of ``source`` without fully opening it.

    Returns a :class:`FormatInfo`. Raises :class:`FormatDetectionError` when no magic
    pattern matches and no extension guess is available.
    """
    registry = get_registry()
    magic_entries = registry.magic_entries()
    extension_map = registry.extension_map()
    name = _source_extension_name(source)
    ext_fmt = _match_extension(name, extension_map)

    # Magic signals split by where they live: "near" ones fit in the default window; "far"
    # ones (ISO's CD001 at 32 769) need an extended peek that is only taken on demand, so the
    # common case never reads 32 KiB just to identify a ZIP/gz/tar in the first few bytes.
    near = [e for e in magic_entries if e.offset + len(e.magic) <= DETECTION_LIMIT]
    far = [e for e in magic_entries if e.offset + len(e.magic) > DETECTION_LIMIT]
    near_needed = max(
        DETECTION_LIMIT, max((e.offset + len(e.magic) for e in near), default=0)
    )
    data = _peek_prefix(source, near_needed)

    # 1. Exact magic in the default window. A single-file compressor is additionally probed
    #    for an inner TAR (so .tar.gz → TAR_GZ, not bare GZ).
    magic_fmt = _match_magic(data, near)
    if magic_fmt is not None:
        info = _resolve_single_file_or_tar(
            data, magic_fmt, DetectionConfidence.CERTAIN, "magic"
        )
        _warn_on_conflict(name, ext_fmt, info.format)
        return info

    # 2. Formats without an exact magic, recognized by a content probe (Brotli decodes a
    #    prefix; zlib gates on its 2-byte header then decodes). A probe is skipped when its
    #    backend is absent, so detection falls through. A matching compressor is likewise
    #    probed for an inner TAR (so .tar.br → TAR_BROTLI).
    for probe_fmt, probe in registry.content_probes():
        if probe(data):
            return _resolve_single_file_or_tar(
                data, probe_fmt, DetectionConfidence.PROBABLE, "content_probe"
            )

    # 3. Far magic (ISO's CD001 at offset 32 769): peek the extended 32 774-byte window on
    #    demand. A stream shorter than the window simply yields no match and falls through —
    #    it is never rejected solely for being too short for the ISO probe.
    if far:
        far_needed = max(e.offset + len(e.magic) for e in far)
        far_data = _peek_prefix(source, far_needed)
        far_fmt = _match_magic(far_data, far)
        if far_fmt is not None:
            _warn_on_conflict(name, ext_fmt, far_fmt)
            return FormatInfo(far_fmt, DetectionConfidence.CERTAIN, "magic")

    # 4. Extension-only guess.
    if ext_fmt is not None:
        return FormatInfo(ext_fmt, DetectionConfidence.GUESS, "extension")

    raise FormatDetectionError(
        "Could not detect archive format: no magic-byte match and no usable file extension.",
        archive_name=name,
    )
