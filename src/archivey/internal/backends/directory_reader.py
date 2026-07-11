"""Directory pseudo-backend: presents a filesystem directory as an ArchiveReader."""

from __future__ import annotations

import os
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Iterator, Mapping

from archivey.config import ArchiveyConfig
from archivey.cost import (
    AccessCost,
    CostReceipt,
    ListingCost,
    StreamCapability,
)
from archivey.diagnostics import DiagnosticCode, ScanRaceContext
from archivey.internal.base_reader import BaseArchiveReader, ReadBackend
from archivey.internal.diagnostics_collector import DiagnosticCollector
from archivey.internal.logs import backends as logger
from archivey.internal.open_site import OpenSite
from archivey.internal.password import _PasswordCandidates
from archivey.internal.registry import register_reader
from archivey.internal.streams.archive_stream import ArchiveStream
from archivey.types import (
    EXTRA_IS_JUNCTION,
    ArchiveFormat,
    ArchiveInfo,
    ArchiveMember,
    MagicSignature,
    MemberStreams,
    MemberType,
)


def _is_junction(entry: os.DirEntry[str]) -> bool:
    """True if a scandir entry is a Windows NTFS junction.

    ``os.DirEntry.is_junction()`` only exists on Python 3.12+; on older interpreters
    (and on every non-Windows platform, where junctions don't exist) this returns False.
    """
    is_junction = getattr(entry, "is_junction", None)
    return bool(is_junction()) if is_junction is not None else False


class DirectoryReader(BaseArchiveReader):
    """Reads a filesystem directory as an archive."""

    _SUPPORTS_RANDOM_ACCESS = True
    _MEMBER_LIST_UPFRONT = True

    def __init__(
        self,
        root: Path,
        streaming: bool,
        archive_name: str | None,
        config: ArchiveyConfig,
        collector: DiagnosticCollector | None = None,
        member_streams: MemberStreams = MemberStreams(0),
        open_site: OpenSite | None = None,
    ) -> None:
        super().__init__(
            ArchiveFormat.DIRECTORY,
            streaming,
            archive_name,
            config,
            collector=collector,
            member_streams=member_streams,
            open_site=open_site,
        )
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
        #
        # Errors: a live filesystem can change under the walk, so an entry (or a whole
        # subdirectory) that vanished between being listed and being inspected is
        # skipped with a warning — a race, not an error. Every other OSError (permission
        # denied, I/O failure) propagates unchanged: silently dropping entries would
        # present an incomplete listing as complete (see the design authority in
        # `openspec/project.md` — no silent guesses — and `error-handling`'s rule that
        # genuine I/O errors are never swallowed or reclassified).
        try:
            with os.scandir(directory) as it:
                entries = sorted(it, key=lambda e: e.name)
        except FileNotFoundError:
            relative = rel_prefix.rstrip("/") or "."
            self._diagnostics_collector.emit(
                code=DiagnosticCode.SCAN_DIRECTORY_VANISHED,
                message=f"Directory vanished during scan, skipping: {str(directory)!r}",
                context=ScanRaceContext(
                    archive_name=self._archive_name,
                    relative_path=relative,
                    entry_kind="directory",
                ),
                logger=logger,
            )
            return

        # Emit all non-directory entries at this level first, then descend into the
        # subdirectories, so the iterator yields a directory's own files before walking
        # into its children. `subdirs` keeps the (member, path) pairs to recurse into.
        subdirs: list[tuple[ArchiveMember, Path]] = []
        for entry in entries:
            rel_path = rel_prefix + entry.name
            try:
                st = entry.stat(follow_symlinks=False)
            except FileNotFoundError:
                self._diagnostics_collector.emit(
                    code=DiagnosticCode.SCAN_ENTRY_VANISHED,
                    message=f"Entry vanished during scan, skipping: {entry.path!r}",
                    context=ScanRaceContext(
                        archive_name=self._archive_name,
                        relative_path=rel_path,
                        entry_kind="entry",
                    ),
                    logger=logger,
                )
                continue

            if entry.is_symlink():
                yield self._make_member(
                    rel_path, st, MemberType.SYMLINK, self._read_link_target(entry.path)
                )
            elif _is_junction(entry):
                # A Windows NTFS junction points at a directory but is a reparse point,
                # not a real subtree to walk — surface it as a symlink-like leaf (flagged
                # via extra[EXTRA_IS_JUNCTION]) and do NOT recurse through it.
                yield self._make_member(
                    rel_path,
                    st,
                    MemberType.SYMLINK,
                    self._read_link_target(entry.path),
                    is_junction=True,
                )
            elif entry.is_dir(follow_symlinks=False):
                member = self._make_member(
                    rel_path + "/", st, MemberType.DIRECTORY, None
                )
                subdirs.append((member, Path(entry.path)))
            elif entry.is_file(follow_symlinks=False):
                yield self._make_member(rel_path, st, MemberType.FILE, None)
            else:
                yield self._make_member(rel_path, st, MemberType.OTHER, None)

        # Now descend, keeping a stable parent-before-children order within each subtree.
        for member, path in subdirs:
            yield member
            yield from self._scan(path, member.name)

    @staticmethod
    def _read_link_target(path: str) -> str:
        """The symlink/junction target, with separators normalized like member names.

        On Windows ``os.readlink`` returns the target with ``\\`` separators; convert
        them to ``/`` so link targets live in the same namespace as member names (where
        the separator conversion is likewise applied only for Windows-origin paths — on
        POSIX a backslash is a literal filename character and is kept).
        """
        target = os.readlink(path)
        if os.name == "nt":
            target = target.replace("\\", "/")
        return target

    def _make_member(
        self,
        name: str,
        st: os.stat_result,
        member_type: MemberType,
        link_target: str | None,
        is_junction: bool = False,
    ) -> ArchiveMember:
        # `name` is built from live filesystem entries (already "/"-separated, no
        # "."/".."/leading-slash components), so it is normalize_member_name()-clean by
        # construction — unlike names decoded from an archive, which every real backend
        # must route through that helper.
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
            extra={EXTRA_IS_JUNCTION: True} if is_junction else {},
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

    def _open_member(self, member: ArchiveMember) -> ArchiveStream:
        full_path = self._root / member.name
        # Wrapped like every backend's member stream (the uniform-handle contract): the
        # directory backend has no translator (a genuine OSError propagates unchanged),
        # but the caller still gets the same ArchiveStream handle type — with its `size`
        # advertisement — as for any other format.
        raw = open(full_path, "rb")  # noqa: SIM115
        return self._wrap_member_stream(raw, member.name, size=member.size)

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
    EXTENSIONS: Mapping[str, ArchiveFormat] = {}
    MAGIC: tuple[MagicSignature, ...] = ()

    def open_read(
        self,
        source: Path | BinaryIO,
        format: ArchiveFormat,
        streaming: bool,
        passwords: _PasswordCandidates | None,
        encoding: str | None,
        archive_name: str | None,
        config: ArchiveyConfig,
        collector: DiagnosticCollector | None = None,
        member_streams: MemberStreams = MemberStreams(0),
        open_site: OpenSite | None = None,
    ) -> DirectoryReader:
        # `format` is always DIRECTORY here (single-format backend); accepted for the
        # uniform ReadBackend signature. Password rejection is central (SUPPORTS_PASSWORD).
        if not isinstance(source, Path):
            raise TypeError("Directory backend requires a Path source")
        return DirectoryReader(
            source,
            streaming,
            archive_name or str(source),
            config,
            collector=collector,
            member_streams=member_streams,
            open_site=open_site,
        )


# Self-register at import time
register_reader(DirectoryReadBackend)
