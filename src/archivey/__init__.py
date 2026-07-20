"""Archivey — Python library for reading, streaming, and safely extracting archives.

Public surface layout (this package root only — not ``internal`` / ``cli``):

- :mod:`archivey.core` — ``open_archive`` / ``open_stream`` / ``extract`` / detection
- :mod:`archivey.reader` — ``ArchiveReader`` ABC
- :mod:`archivey.types` — formats, members, compression
- :mod:`archivey.config` — ``ArchiveyConfig``, limits, passwords, accelerators
- :mod:`archivey.cost` — listing/access cost receipt
- :mod:`archivey.diagnostics` — advisory codes, summaries, extraction reports
- :mod:`archivey.exceptions` — error hierarchy
- :mod:`archivey.measurement` — optional I/O counters

Names in ``__all__`` are the documented API. A few advanced types are also imported
here (so ``from archivey import …`` keeps working) but omitted from ``__all__`` so
they do not crowd the generated API reference — see the ``# noqa: F401`` imports.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("archivey")
except PackageNotFoundError:
    __version__ = "0.0.0+unknown"

from archivey.config import (
    DEFAULT_ARCHIVEY_CONFIG,
    RAPIDGZIP_AUTO_MIN_COMPRESSED_SIZE,  # noqa: F401 — advanced; not in __all__
    AcceleratorMode,
    ArchiveyConfig,
    ExtractionLimits,
    ListingLimits,
    PasswordInput,
    PasswordProvider,
    PasswordRequest,
)
from archivey.core import (
    DetectionConfidence,
    FormatAvailability,
    FormatInfo,
    FormatSupport,
    MissingComponent,
    detect_format,
    extract,
    format_availability,
    list_known_formats,
    list_supported_formats,
    open_archive,
    open_stream,
)
from archivey.cost import (
    AccessCost,
    CostReceipt,
    ListingCost,
    StreamCapability,
)
from archivey.diagnostics import (
    # Context payloads: importable for isinstance/match; omitted from __all__.
    ArchiveEofContext,  # noqa: F401
    Diagnostic,
    DiagnosticCode,
    DiagnosticContext,
    DiagnosticDisposition,
    DiagnosticPolicy,
    DiagnosticSeverity,
    DiagnosticSummary,
    DigestContext,  # noqa: F401
    ExtractionOutcomeContext,  # noqa: F401
    ExtractionReport,
    FormatConflictContext,  # noqa: F401
    MemberListReport,
    MemberTimestampContext,  # noqa: F401
    NameCollisionContext,  # noqa: F401
    NameEncodingContext,  # noqa: F401
    NameNormalizationContext,  # noqa: F401
    NameSanitizedContext,  # noqa: F401
    OnDiagnostic,
    ScanRaceContext,  # noqa: F401
    SeekIndexContext,  # noqa: F401
    StreamRewindContext,  # noqa: F401
    SymlinkTargetContext,  # noqa: F401
)
from archivey.exceptions import (
    ArchiveyError,
    ArchiveyUsageError,
    ConcurrentAccessError,
    CorruptionError,
    DiagnosticRaisedError,
    EncryptionError,
    ExtractionError,
    FilterRejectionError,
    FormatDetectionError,
    LinkTargetNotFoundError,
    OpenError,
    PackageNotInstalledError,
    PathTraversalError,
    ReadError,
    ResourceLimitError,
    SpecialFileError,
    StreamNotSeekableError,
    SymlinkEscapeError,
    TruncatedError,
    UnportableNameError,
    UnsupportedFeatureError,
    UnsupportedFormatError,
    UnsupportedOperationError,
    WriteError,  # noqa: F401 — write API not shipped yet; kept importable
)
from archivey.internal.extraction_types import (
    ExtractionPolicy,
    ExtractionProgress,
    ExtractionResult,
    ExtractionStatus,
    MemberFilter,
    OnError,
    OverwritePolicy,
)
from archivey.internal.streams.archive_stream import ArchiveStream
from archivey.measurement import IoStats, enable_measurement
from archivey.reader import ArchiveReader, MemberSelector
from archivey.types import (
    ArchiveFormat,
    ArchiveInfo,
    ArchiveMember,
    CompressionAlgorithm,
    CompressionMethod,
    ContainerFormat,
    CreateSystem,
    HashAlgorithm,
    MemberStreams,
    MemberType,
    StreamFormat,
    crc32_digest,
)

__all__ = [
    "__version__",
    "open_archive",
    "open_stream",
    "extract",
    "ArchiveyConfig",
    "DEFAULT_ARCHIVEY_CONFIG",
    "ExtractionLimits",
    "ListingLimits",
    "AcceleratorMode",
    "PasswordInput",
    "PasswordRequest",
    "PasswordProvider",
    "OnDiagnostic",
    "ExtractionPolicy",
    "OverwritePolicy",
    "OnError",
    "ExtractionStatus",
    "ExtractionProgress",
    "ExtractionResult",
    "ExtractionReport",
    "MemberListReport",
    "MemberSelector",
    "MemberFilter",
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
    "ArchiveStream",
    "ArchiveFormat",
    "ContainerFormat",
    "StreamFormat",
    "ArchiveMember",
    "ArchiveInfo",
    "MemberType",
    "MemberStreams",
    "CompressionAlgorithm",
    "CompressionMethod",
    "CreateSystem",
    "HashAlgorithm",
    "crc32_digest",
    "CostReceipt",
    "ListingCost",
    "AccessCost",
    "StreamCapability",
    "IoStats",
    "enable_measurement",
    "Diagnostic",
    "DiagnosticCode",
    "DiagnosticContext",
    "DiagnosticSeverity",
    "DiagnosticDisposition",
    "DiagnosticPolicy",
    "DiagnosticSummary",
    "DiagnosticRaisedError",
    "ArchiveyError",
    "ArchiveyUsageError",
    "ConcurrentAccessError",
    "OpenError",
    "FormatDetectionError",
    "UnsupportedFormatError",
    "StreamNotSeekableError",
    "ReadError",
    "CorruptionError",
    "TruncatedError",
    "EncryptionError",
    "LinkTargetNotFoundError",
    "ExtractionError",
    "FilterRejectionError",
    "PathTraversalError",
    "SymlinkEscapeError",
    "SpecialFileError",
    "UnportableNameError",
    "ResourceLimitError",
    "UnsupportedFeatureError",
    "PackageNotInstalledError",
    "UnsupportedOperationError",
]

# Eager backend registration so list_supported_formats / format_availability work
# immediately after `import archivey` (open_archive also imports as a safety net).
import archivey.internal.backends  # noqa: E402,F401

# Keep importlib.metadata helpers out of the public namespace (__all__ is authoritative).
del PackageNotFoundError, version
