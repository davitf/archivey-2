import abc
import os
from typing import BinaryIO, Callable, Collection, Iterator, List

from archivey.internal.io_helpers import is_stream
from archivey.types import (
    ArchiveFormat,
    ArchiveInfo,
    ArchiveMember,
    ExtractFilterFunc,
    ExtractionFilter,
    IteratorFilterFunc,
)


class ArchiveReader(abc.ABC):
    """
    Represents a readable archive, such as a ZIP or TAR file.

    Provides a uniform interface for listing, reading, and extracting files from
    archives, regardless of format. Use [open_archive()][archivey.open_archive] to
    obtain an instance of this class.
    """

    path_or_stream: str | BinaryIO
    path_str: str | None

    def __init__(
        self,
        archive_path: BinaryIO | str | bytes | os.PathLike[str],
        format: ArchiveFormat,
    ):
        """
        Initialize the ArchiveReader with a file path or stream and detected format.

        Args:
            archive_path: Path or binary stream of the archive.
            format: ArchiveFormat indicating the archive type.

        Raises:
            ValueError: If the input is not a supported type.
        """
        if is_stream(archive_path):
            self.path_str = None
            self.path_or_stream = archive_path

        elif isinstance(archive_path, (str, os.PathLike)):
            self.path_or_stream = self.path_str = str(archive_path)
        elif isinstance(archive_path, bytes):
            self.path_or_stream = self.path_str = archive_path.decode("utf-8")
        else:
            raise ValueError(
                f"Expected a stream, str, or bytes, got {type(archive_path)} {archive_path!r}"
            )

        self.format = format

    @abc.abstractmethod
    def close(self) -> None:
        """
        Close the archive and release any underlying resources.

        This method is idempotent (callable multiple times without error).
        It is automatically called when the reader is used as a context manager.
        """
        pass

    @abc.abstractmethod
    def get_members(self) -> List[ArchiveMember]:
        """
        Return a list of all members in the archive.

        For some formats (e.g. TAR), this may require reading the entire archive if no
        central directory is available. Always raises ValueError in streaming mode to
        avoid misuse.

        Returns:
            A list of ArchiveMember objects.

        Raises:
            ArchiveError: If member metadata cannot be read.
            ValueError: If the archive was opened in streaming mode.
        """
        pass

    @abc.abstractmethod
    def get_members_if_available(self) -> List[ArchiveMember] | None:
        """
        Return a list of members if available without full archive traversal.

        For formats with a central directory (e.g. ZIP), this is typically fast.
        Returns None if not readily available (e.g. TAR streams).

        Returns:
            A list of ArchiveMember objects, or None if unavailable.
        """
        pass

    @abc.abstractmethod
    def iter_members_with_streams(
        self,
        members: Collection[ArchiveMember | str]
        | Callable[[ArchiveMember], bool]
        | None = None,
        *,
        pwd: bytes | str | None = None,
        filter: IteratorFilterFunc | ExtractionFilter | None = None,
    ) -> Iterator[tuple[ArchiveMember, BinaryIO | None]]:
        """
        Iterate over archive members, yielding each with a readable stream if applicable.

        For each member, this yields a tuple `(ArchiveMember, stream)`. The `stream` is
        a binary file-like object for regular files, and `None` for non-file members.

        If the archive was opened in streaming mode, this method can only be called once.

        Parameters:
            members: A collection of `ArchiveMember` or filenames, or a predicate
                function that returns True for members to include. If `None`, all
                members are included.
            pwd: Optional password to use for encrypted members, if needed; by default,
                the password passed when opening the archive is used.
            filter: Optional filter or sanitizer applied to each member. Either
                a predefined `ExtractionFilter` policy, or a callable that returns
                a sanitized member or `None` to exclude it.

        Yields:
            Tuples of `(ArchiveMember, BinaryIO | None)`, one per selected member.
            For file members, the stream allows reading their content. For non-file
            members (e.g. directories or links), the stream is `None`.

            Streams are lazily opened only if accessed, so skipping unused members
            is efficient. Each stream is automatically closed when iteration advances
            to the next member or when the generator is closed.

        Raises:
            ArchiveEncryptedError: If a member is encrypted and `pwd` is missing or
                incorrect. (raised only when attempting to read a returned stream)
            ArchiveCorruptedError: If member data is found to be corrupted. (may be
                raised when retrieving the next item, or when attempting to read a
                returned stream)
            ArchiveIOError: If other I/O-related errors occur.
        """
        pass

    @abc.abstractmethod
    def get_archive_info(self) -> ArchiveInfo:
        """
        Return metadata about the archive as an ArchiveInfo object.

        Includes format, solidity, comments, and other archive-level information.

        Returns:
            An ArchiveInfo object.
        """
        pass

    @abc.abstractmethod
    def has_random_access(self) -> bool:
        """
        Return `True` if this archive supports random access to its members.

        Random access allows methods like `open()`, `get_members()`, and `extract()` to
        be used freely. This returns `False` if the archive was opened in streaming
        mode, in which case only a single pass through `iter_members_with_streams()` or
        `extractall()` is supported.
        supported.

        Random access allows methods like `open()`, `get_members()`, and `extract()` to
        work reliably. Returns `False` if the archive was opened from a non-seekable
        source (e.g. a streamed `.tar` file), in which case only a single pass through
        `iter_members_with_streams()` is allowed.

        Returns:
            `True` if random access is available; `False` if in streaming mode.
        """
        pass

    @abc.abstractmethod
    def get_member(self, member_or_filename: ArchiveMember | str) -> ArchiveMember:
        """
        Return an [ArchiveMember][archivey.ArchiveMember] for the given name or member.

        If a filename (str) is provided, looks up the corresponding member. If an
        ArchiveMember is provided, it is returned as-is after validating that it
        belongs to this archive. This is useful when accepting either form in a
        user-facing API.

        Args:
            member_or_filename: A filename or an existing ArchiveMember.

        Returns:
            The corresponding ArchiveMember.

        Raises:
            ArchiveMemberNotFoundError: If the name does not match any member.
        """
        pass

    @abc.abstractmethod
    def open(
        self, member_or_filename: ArchiveMember | str, *, pwd: bytes | str | None = None
    ) -> BinaryIO:
        """
        Open a specific member for reading and return a binary stream.

        Accepts either a filename (str) or an ArchiveMember. Filenames are resolved
        to members automatically. For symlinks, this returns the target file’s content.

        Requires random access support (see `has_random_access()`).

        Args:
            member_or_filename: The member or its filename.
            pwd: Optional password to use for encrypted members, if needed. By default,
                the password passed when opening the archive is used.

        Returns:
            A binary stream for reading the member's content.

        Raises:
            ArchiveMemberNotFoundError: If the member is not found.
            ArchiveMemberCannotBeOpenedError: If the member is not a file or a link
                that points to a file.
            ArchiveEncryptedError: If the member is encrypted and `pwd` is incorrect or
                not provided.
            ArchiveCorruptedError: If the compressed data is corrupted.
            ValueError: If the archive was opened in streaming mode.
        """
        pass

    @abc.abstractmethod
    def extract(
        self,
        member_or_filename: ArchiveMember | str,
        path: str | os.PathLike[str] | None = None,
        pwd: bytes | str | None = None,
    ) -> str | None:
        """
        Extract a single member to a target path.

        Args:
            member_or_filename: The member to extract.
            path: The path to extract to. Defaults to the current working directory.
            pwd: Optional password to use for encrypted members, if needed; by default,
                the password passed when opening the archive is used.

        Returns:
            The path of the extracted file, or None for non-file entries.

        Raises:
            ArchiveMemberNotFoundError: If the member is not found.
            ArchiveEncryptedError: If the member is encrypted and `pwd` is incorrect or
                not provided.
            ArchiveCorruptedError: If the compressed data is corrupted.
            ValueError: If the archive was opened in streaming mode.
        """
        pass

    @abc.abstractmethod
    def extractall(
        self,
        path: str | os.PathLike[str] | None = None,
        members: Collection[ArchiveMember | str]
        | Callable[[ArchiveMember], bool]
        | None = None,
        *,
        pwd: bytes | str | None = None,
        filter: ExtractFilterFunc | ExtractionFilter | None = None,
    ) -> dict[str, ArchiveMember]:
        """
        Extract all (or selected) members to a given directory.

        If the archive was opened in streaming mode, this method can only be called once.

        Args:
            path: Target directory. Defaults to the current working directory if `None`.
                The directory will be created if it doesn't exist.
            members: Optional. A collection of member names or `ArchiveMember` objects
                to extract. If None, all members are extracted. Can also be a callable
                that takes an `ArchiveMember` and returns `True` if it should be
                extracted.
            pwd: Optional password to use for encrypted members, if needed; by default,
                the password passed when opening the archive is used.
            filter: Optional filter or sanitizer applied to each member. Either
                a predefined `ExtractionFilter` policy, or a callable that returns
                a sanitized member or `None` to exclude it.

        Returns:
            A mapping from extracted file paths (including the target directory) to
            their corresponding `ArchiveMember` objects.

        Raises:
            ArchiveEncryptedError: If a member is encrypted and `pwd` is invalid or missing.
            ArchiveCorruptedError: If the archive is corrupted.
            ArchiveIOError: If other I/O-related issues occur.
            SameFileError: If extraction would overwrite a file in the archive itself.
        """
        pass

    @abc.abstractmethod
    def resolve_link(self, member: ArchiveMember) -> ArchiveMember | None:
        """
        Resolve a link member to its final non-link target.

        If the input is not a link, returns the member itself. For symlinks or hardlinks,
        follows the chain to the real target. If the link points to a file that is not
        in the archive, returns `None`.

        Args:
            member: The ArchiveMember to resolve.

        Returns:
            The resolved ArchiveMember, or None if resolution fails.
        """
        pass

    # Context manager support
    def __enter__(self) -> "ArchiveReader":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()
