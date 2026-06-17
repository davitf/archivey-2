"""Archivey exception hierarchy."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from archivey.internal._types import ArchiveFormat


class ArchiveyError(Exception):
    """Root of all Archivey exceptions."""

    def __init__(
        self,
        message: str,
        *,
        source_format: "ArchiveFormat | None" = None,
        archive_name: str | None = None,
        member_name: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.source_format = source_format
        self.archive_name = archive_name
        self.member_name = member_name

    def __str__(self) -> str:
        parts = [self.message]
        if self.archive_name:
            parts.append(f"archive={self.archive_name!r}")
        if self.member_name:
            parts.append(f"member={self.member_name!r}")
        if self.source_format:
            parts.append(f"format={self.source_format!r}")
        if len(parts) == 1:
            return self.message
        return f"{self.message} ({', '.join(parts[1:])})"


class OpenError(ArchiveyError):
    """Cannot open or parse archive header."""


class FormatDetectionError(OpenError):
    """Could not detect archive format."""


class UnsupportedFormatError(OpenError):
    """Format detected but no backend available."""


class StreamNotSeekableError(OpenError):
    """Source is non-seekable but this format/backend needs seek."""


class ReadError(ArchiveyError):
    """Error reading a member."""


class CorruptionError(ReadError):
    """CRC mismatch or bad data block."""


class TruncatedError(ReadError):
    """Unexpected EOF."""


class EncryptionError(ReadError):
    """Password required or wrong password."""


class LinkTargetNotFoundError(ReadError):
    """A symlink/hardlink target is absent from the archive."""


class WriteError(ArchiveyError):
    """Error writing an archive."""


class ExtractionError(ArchiveyError):
    """Error extracting a member to disk."""


class FilterRejectionError(ExtractionError):
    """Safety filter blocked the member."""


class PathTraversalError(FilterRejectionError):
    """Path traversal attempt (../ or absolute path)."""


class SymlinkEscapeError(FilterRejectionError):
    """Symlink resolves outside destination."""


class SpecialFileError(FilterRejectionError):
    """Device node, FIFO, socket — always rejected."""


class UnsupportedFeatureError(ArchiveyError):
    """Recognized but unhandled feature/variant/codec."""


class PackageNotInstalledError(ArchiveyError):
    """A required optional package or external tool is absent."""


class UnsupportedOperationError(ArchiveyError):
    """API misuse: operation not valid for this reader's mode."""
