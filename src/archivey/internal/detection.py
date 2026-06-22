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

Content probes (Brotli), the inner-TAR probe, the ISO extended window, and SFX scanning
arrive with the backends they feed in later Phase-3 stages / Phase 7; this module is the
Stage-1 core.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import BinaryIO

from archivey.internal.errors import FormatDetectionError
from archivey.internal.logs import detection as logger
from archivey.internal.registry import get_registry
from archivey.internal.streams.codecs import Codec, is_codec_available
from archivey.internal.streams.peekable import DETECTION_LIMIT, PeekableStream
from archivey.internal.streams.streamtools import is_seekable, read_exact
from archivey.internal.types import ArchiveFormat

# The zlib 2-byte header (CMF/FLG) is not a true magic number — the same prefix begins
# many raw-deflate streams and can occur in arbitrary data — so a zlib magic match is the
# weakest entry in the table: it is consulted only after the stronger magics and the
# content probe, and a conflicting extension overrides it (see ``format-detection``).
_WEAK_MAGIC_FORMATS: frozenset[ArchiveFormat] = frozenset({ArchiveFormat.ZLIB})

# Bytes fed to a content probe (e.g. Brotli) — enough to trip a malformed-stream error
# without decompressing the whole payload.
_PROBE_PREFIX = 256


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
    magic_entries: list[tuple[int, bytes, ArchiveFormat]],
    *,
    weak: bool,
) -> ArchiveFormat | None:
    """Return the first matching magic, considering only weak or only strong entries."""
    for offset, magic, fmt in magic_entries:
        is_weak = fmt in _WEAK_MAGIC_FORMATS
        if is_weak != weak:
            continue
        if data[offset : offset + len(magic)] == magic:
            return fmt
    return None


def _brotli_content_probe(data: bytes) -> bool:
    """Whether a bounded prefix of ``data`` decompresses cleanly through Brotli.

    Brotli has no magic bytes, so it is recognized structurally. Skipped (returns
    ``False``, i.e. "no match") when the Brotli backend is not installed, so detection
    falls through to the ``.br`` extension guess. Operates on the already-peeked bytes, so
    it consumes nothing from the source.
    """
    if not is_codec_available(Codec.BROTLI):
        return False
    import brotli  # optional dep; guarded by is_codec_available above

    try:
        brotli.Decompressor().process(data[:_PROBE_PREFIX])
        return True
    except brotli.error:
        return False


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


def detect_format(source: str | Path | BinaryIO) -> FormatInfo:
    """Identify the archive format of ``source`` without fully opening it.

    Returns a :class:`FormatInfo`. Raises :class:`FormatDetectionError` when no magic
    pattern matches and no extension guess is available.
    """
    registry = get_registry()
    magic_entries = registry.magic_entries()
    extension_map = registry.extension_map()

    # Read enough to cover the deepest magic offset (e.g. TAR's ustar at 257, ISO's CD001
    # at 32 769), but at least the default detection window.
    needed = max(
        DETECTION_LIMIT,
        max((offset + len(magic) for offset, magic, _ in magic_entries), default=0),
    )
    data = _peek_prefix(source, needed)
    name = _source_extension_name(source)
    ext_fmt = _match_extension(name, extension_map)

    # 1. Strong (exact, multi-byte) magic wins, with a conflict warning vs the extension.
    strong_fmt = _match_magic(data, magic_entries, weak=False)
    if strong_fmt is not None:
        if ext_fmt is not None and ext_fmt != strong_fmt:
            logger.warning(
                "Format conflict for %r: extension suggests %r but magic bytes indicate "
                "%r; using the magic-byte result.",
                name,
                ext_fmt,
                strong_fmt,
            )
        return FormatInfo(strong_fmt, DetectionConfidence.CERTAIN, "magic")

    # 2. Weak magic (zlib's 2-byte header): a real but low-confidence signal. A conflicting
    #    extension overrides it (no warning — the weakness is by design); otherwise report
    #    it as PROBABLE rather than CERTAIN.
    weak_fmt = _match_magic(data, magic_entries, weak=True)
    if weak_fmt is not None:
        if ext_fmt is not None and ext_fmt != weak_fmt:
            return FormatInfo(ext_fmt, DetectionConfidence.GUESS, "extension")
        return FormatInfo(weak_fmt, DetectionConfidence.PROBABLE, "magic")

    # 3. Content probes for magic-less formats (Brotli). Skipped when the backend is absent.
    if _brotli_content_probe(data):
        return FormatInfo(ArchiveFormat.BROTLI, DetectionConfidence.PROBABLE, "content_probe")

    # 4. Extension-only guess.
    if ext_fmt is not None:
        return FormatInfo(ext_fmt, DetectionConfidence.GUESS, "extension")

    raise FormatDetectionError(
        "Could not detect archive format: no magic-byte match and no usable file extension.",
        archive_name=name,
    )
