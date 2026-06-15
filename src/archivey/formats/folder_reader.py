import logging
import os
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Iterator, Optional

from archivey.exceptions import (
    ArchiveError,
    ArchiveMemberNotFoundError,
)
from archivey.internal.base_reader import BaseArchiveReader
from archivey.internal.utils import get_ownership_from_stat
from archivey.types import (
    ArchiveFormat,
    ArchiveInfo,
    ArchiveMember,
    MemberType,
)

logger = logging.getLogger(__name__)


class FolderReader(BaseArchiveReader):
    """
    Reads a folder on the filesystem as an archive.
    """

    def __init__(
        self,
        format: ArchiveFormat,
        archive_path: BinaryIO | str | bytes | os.PathLike[str],
        pwd: bytes | str | None = None,
        streaming_only: bool = False,
    ):
        super().__init__(
            ArchiveFormat.FOLDER,
            archive_path,
            streaming_only=streaming_only,
            members_list_supported=True,
            pwd=None,
        )

        if format != ArchiveFormat.FOLDER:
            raise ValueError(f"Unsupported archive format: {format}")

        if pwd is not None:
            raise ValueError("Folders do not support password protection")

        if self.path_str is None:
            raise ValueError("FolderReader cannot be opened from a stream")

        self.path = Path(self.path_str).resolve()  # Store absolute path

        if not self.path.is_dir():
            raise ValueError(f"Path is not a directory: {self.path}")

        self._closed = False

    def _get_member_type(self, lstat_result: os.stat_result) -> MemberType:
        """Determines the MemberType from a path and its lstat result."""
        if stat.S_ISDIR(lstat_result.st_mode):
            return MemberType.DIR
        if stat.S_ISLNK(lstat_result.st_mode):
            return MemberType.SYMLINK
        if stat.S_ISREG(lstat_result.st_mode):
            return MemberType.FILE
        return MemberType.OTHER

    def _convert_entry_to_member(
        self, entry_path: Path, seen_inodes: Optional[dict[int, str]] = None
    ) -> ArchiveMember:
        """Converts a filesystem path to an ArchiveMember."""
        filename = str(entry_path.relative_to(self.path)).replace(os.sep, "/")

        try:
            # Use lstat to get info about the link itself, not the target
            stat_result = entry_path.lstat()

        except OSError as e:
            # Could be a broken symlink or permission error
            # Create a placeholder member
            return ArchiveMember(
                filename=filename,
                raw_filename=filename,
                file_size=0,
                compress_size=0,
                mtime_with_tz=None,
                type=MemberType.OTHER,  # Or be more specific if possible from error
                comment=f"Error reading entry: {e}",
                raw_info=e,
            )

        member_type = self._get_member_type(stat_result)

        ownership = get_ownership_from_stat(stat_result)

        # Check for hardlinks if this is a regular file and we're tracking inodes
        if member_type == MemberType.FILE and seen_inodes is not None:
            inode = stat_result.st_ino
            if inode in seen_inodes:
                # This is a hardlink to a previously seen file
                member_type = MemberType.HARDLINK
                link_target = seen_inodes[inode]
            else:
                # First time seeing this inode, record it
                seen_inodes[inode] = filename
                link_target = None
        else:
            link_target = None

        if member_type == MemberType.DIR and not filename.endswith("/"):
            filename += "/"

        # Handle symlink targets
        if member_type == MemberType.SYMLINK:
            try:
                link_target = os.readlink(entry_path)
            except OSError:
                link_target = "Error reading link target"

        logger.info("filename: %s member_type: %s", filename, member_type)
        return ArchiveMember(
            filename=filename,
            raw_filename=filename,
            file_size=stat_result.st_size,
            compress_size=stat_result.st_size,  # No compression for folders
            # st_mtime is in seconds since the epoch, so UTC.
            mtime_with_tz=datetime.fromtimestamp(stat_result.st_mtime, tz=timezone.utc),
            type=member_type,
            mode=stat_result.st_mode & 0o7777,
            uid=ownership.uid,
            gid=ownership.gid,
            uname=ownership.uname,
            gname=ownership.gname,
            link_target=link_target,
        )

    def iter_members_for_registration(self) -> Iterator[ArchiveMember]:
        # Track inode numbers to detect hardlinks
        seen_inodes: dict[int, str] = {}

        for root, dirnames, filenames in os.walk(
            self.path, topdown=True, followlinks=False
        ):
            dirpath = Path(root)
            dirnames.sort()
            filenames.sort()
            for dirname in dirnames:
                yield self._convert_entry_to_member(dirpath / dirname, seen_inodes)
            for filename in filenames:
                yield self._convert_entry_to_member(dirpath / filename, seen_inodes)

    def _translate_exception(self, e: Exception) -> Optional[ArchiveError]:
        if isinstance(e, FileNotFoundError):
            return ArchiveMemberNotFoundError(f"Member not found: {e}")
        return None

    def _open_member(
        self,
        member: ArchiveMember,
        pwd: str | bytes | None,
        for_iteration: bool,
    ) -> BinaryIO:
        assert member.type == MemberType.FILE

        # Convert archive path (with '/') to OS-specific path
        os_specific_member_path = member.filename.replace("/", os.sep)
        full_path = self.path / os_specific_member_path

        logger.info("full_path: %s", full_path)

        if not full_path.exists():
            raise ArchiveMemberNotFoundError(
                f"Member not found: {member.filename} (resolved to {full_path})"
            )

        # It's good practice to ensure the resolved path is still within the archive root
        # to prevent potential directory traversal issues if member_name contains '..'
        # TODO: this check may not actually be needed, as this method
        # only opens files, not symlinks. Double-check this.
        try:
            resolved_full_path = full_path.resolve()
            archive_root = self.path

            logger.info("resolved_full_path: %s", resolved_full_path)
            logger.info("archive_root: %s", archive_root)

            # Verify the resolved path stays within the archive root. Using
            # pathlib's ``is_relative_to`` avoids issues with string prefix
            # comparisons and correctly handles symlinks and ``..`` segments.
            if not resolved_full_path.is_relative_to(archive_root):
                raise ArchiveMemberNotFoundError(
                    f"Access to member '{member.filename}' outside archive root is denied."
                )

        except OSError as e:  # e.g. broken symlink during resolve()
            raise ArchiveMemberNotFoundError(
                f"Error resolving path for member '{member.filename}': {e}"
            ) from e

        return full_path.open("rb")
        # try:
        #     return ArchiveStream(
        #         open_fn=lambda: full_path.open("rb"),
        #         underlying_library_name="pathlib",
        #         archive_path=self.path_str,
        #         member_name=member.filename,
        #     )
        # except OSError as e:
        #     raise ArchiveReadError(
        #         f"Cannot open member '{member.filename}': {e}"
        #     ) from e

    def get_archive_info(self) -> ArchiveInfo:
        self.check_archive_open()

        return ArchiveInfo(
            format=self.format,
        )

    def _close_archive(self) -> None:
        """Close the archive and release any resources."""
        # No-op for FolderReader, as there's no main file handle to close.
        # Individual files are opened and closed in the open() method.
        pass
