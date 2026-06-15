from __future__ import annotations

import collections
import logging
import os
import shutil
import threading
from typing import TYPE_CHECKING, Any

from archivey.config import OverwriteMode
from archivey.exceptions import (
    ArchiveFileExistsError,
)
from archivey.internal.utils import set_file_mtime, set_file_permissions
from archivey.types import ArchiveMember, MemberType

if TYPE_CHECKING:
    from archivey.archive_reader import ArchiveReader
    from archivey.internal.io_helpers import ReadableBinaryStream

logger = logging.getLogger(__name__)


def apply_member_metadata(member: ArchiveMember, target_path: str) -> None:
    # Set atime + mtime via os.utime when atime is available; otherwise fall back
    # to the existing set_file_mtime helper (which uses member.mtime — a naive
    # datetime — to preserve the same round-trip behaviour as the old code).
    if member.atime is not None and member.mtime is not None:
        # atime: strip timezone and use as naive local time, consistent with mtime.
        atime_naive = member.atime.replace(tzinfo=None)
        kwargs: dict[str, Any] = {}
        if member.type == MemberType.SYMLINK:
            if os.utime not in os.supports_follow_symlinks:
                atime_naive = None  # type: ignore[assignment]
            else:
                kwargs["follow_symlinks"] = False
        if atime_naive is not None and member.type != MemberType.HARDLINK:
            try:
                os.utime(
                    target_path,
                    (atime_naive.timestamp(), member.mtime.timestamp()),
                    **kwargs,
                )
            except OSError:
                pass
    elif member.mtime:
        set_file_mtime(target_path, member.mtime, member.type)

    if member.mode:
        set_file_permissions(target_path, member.mode, member.type)

    # Apply Windows read-only attribute by stripping write bits
    if member.windows_attrs is not None and (member.windows_attrs & 0x0001):
        try:
            current_stat = os.stat(target_path)
            new_mode = current_stat.st_mode & ~0o222
            os.chmod(target_path, new_mode)
        except OSError:
            pass


class ExtractionHelper:
    def __init__(
        self,
        archive_reader: "ArchiveReader",
        root_path: str,
        overwrite_mode: OverwriteMode,
        can_process_pending_extractions: bool = True,
    ):
        assert isinstance(overwrite_mode, OverwriteMode)
        self.archive_reader = archive_reader
        self.root_path = root_path
        self.overwrite_mode = overwrite_mode
        self.can_process_pending_extractions = can_process_pending_extractions

        self._lock = threading.Lock()

        self.extracted_members_by_path: dict[str, ArchiveMember] = {}
        self.extracted_path_by_source_id: dict[int, str] = {}

        self.failed_extractions: list[ArchiveMember] = []

        self.pending_files_to_extract_by_id: dict[int, ArchiveMember] = {}
        self.pending_target_members_by_source_id: dict[int, list[ArchiveMember]] = (
            collections.defaultdict(list)
        )

    def get_output_path(self, member: ArchiveMember) -> str:
        return os.path.normpath(os.path.join(self.root_path, member.filename))

    def check_overwrites(self, member: ArchiveMember, path: str) -> bool:
        # TODO: should we handle the case where some entry in the path to the file
        # is actually a symlink pointing outside the root path? Is that a possible
        # security issue?

        if not os.path.lexists(path):
            # File doesn't exist, nothing to do
            return True

        existing_file_is_dir = os.path.isdir(path)
        if member.type == MemberType.DIR and existing_file_is_dir:
            # No problem, we're overwriting a directory with a directory
            return True

        if path in self.extracted_members_by_path:
            # The file was created during this extraction, so we can overwrite it regardless
            # of the overwrite mode.
            # But we only want to keep the last version of the file, so don't let an
            # earlier version overwrite a later one.
            if self.extracted_members_by_path[path].member_id > member.member_id:
                logger.info(
                    "Skipping %s %s as it's a later version of the same file",
                    member.type.value,
                    path,
                )
                return False

            logger.info(
                "Overwriting existing %s %s as it was created during this extraction",
                member.type.value,
                path,
            )

        elif self.overwrite_mode == OverwriteMode.SKIP:
            logger.info(
                "Skipping existing %s %s",
                member.type.value,
                path,
            )
            self.failed_extractions.append(member)
            return False

        elif self.overwrite_mode == OverwriteMode.ERROR:
            self.failed_extractions.append(member)
            raise ArchiveFileExistsError(f"{member.type.value} {path} already exists")

        if member.type == MemberType.DIR:
            # This is only reached if the member is a directory and the existing file is not
            self.failed_extractions.append(member)
            raise ArchiveFileExistsError(
                f"Cannot create dir {path} as it already exists as a file"
            )

        if existing_file_is_dir:
            self.failed_extractions.append(member)
            raise ArchiveFileExistsError(
                f"Cannot create {member.type.value} {path} as it already exists as a dir"
            )

        logger.info("Removing existing file %s", path)
        os.remove(path)

        return True

    def create_directory(self, member: ArchiveMember, path: str) -> bool:
        if not self.check_overwrites(member, path):
            return False

        os.makedirs(path, exist_ok=True)
        self.extracted_members_by_path[path] = member
        return True

    def process_file_extracted(
        self, member: ArchiveMember, extracted_path: str | None
    ) -> None:
        """Called for files that had a delayed extraction."""
        logger.debug(
            "Processing external extraction of %s [%s] to %s",
            member.filename,
            member.member_id,
            extracted_path,
        )
        if member.is_link:
            self.extract_member(member, None)

        if extracted_path is None:
            logger.error(
                "No extracted path for %s [%s]",
                member.filename,
                member.member_id,
            )
            self.failed_extractions.append(member)
            return

        targets = self.pending_target_members_by_source_id.pop(member.member_id, None)
        if not targets:
            # We were not expecting this file to be extracted. TODO: should we delete it?
            logger.error(
                "Unexpected file %s was extracted by an external library",
                member.filename,
            )
            return

        self.pending_files_to_extract_by_id.pop(member.member_id, None)

        self.can_move_file = True
        written_target_paths: set[str] = set()
        for target in targets:
            logger.info(
                "  Processing target %s [%s] (member [%s])",
                target.filename,
                target.member_id,
                member.member_id,
            )
            # TODO: handle exceptions

            target_path = self.get_output_path(target)

            if self.can_move_file:
                # The first target is either the original member or, if it was not
                # extracted, the first hardlink that pointed to it, but which should become a regular file.
                # In both cases, move the file if it is not in the expected location
                # (which can happen even for the original member, if the library renamed it
                # if there were several files with the same name -- py7zr does this,
                # or if the filter function renamed it).

                if os.path.realpath(target_path) == os.path.realpath(extracted_path):
                    logger.info(
                        "  File %s is already in the expected location",
                        target.filename,
                    )
                    with self._lock:
                        self.can_move_file = False
                        self.extracted_members_by_path[target_path] = target
                        written_target_paths.add(target_path)
                else:
                    with self._lock:
                        logger.info(
                            "  Moving file from %s to %s",
                            extracted_path,
                            target_path,
                        )
                        if not self.check_overwrites(member, target_path):
                            continue

                        os.makedirs(os.path.dirname(target_path), exist_ok=True)
                        shutil.move(extracted_path, target_path)
                        self.extracted_members_by_path[target_path] = target
                        written_target_paths.add(target_path)

            else:
                # Create a hardlink to the first target.
                logger.info(
                    "  Creating hardlink for %s [%s] (member [%s])",
                    target.filename,
                    target.member_id,
                    member.member_id,
                )
                try:
                    with self._lock:
                        # Some tar archives can contain hardlinks to a file with the same name.
                        # If we check for overwrites here, it can end up deleting the original
                        # extracted file, and we'll have nothing to link to.
                        if target_path in written_target_paths:
                            logger.info(
                                "  Skipping hardlink for %s [%s] (member [%s]) as it is the same file",
                                target.filename,
                                target.member_id,
                                member.member_id,
                            )
                            # This was technically extracted last.
                            self.extracted_members_by_path[target_path] = target
                            continue

                        if not self.check_overwrites(member, target_path):
                            continue

                        os.makedirs(os.path.dirname(target_path), exist_ok=True)
                        os.link(target_path, self.get_output_path(target))
                        self.extracted_members_by_path[target_path] = target
                        written_target_paths.add(target_path)

                except (AttributeError, NotImplementedError, OSError):
                    # os.link failed, so we need to create a copy as a regular file.
                    # The list of exceptions was taken from tarfile.py.
                    logger.info(
                        "Creating hardlink for %s failed, copying the file instead",
                        target.filename,
                    )
                    shutil.copyfile(extracted_path, target_path)
                    if target.mtime:
                        set_file_mtime(target_path, target.mtime, MemberType.FILE)
                    if target.mode:
                        set_file_permissions(target_path, target.mode, MemberType.FILE)
                    self.extracted_members_by_path[target_path] = target

            # Remove the file from the pending list.
            self.extracted_path_by_source_id[target.member_id] = target_path

    def create_regular_file(
        self, member: ArchiveMember, stream: ReadableBinaryStream | None, path: str
    ) -> bool:
        if not self.check_overwrites(member, path):
            return False

        if stream is None:
            # This is a delayed extraction, so we need to store the member and the path
            # for later.
            self.pending_files_to_extract_by_id[member.member_id] = member
            self.pending_target_members_by_source_id[member.member_id].append(member)
            return True

        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as dst:
            shutil.copyfileobj(stream, dst)
        self.extracted_members_by_path[path] = member
        self.extracted_path_by_source_id[member.member_id] = path

        if member.member_id in self.pending_target_members_by_source_id:
            self.process_file_extracted(member, path)

        return True

    def create_link(self, member: ArchiveMember, member_path: str) -> bool:
        logger.info(
            "Creating link %s to %s , path=%s",
            member.filename,
            member.link_target,
            member_path,
        )
        if member.link_target is None:
            # The link target may not have been read yet (possible for 7z archives)
            if self.can_process_pending_extractions:
                logger.info(
                    "Link target not set for %s, storing for later extraction",
                    member.filename,
                )
                self.pending_files_to_extract_by_id[member.member_id] = member

                return True
            logger.error("Link target not set for %s", member.filename)
            self.failed_extractions.append(member)
            return False

        if member.type == MemberType.HARDLINK:
            # Hard links can only point to files in the same archive.
            # If that file was already extracted, take the target path from the extracted path
            target_member = self.archive_reader.resolve_link(member)
            if target_member is None:
                logger.error(
                    "Hardlink target %s not found for %s",
                    member.link_target,
                    member.filename,
                )
                self.failed_extractions.append(member)
                return False

            target_path = self.extracted_path_by_source_id.get(target_member.member_id)
            if target_path is None:
                # The target file was not extracted, so we need to store it for later
                # extraction if possible.
                if self.can_process_pending_extractions:
                    logger.info(
                        "Storing hardlink %s for later extraction as its target %s was not extracted",
                        member.filename,
                        target_member.filename,
                    )
                    self.pending_files_to_extract_by_id[target_member.member_id] = (
                        target_member
                    )
                    self.pending_target_members_by_source_id[
                        target_member.member_id
                    ].append(member)
                    return True
                logger.error(
                    "Hardlink target %s was not extracted for %s",
                    member.link_target,
                    member.filename,
                )
                self.failed_extractions.append(member)
                return False

        elif member.type == MemberType.SYMLINK:
            symlink_dir = os.path.dirname(os.path.join(self.root_path, member.filename))
            target_path = os.path.normpath(
                os.path.join(symlink_dir, member.link_target)
            )

        else:
            raise ValueError(f"Unexpected member type: {member.type}")

        if os.path.realpath(member_path) == os.path.realpath(target_path):
            # .tar files can contain links to themselves, which is not a problem,
            # but we can't remove the previous file in this case as there would be
            # nowhere to point to.
            logger.info("Skipping %s to self: %s", member.type.value, member.filename)
            return True

        if not self.check_overwrites(member, member_path):
            return False

        os.makedirs(os.path.dirname(member_path), exist_ok=True)
        if member.type == MemberType.HARDLINK:
            os.link(target_path, member_path)
        else:
            target_member = self.archive_reader.resolve_link(member)
            os.symlink(
                member.link_target,
                member_path,
                target_is_directory=target_member is not None
                and target_member.type == MemberType.DIR,
            )
        self.extracted_members_by_path[member_path] = member
        return True

    def extract_member(
        self, member: ArchiveMember, stream: ReadableBinaryStream | None
    ) -> bool:
        path = self.get_output_path(member)
        logger.info(
            "Extracting %s [%s] to %s, stream: %s",
            member.filename,
            member.member_id,
            path,
            stream is not None,
        )

        if member.is_dir:
            return self.create_directory(member, path)

        if member.is_file:
            return self.create_regular_file(member, stream, path)

        if member.is_link:
            return self.create_link(member, path)

        self.failed_extractions.append(member)
        logger.error("Unexpected member type: %s", member.type)
        return False

    # def process_external_extraction(self, member: ArchiveMember, rel_path: str) -> None:
    #     """Called for files that were extracted by an external library."""
    #     full_path = os.path.realpath(os.path.join(self.root_path, rel_path))
    #     self.process_file_extracted(member, full_path)

    def get_pending_extractions(self) -> list[ArchiveMember]:
        logger.info(
            "Getting pending extractions: %s",
            ", ".join(
                f"{k}: {v.filename} ({v.type.value})"
                for k, v in self.pending_files_to_extract_by_id.items()
            ),
        )
        return list(self.pending_files_to_extract_by_id.values())

    def get_failed_extractions(self) -> list[ArchiveMember]:
        return self.failed_extractions

    def apply_metadata(self) -> None:
        for path, member in self.extracted_members_by_path.items():
            apply_member_metadata(member, path)
