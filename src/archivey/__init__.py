"""Archivey — Python library for reading, streaming, and safely extracting archives."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("archivey")
except PackageNotFoundError:
    __version__ = "0.0.0+unknown"

from archivey.internal.api import open_archive
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
from archivey.internal.intent import (
    AccessCost,
    CostReceipt,
    Intent,
    ListingCost,
    StreamCapability,
)
from archivey.internal.reader import ArchiveReader
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
    "Intent",
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

# Keep the importlib.metadata helpers out of the public `archivey` namespace
# (so `__all__` stays the single, hand-curated description of the public API).
del PackageNotFoundError, version
