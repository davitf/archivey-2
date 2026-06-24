"""Archivey — Python library for reading, streaming, and safely extracting archives."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("archivey")
except PackageNotFoundError:
    __version__ = "0.0.0+unknown"

from archivey.internal.cost import (
    AccessCost,
    CostReceipt,
    ListingCost,
    StreamCapability,
)
from archivey.internal.detection import (
    DetectionConfidence,
    FormatInfo,
    detect_format,
)
from archivey.internal.errors import (
    ArchiveyError,
    CorruptionError,
    EncryptionError,
    ExtractionError,
    FilterRejectionError,
    FormatDetectionError,
    LinkTargetNotFoundError,
    OpenError,
    PackageNotInstalledError,
    PathTraversalError,
    ReadError,
    SpecialFileError,
    StreamNotSeekableError,
    SymlinkEscapeError,
    TruncatedError,
    UnsupportedFeatureError,
    UnsupportedFormatError,
    UnsupportedOperationError,
    WriteError,
)
from archivey.internal.open_archive import open_archive
from archivey.internal.reader import ArchiveReader
from archivey.internal.registry import (
    FormatAvailability,
    FormatSupport,
    MissingComponent,
    format_availability,
    list_known_formats,
    list_supported_formats,
)
from archivey.internal.types import (
    ArchiveFormat,
    ArchiveInfo,
    ArchiveMember,
    CompressionAlgorithm,
    CompressionMethod,
    ContainerFormat,
    CreateSystem,
    MemberType,
    StreamFormat,
)

__all__ = [
    "__version__",
    "open_archive",
    "detect_format",
    "FormatInfo",
    "DetectionConfidence",
    "format_availability",
    "list_supported_formats",
    "list_known_formats",
    "FormatSupport",
    "FormatAvailability",
    "MissingComponent",
    "ArchiveReader",
    "ArchiveFormat",
    "ContainerFormat",
    "StreamFormat",
    "ArchiveMember",
    "ArchiveInfo",
    "MemberType",
    "CompressionAlgorithm",
    "CompressionMethod",
    "CreateSystem",
    "CostReceipt",
    "ListingCost",
    "AccessCost",
    "StreamCapability",
    "ArchiveyError",
    "OpenError",
    "FormatDetectionError",
    "UnsupportedFormatError",
    "StreamNotSeekableError",
    "ReadError",
    "CorruptionError",
    "TruncatedError",
    "EncryptionError",
    "LinkTargetNotFoundError",
    "WriteError",
    "ExtractionError",
    "FilterRejectionError",
    "PathTraversalError",
    "SymlinkEscapeError",
    "SpecialFileError",
    "UnsupportedFeatureError",
    "PackageNotInstalledError",
    "UnsupportedOperationError",
]

# Register the bundled backends eagerly (each module self-registers on import), so the
# availability queries (list_supported_formats / format_availability) work immediately
# after `import archivey`, without first calling open_archive().
import archivey.formats  # noqa: E402,F401

# Keep the importlib.metadata helpers out of the public `archivey` namespace
# (so `__all__` stays the single, hand-curated description of the public API).
del PackageNotFoundError, version
