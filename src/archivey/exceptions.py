"""
Custom exceptions raised by Archivey.

The base `ArchiveError` can be accessed from the `archivey` module. More specific
subtypes are defined here to allow fine-grained error handling when needed.
"""


# Base exception for all archive-related errors
class ArchiveError(Exception):
    """Base exception for all archive-related errors raised by Archivey."""

    def __init__(
        self,
        message: str,
        archive_path: str | None = None,
        member_name: str | None = None,
    ):
        super().__init__(message)
        self.archive_path = archive_path
        self.member_name = member_name

    def __str__(self):
        base = super().__str__()
        if self.archive_path:
            base = f"{base} (in {self.archive_path})"
        if self.member_name:
            base = f"{base} (when processing {self.member_name})"
        return base


# Errors while reading or parsing archive data
class ArchiveReadError(ArchiveError):
    """Base class for errors while reading or decoding the archive contents."""

    pass


class ArchiveUnsupportedFeatureError(ArchiveReadError):
    """Raised when an archive format or feature is not supported."""

    pass


class ArchiveCorruptedError(ArchiveReadError):
    """Raised when an archive is detected as corrupted, incomplete, or invalid."""

    pass


class ArchiveEOFError(ArchiveCorruptedError):
    """Raised when an unexpected end-of-file is encountered while reading an archive."""

    pass


class ArchiveStreamNotSeekableError(ArchiveReadError):
    """
    Raised when a non-seekable stream is passed to `open_archive()` or
    `open_compressed_stream()`, but the archive format or backend library
    requires a seekable input stream.
    """

    pass


# Errors related to archive members
class ArchiveMemberError(ArchiveError):
    """Base class for errors related to archive members."""

    pass


class ArchiveMemberNotFoundError(ArchiveMemberError):
    """Raised when a requested member is not found within the archive."""

    pass


class ArchiveMemberCannotBeOpenedError(ArchiveMemberError):
    """
    Raised when a member cannot be opened for reading,
    typically because it's a directory, special file, or unresolved link.
    """

    pass


class ArchiveLinkTargetNotFoundError(ArchiveMemberError):
    """
    Raised when a symbolic or hard link within the archive points to a target
    that cannot be found within the same archive.
    """

    pass


# Errors related to writing extracted files
class ArchiveExtractionError(ArchiveError):
    """Base class for errors encountered during extraction to the filesystem."""

    pass


class ArchiveFileExistsError(ArchiveExtractionError):
    """
    Raised during extraction if a file to be written already exists and
    the overwrite mode prevents overwriting it.
    """

    pass


# Other specialized errors
class ArchiveEncryptedError(ArchiveError):
    """
    Raised when an archive or member is encrypted and either no password
    was provided, or the provided password is incorrect.
    """

    pass


class ArchiveFilterError(ArchiveError):
    """Raised when a filter rejects a member due to unsafe properties."""

    pass


class ArchiveNotSupportedError(ArchiveError):
    """Raised when the detected archive format is not supported by Archivey."""

    pass


class PackageNotInstalledError(ArchiveError):
    """
    Raised when a required third-party library or package for handling a specific
    archive format is not installed in the environment.
    """

    pass
