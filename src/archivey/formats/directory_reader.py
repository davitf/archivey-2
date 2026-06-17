"""Directory pseudo-backend: presents a filesystem directory as an ArchiveReader."""

from __future__ import annotations

import os
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Iterator

from archivey.internal._intent import (
    AccessCost,
    CostReceipt,
    Intent,
    ListingCost,
    StreamCapability,
)
from archivey.internal._reader import BaseArchiveReader, ReadBackend
from archivey.internal._registry import register_reader
from archivey.internal._types import (
    ArchiveFormat,
    ArchiveInfo,
    ArchiveMember,
    MemberType,
)


class DirectoryReader(BaseArchiveReader):
    """Reads a filesystem directory as an archive."""

    _SUPPORTS_RANDOM_ACCESS = True
    _MEMBER_LIST_UPFRONT = True

    def __init__(
        self,
        root: Path,
        intent: Intent,
        archive_name: str | None,
    ) -> None:
        super().__init__(ArchiveFormat.DIRECTORY, intent, archive_name)  # type: ignore[attr-defined]
        self._root = root

    def _iter_members(self) -> Iterator[ArchiveMember]:
        for dirpath, dirnames, filenames in os.walk(self._root, followlinks=False):
            rel_dirpath = os.path.relpath(dirpath, self._root)
            if rel_dirpath == ".":
                rel_dirpath = ""

            # Sort for deterministic order
            dirnames.sort()
            filenames_sorted = sorted(filenames)

            # Yield directory entry (skip root itself)
            if rel_dirpath:
                dir_stat = os.stat(dirpath)
                yield self._stat_to_member(
                    rel_dirpath + "/",
                    dirpath,
                    dir_stat,
                    MemberType.DIRECTORY,
                    None,
                )

            for name in filenames_sorted:
                full_path = os.path.join(dirpath, name)
                rel_path = os.path.join(rel_dirpath, name) if rel_dirpath else name
                try:
                    entry_stat = os.lstat(full_path)
                except OSError:
                    continue

                if stat.S_ISLNK(entry_stat.st_mode):
                    link_target = os.readlink(full_path)
                    yield self._stat_to_member(
                        rel_path,
                        full_path,
                        entry_stat,
                        MemberType.SYMLINK,
                        link_target,
                    )
                elif stat.S_ISREG(entry_stat.st_mode):
                    yield self._stat_to_member(
                        rel_path,
                        full_path,
                        entry_stat,
                        MemberType.FILE,
                        None,
                    )
                else:
                    yield self._stat_to_member(
                        rel_path,
                        full_path,
                        entry_stat,
                        MemberType.OTHER,
                        None,
                    )

    def _stat_to_member(
        self,
        rel_path: str,
        full_path: str,
        st: os.stat_result,
        member_type: MemberType,
        link_target: str | None,
    ) -> ArchiveMember:
        # Use forward slashes
        name = rel_path.replace(os.sep, "/")

        # Timestamps: use mtime as modified
        modified = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)

        uid: int | None = getattr(st, "st_uid", None)
        gid: int | None = getattr(st, "st_gid", None)

        # Try to get uname/gname on Unix
        uname: str | None = None
        gname: str | None = None
        try:
            import grp
            import pwd

            if uid is not None:
                uname = pwd.getpwuid(uid).pw_name
            if gid is not None:
                gname = grp.getgrgid(gid).gr_name
        except (ImportError, KeyError):
            pass

        size = st.st_size if member_type == MemberType.FILE else None

        return ArchiveMember(
            type=member_type,
            name=name,
            raw_name=name.encode("utf-8", errors="surrogateescape"),
            size=size,
            compressed_size=size,  # no compression
            modified=modified,
            mode=stat.S_IMODE(st.st_mode),
            uid=uid,
            gid=gid,
            uname=uname,
            gname=gname,
            link_target=link_target,
        )

    def _open_member(self, member: ArchiveMember) -> BinaryIO:
        full_path = self._root / member.name
        return open(full_path, "rb")  # noqa: SIM115

    def _get_archive_info(self) -> ArchiveInfo:
        cost = CostReceipt(
            listing_cost=ListingCost.INDEXED,
            access_cost=AccessCost.DIRECT,
            stream_capability=StreamCapability.SEEKABLE,
            solid_block_count=None,
        )
        return ArchiveInfo(
            format=ArchiveFormat.DIRECTORY,  # type: ignore[attr-defined]
            format_version=None,
            is_solid=False,
            member_count=None,  # unknown until walked
            comment=None,
            is_encrypted=False,
            is_multivolume=False,
            cost=cost,
        )

    def _close_archive(self) -> None:
        pass  # nothing to close for filesystem directory


class DirectoryReadBackend(ReadBackend):
    """Backend factory for directory pseudo-archives."""

    FORMATS: tuple[ArchiveFormat, ...] = (ArchiveFormat.DIRECTORY,)  # type: ignore[attr-defined]
    EXTENSIONS: tuple[str, ...] = ()
    MAGIC: tuple[tuple[int, bytes], ...] = ()
    REQUIRES_SEEK = False

    def open_read(
        self,
        source: Path | BinaryIO,
        intent: Intent,
        password: bytes | None,
        encoding: str | None,
    ) -> DirectoryReader:
        if not isinstance(source, Path):
            raise TypeError("Directory backend requires a Path source")
        archive_name = str(source)
        return DirectoryReader(source, intent, archive_name)


# Self-register at import time
register_reader(DirectoryReadBackend)
