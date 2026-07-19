"""Archivey exception hierarchy.

Two roots (intentional):

- :class:`ArchiveyError` — archive / environment / format problems. Safe to catch
  broadly when wrapping untrusted input.
- :class:`ArchiveyUsageError` — caller API misuse. Deliberately **outside** the
  ``ArchiveyError`` tree so ``except ArchiveyError`` does not hide bugs in calling
  code.

Under ``ArchiveyError``, names track the failing phase: open → read → extract,
plus feature/package/resource limits.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from archivey.diagnostics import Diagnostic
    from archivey.types import ArchiveFormat


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
            # Human label (ZIP / TAR_GZ / SEVEN_Z), not ArchiveFormat.ZIP repr.
            parts.append(f"format={self.source_format.display_name}")
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


class UnportableNameError(FilterRejectionError):
    """A member name is unsafe on the destination OS under the active policy.

    Covers the cross-platform name hazards (see ``safe-extraction``): Windows-reserved
    device names, a trailing dot/space, or ``:`` in a path segment, rejected under
    ``STRICT`` on every platform (``STANDARD`` rejects the reserved/``:`` subset).
    """


class ResourceLimitError(ArchiveyError):
    """A configured listing or extraction resource limit was exceeded.

    Covers :class:`~archivey.config.ListingLimits` materialization caps and
    :class:`~archivey.config.ExtractionLimits` bomb guards. Sibling of
    :class:`ExtractionError` (not a subclass): limit trips are not filter/path failures.
    """


class UnsupportedFeatureError(ArchiveyError):
    """Recognized but unhandled feature/variant/codec."""


class PackageNotInstalledError(ArchiveyError):
    """A required optional package or external tool is absent."""


class UnsupportedOperationError(ArchiveyError):
    """Operation not valid for this archive, format, backend, or access mode.

    Describes what an archive or mode cannot provide — not a bug in calling code.
    Caller misuse (wrong-reader identity, post-close use, undeclared concurrent
    streams) raises :class:`ArchiveyUsageError` instead.
    """


class ArchiveyUsageError(Exception):
    """Caller misuse of the Archivey API — deliberately not an :class:`ArchiveyError`.

    ``except ArchiveyError`` wraps archive/environment problems; usage errors indicate
    a bug in calling code and must not be swallowed by those handlers.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    def __str__(self) -> str:
        return self.message


class ConcurrentAccessError(ArchiveyUsageError):
    """A second overlapping member stream was opened without ``MemberStreams.CONCURRENT``.

    The message includes the ``open_archive()`` call site so the error points at where
    the capability should have been declared.
    """


class DiagnosticRaisedError(ArchiveyError):
    """A diagnostic was escalated to an error via :class:`~archivey.diagnostics.DiagnosticPolicy`.

    Always-stop: extraction MUST NOT catch this as a per-member failure under
    ``OnError.CONTINUE``. Carries the escalated :class:`~archivey.diagnostics.Diagnostic`.
    """

    def __init__(
        self,
        message: str,
        *,
        diagnostic: "Diagnostic",
        source_format: "ArchiveFormat | None" = None,
        archive_name: str | None = None,
        member_name: str | None = None,
    ) -> None:
        super().__init__(
            message,
            source_format=source_format,
            archive_name=archive_name,
            member_name=member_name,
        )
        self.diagnostic = diagnostic
