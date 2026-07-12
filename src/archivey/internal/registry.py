"""Backend registry: maps ArchiveFormat to ReadBackend/WriteBackend classes.

Registration is **unconditional**: every known backend — core and optional alike —
registers when its module is imported. Availability is then derived centrally from the
optional dependency's module-or-``None`` sentinel, so the registry can report a tri-state,
compositional :class:`FormatSupport` (FULL / PARTIAL / NONE) and produce install-hint
errors, rather than silently dropping a format whose dependency is absent (see
``backend-registry``).
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from enum import Enum
from types import ModuleType
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from archivey.internal.base_reader import ReadBackend, WriteBackend

from archivey.exceptions import (
    UnsupportedFormatError,
    UnsupportedOperationError,
)
from archivey.internal.streams.codecs import (
    SINGLE_FILE_CODECS,
    STREAM_CODECS,
    Codec,
    codec_for_stream_format,
    codec_requirement,
    is_codec_available,
)
from archivey.types import (
    ArchiveFormat,
    ContainerFormat,
    MagicSignature,
    MissingComponent,
    StreamFormat,
)

__all__ = [
    "BackendRegistry",
    "FormatAvailability",
    "FormatSupport",
    "MissingComponent",
    "format_availability",
    "get_registry",
    "list_known_formats",
    "list_supported_formats",
    "register_reader",
    "register_writer",
]


class FormatSupport(Enum):
    """Tri-state readability of a known format (see ``backend-registry``)."""

    FULL = "full"  # backend usable AND every optional codec/tool it can use is present
    PARTIAL = (
        "partial"  # opens & lists; common members decode; some optional codec missing
    )
    NONE = "none"  # backend (or a single-codec format's sole codec) is unavailable


@dataclass(frozen=True)
class FormatAvailability:
    """The support level of a format plus the components needed to raise it."""

    format: ArchiveFormat
    support: FormatSupport
    missing: tuple[MissingComponent, ...] = ()  # empty when FULL


# Optional member-codecs each container can use, beyond the always-present stdlib codecs.
# A format is FULL when all of these are available, PARTIAL when some are missing (a
# multi-codec container still opens and lists). Single-codec formats (bare RAW_STREAM
# compressors and compressed tars) are handled separately: their sole stream codec
# missing makes them NONE, not PARTIAL.
#
# NOTE (Phase 3 → 7 gap): ZIP member *decode* currently goes through stdlib zipfile,
# which cannot decompress deflate64/ppmd (or zstd before Python 3.14) even when these
# codec packages are installed — such members raise UnsupportedFeatureError at read.
# This table describes the intended post-Phase-7 composition, where the codec layer is
# wired into ZIP member reads (see ``format-zip`` / ``compressed-streams``).
_CONTAINER_OPTIONAL_CODECS: dict[ContainerFormat, tuple[Codec, ...]] = {
    ContainerFormat.SEVEN_Z: (
        Codec.PPMD,
        Codec.DEFLATE64,
        Codec.ZSTD,
        Codec.BROTLI,
    ),
    ContainerFormat.ZIP: (Codec.DEFLATE64, Codec.ZSTD, Codec.PPMD),
}

# How each optional codec is obtained (install hint surfaced to the caller) now lives on
# the codec descriptors; read via codec_requirement() so a codec's package / extra / hint
# is declared in exactly one place (see ``backend-registry``).


def _optional(name: str) -> ModuleType | None:
    """Return the named module, or ``None`` when it (the optional extra) is not installed."""
    try:
        return importlib.import_module(name)
    except ImportError:
        return None


class BackendRegistry:
    """Central registry mapping formats to their read and write backends."""

    def __init__(self) -> None:
        self._readers: dict[ArchiveFormat, type[ReadBackend]] = {}
        self._writers: dict[ArchiveFormat, type[WriteBackend]] = {}
        # Unique reader classes in registration order (a class may serve several formats).
        self._reader_classes: list[type[ReadBackend]] = []

    def register_reader(self, backend_cls: type[ReadBackend]) -> None:
        if backend_cls not in self._reader_classes:
            self._reader_classes.append(backend_cls)
        for fmt in backend_cls.FORMATS:
            self._readers[fmt] = backend_cls

    def register_writer(self, backend_cls: type[WriteBackend]) -> None:
        for fmt in backend_cls.FORMATS:
            self._writers[fmt] = backend_cls

    # --- detection tables (aggregated from two sources) ----------------------------------
    # The container format backends (`ReadBackend.MAGIC`/`EXTENSIONS`/`CONTENT_PROBES`) and
    # the stream-codec objects (`STREAM_CODECS`). The detector reads both via these methods,
    # so a new standalone codec is detectable by adding one `StreamCodec` — no edits here.

    def magic_entries(self) -> list[MagicSignature]:
        """All exact magic signals across registered backends and the stream codecs."""
        entries: list[MagicSignature] = []
        for cls in self._reader_classes:
            entries.extend(cls.MAGIC)
        for codec in STREAM_CODECS:
            entries.extend(codec.magic)
        return entries

    def content_probes(self) -> list[tuple[ArchiveFormat, Callable[[bytes], bool]]]:
        """(format, probe) pairs for formats recognized by a content probe.

        Drawn from the backends and from the stream codecs that override the no-op base
        content probe (Brotli, which has no magic; zlib, whose 2-byte header is too
        unspecific to trust on its own).
        """
        probes: list[tuple[ArchiveFormat, Callable[[bytes], bool]]] = []
        for cls in self._reader_classes:
            probes.extend(cls.CONTENT_PROBES)
        for codec in SINGLE_FILE_CODECS:
            if codec.probes_content and codec.single_file_format is not None:
                probes.append((codec.single_file_format, codec.content_probe))
        return probes

    def extension_map(self) -> dict[str, ArchiveFormat]:
        """The merged ``extension -> format`` map across backends and the stream codecs."""
        merged: dict[str, ArchiveFormat] = {}
        for cls in self._reader_classes:
            merged.update(cls.EXTENSIONS)
        for codec in SINGLE_FILE_CODECS:
            if codec.single_file_format is not None:
                for ext in codec.extensions:
                    merged[ext] = codec.single_file_format
        return merged

    # --- availability --------------------------------------------------------------------

    def _backend_available(self, backend_cls: type[ReadBackend]) -> bool:
        dep = backend_cls.OPTIONAL_DEPENDENCY
        return dep is None or _optional(dep) is not None

    def format_availability(self, fmt: ArchiveFormat) -> FormatAvailability:
        """Compute the tri-state support of ``fmt`` compositionally (see ``backend-registry``)."""
        backend_cls = self._readers.get(fmt)
        if backend_cls is None:
            return FormatAvailability(fmt, FormatSupport.NONE, ())

        if not self._backend_available(backend_cls):
            dep = backend_cls.OPTIONAL_DEPENDENCY
            assert (
                dep is not None
            )  # _backend_available only returns False when dep is set
            hint = backend_cls.INSTALL_HINT or f"install {dep}"
            return FormatAvailability(
                fmt,
                FormatSupport.NONE,
                (MissingComponent(dep, hint),),
            )

        # The format's own stream codec — a bare single-file compressor's sole codec, or
        # the outer codec of a compressed tar (`tar.<codec>`). With it missing the archive
        # cannot even be listed, so the format is NONE (the single-codec rule in
        # ``backend-registry``), not PARTIAL.
        if fmt.stream != StreamFormat.UNCOMPRESSED:
            stream_codec = codec_for_stream_format(fmt.stream)
            if not is_codec_available(stream_codec):
                requirement = codec_requirement(stream_codec)
                assert requirement is not None  # an optional codec always declares one
                return FormatAvailability(fmt, FormatSupport.NONE, (requirement,))

        # Backend + stream codec usable; fold in the optional *member* codecs the
        # container can use. A multi-codec container missing some still opens -> PARTIAL.
        missing: list[MissingComponent] = []
        for codec in _CONTAINER_OPTIONAL_CODECS.get(fmt.container, ()):
            if not is_codec_available(codec):
                requirement = codec_requirement(codec)
                assert requirement is not None  # an optional codec always declares one
                missing.append(requirement)

        # ZIP exception (until Phase 7): member *data* still decodes via stdlib zipfile,
        # which cannot use the optional codecs (deflate64/PPMd/zstd) even when installed,
        # so ZIP never reports FULL — it is PARTIAL regardless of codec installation. The
        # `missing` list still names absent packages, and is empty when all are present
        # (the read-time gap is implementation stage, not a missing install). See the
        # `backend-registry` / `format-zip` Phase 5 deltas.
        if fmt.container == ContainerFormat.ZIP:
            return FormatAvailability(fmt, FormatSupport.PARTIAL, tuple(missing))

        if not missing:
            return FormatAvailability(fmt, FormatSupport.FULL, ())
        return FormatAvailability(fmt, FormatSupport.PARTIAL, tuple(missing))

    # --- selection -----------------------------------------------------------------------

    def reader_for_format(self, fmt: ArchiveFormat) -> type[ReadBackend]:
        availability = self.format_availability(fmt)
        if availability.support is FormatSupport.NONE:
            backend_cls = self._readers.get(fmt)
            if backend_cls is None:
                raise UnsupportedFormatError(
                    f"No read backend registered for format {fmt!r}",
                    source_format=fmt,
                )
            hints = "; ".join(
                f"{m.name} ({m.install_hint})" for m in availability.missing
            )
            raise UnsupportedFormatError(
                f"Format {fmt!r} is not available: missing {hints}",
                source_format=fmt,
            )
        return self._readers[fmt]

    def writer_for_format(self, fmt: ArchiveFormat) -> type[WriteBackend]:
        if fmt not in self._writers:
            raise UnsupportedOperationError(
                f"No write backend registered for format {fmt!r}",
                source_format=fmt,
            )
        return self._writers[fmt]

    # --- queries -------------------------------------------------------------------------

    def list_known_formats(self) -> list[ArchiveFormat]:
        """Every format the registry knows, including those with support NONE."""
        return list(self._readers.keys())

    def list_supported_formats(self) -> list[ArchiveFormat]:
        """Formats readable now — support FULL or PARTIAL (NONE excluded)."""
        return [
            fmt
            for fmt in self._readers
            if self.format_availability(fmt).support is not FormatSupport.NONE
        ]

    def list_writable_formats(self) -> list[ArchiveFormat]:
        return list(self._writers.keys())


# Module-level singleton
_registry = BackendRegistry()


def register_reader(backend_cls: type[ReadBackend]) -> None:
    _registry.register_reader(backend_cls)


def register_writer(backend_cls: type[WriteBackend]) -> None:
    _registry.register_writer(backend_cls)


def get_registry() -> BackendRegistry:
    return _registry


def format_availability(fmt: ArchiveFormat) -> FormatAvailability:
    """Public query: the tri-state support level of ``fmt`` and its missing components."""
    return _registry.format_availability(fmt)


def list_supported_formats() -> list[ArchiveFormat]:
    """Public query: formats readable now (support FULL or PARTIAL)."""
    return _registry.list_supported_formats()


def list_known_formats() -> list[ArchiveFormat]:
    """Public query: every format the registry knows, including support NONE."""
    return _registry.list_known_formats()
