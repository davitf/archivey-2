"""Directory pseudo-backend: presents a filesystem directory as an ArchiveReader."""

from __future__ import annotations

import os
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Iterator

from archivey.internal.intent import (
    AccessCost,
    CostReceipt,
    Intent,
    ListingCost,
    StreamCapability,
)
from archivey.internal.reader import BaseArchiveReader, ReadBackend
from archivey.internal.registry import register_reader
from archivey.internal.types import (
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
        super().__init__(ArchiveFormat.DIRECTORY, intent, archive_name)
        self._root = root
        # uid/gid -> name caches: most entries in a tree share an owner/group, and
        # pwd/grp lookups hit the system database (nss) on every call, so we memoize.
        self._uname_cache: dict[int, str | None] = {}
        self._gname_cache: dict[int, str | None] = {}

    def _iter_members(self) -> Iterator[ArchiveMember]:
        yield from self._scan(self._root, "")

    def _scan(self, directory: Path, rel_prefix: str) -> Iterator[ArchiveMember]:
        # os.scandir yields DirEntry objects whose stat() is cached, so we avoid a
        # separate os.stat()/os.lstat() syscall per entry.
        try:
            with os.scandir(directory) as it:
                entries = sorted(it, key=lambda e: e.name)
        except OSError:
            return

        for entry in entries:
            rel_path = rel_prefix + entry.name
            try:
                st = entry.stat(follow_symlinks=False)
            except OSError:
                continue

            if entry.is_symlink():
                yield self._make_member(
                    rel_path, st, MemberType.SYMLINK, os.readlink(entry.path)
                )
            elif entry.is_dir(follow_symlinks=False):
                yield self._make_member(rel_path + "/", st, MemberType.DIRECTORY, None)
                # Recurse, keeping members in a stable parent-before-children order.
                yield from self._scan(Path(entry.path), rel_path + "/")
            elif entry.is_file(follow_symlinks=False):
                yield self._make_member(rel_path, st, MemberType.FILE, None)
            else:
                yield self._make_member(rel_path, st, MemberType.OTHER, None)

    def _make_member(
        self,
        name: str,
        st: os.stat_result,
        member_type: MemberType,
        link_target: str | None,
    ) -> ArchiveMember:
        # `name` is already built with "/" separators from the scan, so no path
        # rewriting is needed here.
        modified = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)
        accessed = datetime.fromtimestamp(st.st_atime, tz=timezone.utc)
        # st_birthtime is the true creation time but only exists on some platforms
        # (macOS/BSD, Windows, recent Linux); st_ctime is metadata-change time on
        # Unix, NOT creation, so we never use it for `created`. Hence the getattr.
        birthtime = getattr(st, "st_birthtime", None)
        created = (
            datetime.fromtimestamp(birthtime, tz=timezone.utc)
            if birthtime is not None
            else None
        )

        # os.stat_result always defines st_uid/st_gid (both 0 on Windows), so no
        # getattr guard is needed.
        uid = st.st_uid
        gid = st.st_gid

        size = st.st_size if member_type == MemberType.FILE else None

        return ArchiveMember(
            type=member_type,
            name=name,
            raw_name=name.encode("utf-8", errors="surrogateescape"),
            size=size,
            compressed_size=size,  # no compression
            modified=modified,
            accessed=accessed,
            created=created,
            mode=stat.S_IMODE(st.st_mode),
            uid=uid,
            gid=gid,
            uname=self._lookup_uname(uid),
            gname=self._lookup_gname(gid),
            link_target=link_target,
        )

    def _lookup_uname(self, uid: int) -> str | None:
        if uid not in self._uname_cache:
            try:
                import pwd

                self._uname_cache[uid] = pwd.getpwuid(uid).pw_name
            except (ImportError, KeyError):
                self._uname_cache[uid] = None
        return self._uname_cache[uid]

    def _lookup_gname(self, gid: int) -> str | None:
        if gid not in self._gname_cache:
            try:
                import grp

                self._gname_cache[gid] = grp.getgrgid(gid).gr_name
            except (ImportError, KeyError):
                self._gname_cache[gid] = None
        return self._gname_cache[gid]

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
            format=ArchiveFormat.DIRECTORY,
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

    FORMATS: tuple[ArchiveFormat, ...] = (ArchiveFormat.DIRECTORY,)
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
