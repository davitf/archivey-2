"""Native RAR reader backend (metadata via rar_parser; data via RARLAB unrar)."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import BinaryIO

from archivey.config import ArchiveyConfig
from archivey.cost import AccessCost, CostReceipt, ListingCost, StreamCapability
from archivey.exceptions import (
    EncryptionError,
    StreamNotSeekableError,
    TruncatedError,
)
from archivey.internal.backends.rar_parser import (
    RAR5_ID,
    RAR_ID,
    RarArchive,
    RarMemberInfo,
    parse_rar_archive,
    parse_rar_volumes,
)
from archivey.internal.backends.rar_unrar import (
    open_unrar_p,
    terminate_unrar,
)
from archivey.internal.base_reader import BaseArchiveReader, ReadBackend
from archivey.internal.diagnostics_collector import DiagnosticCollector
from archivey.internal.naming import emit_member_name_normalized, normalize_member_name
from archivey.internal.open_site import OpenSite
from archivey.internal.password import (
    _PasswordCandidates,
    _PasswordCandidatesExhausted,
)
from archivey.internal.registry import register_reader
from archivey.internal.streams.archive_stream import ArchiveStream
from archivey.internal.streams.streamtools import (
    DelegatingStream,
    SharedSource,
    SlicingStream,
    SolidBlockReader,
    is_seekable,
    is_stream,
)
from archivey.internal.streams.verify import VerifyingStream
from archivey.internal.volumes import ConcatenatedFile, discover_volume_siblings
from archivey.types import (
    EXTRA_IS_JUNCTION,
    ArchiveFormat,
    ArchiveInfo,
    ArchiveMember,
    CompressionAlgorithm,
    CompressionMethod,
    CreateSystem,
    MagicSignature,
    MemberStreams,
    MemberType,
)

# rarfile / RAR host_os values (parser maps RAR5 Windows→2, Unix→3).
_RAR_HOST_OS_TO_CREATE_SYSTEM: dict[int, CreateSystem] = {
    0: CreateSystem.FAT,
    1: CreateSystem.OS2_HPFS,
    2: CreateSystem.WINDOWS_NTFS,
    3: CreateSystem.UNIX,
    4: CreateSystem.MACINTOSH,
    5: CreateSystem.BEOS,
}

_RAR_METHOD_STORED = 0x30
_RAR_ENCDATA_FLAG_TWEAKED_CHECKSUMS = 0x02
_RAR5_XREDIR_WINDOWS_JUNCTION = 3


def _member_stream_size(member: ArchiveMember) -> int:
    return member.size if member.size is not None else 0


def _password_as_str(password: bytes | str | None) -> str | None:
    if password is None or password == b"" or password == "":
        return None
    if isinstance(password, bytes):
        return password.decode("utf-8", errors="surrogateescape")
    return password


def _compression_for(info: RarMemberInfo) -> tuple[CompressionMethod, ...]:
    method = info.compress_type
    if method is None:
        return ()
    if method == _RAR_METHOD_STORED:
        return (CompressionMethod(algo=CompressionAlgorithm.STORED),)
    # RAR M1–M5 are proprietary; expose as UNKNOWN with the method byte as level.
    level = method - _RAR_METHOD_STORED if method >= _RAR_METHOD_STORED else None
    return (CompressionMethod(algo=CompressionAlgorithm.UNKNOWN, level=level),)


def _crc_is_tweaked(info: RarMemberInfo) -> bool:
    enc = info.file_encryption
    if enc is None:
        return False
    return bool(enc.flags & _RAR_ENCDATA_FLAG_TWEAKED_CHECKSUMS)


def _member_hashes(info: RarMemberInfo) -> dict[str, int | bytes]:
    hashes: dict[str, int | bytes] = {}
    if info.crc32 is not None and not _crc_is_tweaked(info):
        hashes["crc32"] = info.crc32
    if info.blake2sp_hash is not None:
        hashes["blake2sp"] = info.blake2sp_hash
    return hashes


def _copy_stream_to_path(source: BinaryIO, dest: Path) -> None:
    pos = source.tell()
    source.seek(0)
    try:
        with dest.open("wb") as out:
            shutil.copyfileobj(source, out)
    finally:
        source.seek(pos)


class _UnrarOwnedStream(DelegatingStream):
    """Stdout wrapper that terminates the owning ``unrar`` process on close."""

    def __init__(self, stdout: BinaryIO, proc: subprocess.Popen[bytes]) -> None:
        super().__init__(stdout)
        self._proc = proc

    def close(self) -> None:
        if self.closed:
            return
        try:
            self._inner.close()
        finally:
            if self._proc.poll() is None:
                terminate_unrar(self._proc)
            else:
                # Drain wait status if the process already exited on EOF.
                try:
                    self._proc.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    terminate_unrar(self._proc)
            rc = self._proc.returncode
            # Mark closed without relying on DelegatingStream (already closed inner).
            super(DelegatingStream, self).close()
            # unrar exit 11 = bad password (RARLAB).
            if rc == 11:
                raise EncryptionError("Incorrect RAR password or encrypted member")


class RarReader(BaseArchiveReader):
    """Reads RAR archives: native metadata parse + RARLAB ``unrar`` for data."""

    _SUPPORTS_RANDOM_ACCESS = True
    _MEMBER_LIST_UPFRONT = True

    def __init__(
        self,
        source: Path | BinaryIO,
        streaming: bool,
        passwords: _PasswordCandidates | None,
        encoding: str | None,
        archive_name: str | None,
        config: ArchiveyConfig,
        collector: DiagnosticCollector | None = None,
        member_streams: MemberStreams = MemberStreams(0),
        open_site: OpenSite | None = None,
        *,
        volume_count: int = 1,
    ) -> None:
        super().__init__(
            ArchiveFormat.RAR,
            streaming,
            archive_name,
            config,
            collector=collector,
            member_streams=member_streams,
            open_site=open_site,
        )
        del encoding  # RAR names are decoded by the native parser.
        self._source = source
        self._passwords = passwords or _PasswordCandidates()
        self._volume_count = getattr(source, "volume_count", volume_count)
        self._temp_path: Path | None = None
        self._temp_dir: Path | None = None
        self._owned_concat: ConcatenatedFile | None = None
        self._archive_path: Path | None = None
        self._volume_paths: list[Path] = []
        self._live_unrar: subprocess.Popen[bytes] | None = None

        if is_stream(source) and not is_seekable(source):
            raise StreamNotSeekableError(
                "RAR archives require a seekable source: headers and stored member "
                "ranges are addressed by offsets.",
                archive_name=archive_name,
                source_format=ArchiveFormat.RAR,
            )

        self._shared = self._open_shared_source(source)
        self._archive, self._unrar_password = self._parse_archive()
        if self._archive.is_volume or self._volume_count > 1:
            self._volume_count = max(self._volume_count, len(self._volume_paths) or 1)
        self._members = [self._to_member(info) for info in self._archive.members]

    def _open_shared_source(self, source: Path | BinaryIO) -> SharedSource:
        """Build SharedSource, discovering/materializing volumes as needed."""
        if isinstance(source, Path):
            siblings = discover_volume_siblings(source)
            if siblings is not None and len(siblings) > 1:
                self._volume_paths = siblings
                self._volume_count = len(siblings)
                self._archive_path = siblings[0]
                concat = ConcatenatedFile(siblings)
                self._owned_concat = concat
                return SharedSource(concat)
            self._volume_paths = [source]
            self._archive_path = source
            return SharedSource(source)

        if isinstance(source, ConcatenatedFile):
            paths = source.volume_paths
            if paths:
                # Path volumes: prefer real sibling files for unrar.
                self._volume_paths = paths
                self._volume_count = len(paths)
                self._archive_path = paths[0]
                return SharedSource(source)
            # Stream volumes: materialize for unrar; parse from originals.
            items = source.volume_items
            self._volume_count = len(items)
            self._materialize_stream_volumes(items)
            return SharedSource(source)

        # Single non-path stream — materialize later when unrar is needed.
        return SharedSource(source)

    def _materialize_stream_volumes(self, items: Sequence[Path | BinaryIO]) -> None:
        """Write ordered volumes into a temp dir with ``name.partN.rar`` names."""
        temp_dir = Path(tempfile.mkdtemp(prefix="archivey-rar-vol-"))
        self._temp_dir = temp_dir
        stem = "archive"
        if self._archive_name:
            stem = Path(self._archive_name).stem or stem
        paths: list[Path] = []
        try:
            for index, item in enumerate(items, start=1):
                dest = temp_dir / f"{stem}.part{index}.rar"
                if isinstance(item, Path):
                    shutil.copy2(item, dest)
                else:
                    _copy_stream_to_path(item, dest)
                paths.append(dest)
        except BaseException:
            shutil.rmtree(temp_dir, ignore_errors=True)
            self._temp_dir = None
            raise
        self._volume_paths = paths
        self._archive_path = paths[0]

    def _parse_archive(self) -> tuple[RarArchive, str | None]:
        def parse(password: bytes | None) -> RarArchive:
            if len(self._volume_paths) > 1:
                handles: list[BinaryIO] = []
                try:
                    for path in self._volume_paths:
                        handles.append(path.open("rb"))
                    return parse_rar_volumes(handles, password=password)
                finally:
                    for handle in handles:
                        handle.close()

            # Single volume — may still be a ConcatenatedFile of streams that we
            # already materialized into _volume_paths of length 1, or a lone file.
            if self._volume_paths:
                with self._volume_paths[0].open("rb") as handle:
                    return parse_rar_archive(handle, password=password)

            view = self._shared.view(0)
            try:
                view.seek(0)
                archive = parse_rar_archive(view, password=password)
                if archive.needs_next_volume or archive.is_volume:
                    raise TruncatedError(
                        "Incomplete RAR multi-volume set: additional volumes required"
                    )
                return archive
            finally:
                view.close()

        try:
            try:
                archive = parse(None)
                # Incomplete set opened as a lone volume-1 path with no siblings.
                if archive.needs_next_volume and len(self._volume_paths) <= 1:
                    raise TruncatedError(
                        "Incomplete RAR multi-volume set: end of archive expects "
                        "another volume"
                    )
                return archive, self._first_candidate_str()
            except EncryptionError:
                if not self._passwords.has_passwords():
                    raise

                def confirm(password: bytes) -> RarArchive:
                    return parse(password)

                archive = self._passwords.attempt(None, confirm)
                if archive.needs_next_volume and len(self._volume_paths) <= 1:
                    raise TruncatedError(
                        "Incomplete RAR multi-volume set: end of archive expects "
                        "another volume"
                    )
                return archive, self._first_candidate_str()
        except _PasswordCandidatesExhausted as exc:
            message = (
                exc.last_error.message
                if exc.last_error is not None
                else "Password required to decrypt RAR headers"
            )
            raise EncryptionError(message) from exc

    def _first_candidate_str(self) -> str | None:
        for password in self._passwords.iter_candidates():
            return _password_as_str(password)
        return None

    def _ensure_archive_path(self) -> Path:
        """Return a filesystem path ``unrar`` can open (materialize streams once)."""
        if self._archive_path is not None:
            return self._archive_path
        # Single stream source: write one temp .rar for unrar.
        fd, name = tempfile.mkstemp(suffix=".rar")
        path = Path(name)
        try:
            with os.fdopen(fd, "wb") as out:
                view = self._shared.view(0)
                try:
                    while True:
                        chunk = view.read(1 << 20)
                        if not chunk:
                            break
                        out.write(chunk)
                finally:
                    view.close()
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        self._temp_path = path
        self._archive_path = path
        return path

    def _iter_members(self) -> Iterator[ArchiveMember]:
        yield from self._members

    def _to_member(self, info: RarMemberInfo) -> ArchiveMember:
        member_type = self._member_type(info)
        name = normalize_member_name(
            info.filename,
            member_type,
            backslash_is_separator=True,
        )
        raw_name = (
            info.orig_filename
            if info.orig_filename is not None
            else info.filename.encode("utf-8", errors="surrogateescape")
        )
        link_target: str | None = None
        extra: dict[str, object] = {}
        if info.file_redir is not None:
            link_target = info.file_redir[2]
            if info.file_redir[0] == _RAR5_XREDIR_WINDOWS_JUNCTION:
                extra[EXTRA_IS_JUNCTION] = True

        create_system = (
            _RAR_HOST_OS_TO_CREATE_SYSTEM.get(info.host_os, CreateSystem.UNKNOWN)
            if info.host_os is not None
            else CreateSystem.UNKNOWN
        )
        mode: int | None = None
        windows_attrs: int | None = None
        if info.mode is not None:
            # RAR5 stores mode as a vint; mask before S_IMODE so hostile values cannot
            # raise OverflowError from the C helper.
            raw_mode = info.mode & 0xFFFFFFFF
            if info.host_os == 3:  # Unix
                mode = stat.S_IMODE(raw_mode)
            elif info.host_os == 2:  # Win32
                windows_attrs = raw_mode

        member = ArchiveMember(
            type=member_type,
            name=name,
            raw_name=raw_name,
            size=info.file_size,
            compressed_size=info.compress_size,
            modified=info.mtime,
            mode=mode,
            compression=_compression_for(info),
            is_encrypted=info.is_encrypted,
            create_system=create_system,
            windows_attrs=windows_attrs,
            hashes=_member_hashes(info),
            link_target=link_target,
            extra=extra,
            _raw=info,
        )
        emit_member_name_normalized(
            self._diagnostics_collector,
            member=member,
            presented_name=info.filename,
            archive_name=self._archive_name,
        )
        return member

    @staticmethod
    def _member_type(info: RarMemberInfo) -> MemberType:
        if info.is_directory:
            return MemberType.DIRECTORY
        if info.is_hardlink_or_copy:
            return MemberType.HARDLINK
        if info.is_symlink:
            return MemberType.SYMLINK
        return MemberType.FILE

    def _iter_with_data(self) -> Iterator[tuple[ArchiveMember, ArchiveStream | None]]:
        if not self._archive.is_solid:
            # Nonsolid: default lazy per-member named opens (never ALL-pipe demux).
            yield from super()._iter_with_data()
            return

        path = self._ensure_archive_path()
        proc, stdout = open_unrar_p(path, password=self._unrar_password)
        self._live_unrar = proc
        owned: BinaryIO = _UnrarOwnedStream(stdout, proc)
        solid = SolidBlockReader(owned)
        previous: ArchiveStream | None = None
        pipe_offset = 0
        try:
            for member in self._members:
                if previous is not None:
                    previous.close()
                    previous = None
                raw = member._raw
                assert isinstance(raw, RarMemberInfo)
                if not raw.is_payload_file() or not member.is_file:
                    yield member, None
                    continue
                size = _member_stream_size(member)
                try:
                    inner = solid.open_member(pipe_offset, size)
                except EOFError as exc:
                    raise TruncatedError(
                        "RAR solid stream ended before the requested member"
                    ) from exc
                pipe_offset += size
                stream = self._wrap_payload_stream(inner, member)
                previous = stream
                yield member, stream
        finally:
            if previous is not None:
                previous.close()
            solid.close()
            self._live_unrar = None

    def _wrap_payload_stream(
        self, inner: BinaryIO, member: ArchiveMember
    ) -> ArchiveStream:
        if member.hashes:
            inner = VerifyingStream(
                inner,
                member.hashes,
                collector=self._diagnostics_collector,
                member=member,
                archive_name=self._archive_name,
            )
        return self._wrap_member_stream(inner, member.name, size=member.size)

    def _can_direct_read(self, info: RarMemberInfo) -> bool:
        return (
            info.compress_type == _RAR_METHOD_STORED
            and not info.is_encrypted
            and not info.file_solid
            and not info.split_after
            and not info.split_before
            and not info.spanned_volumes
        )

    def _direct_view(self, info: RarMemberInfo, length: int | None = None) -> BinaryIO:
        size = info.file_size if length is None else length
        return self._shared.view(info.data_offset, size)

    def _ensure_link_target(self, member: ArchiveMember) -> None:
        if member.type != MemberType.SYMLINK or member.link_target is not None:
            return
        raw = member._raw
        assert isinstance(raw, RarMemberInfo)
        if raw.file_redir is not None:
            member.link_target = raw.file_redir[2]
            return
        # RAR4: symlink target stored as M0 member data (even when file_solid).
        if (
            raw.compress_type == _RAR_METHOD_STORED
            and not raw.is_encrypted
            and raw.file_size > 0
            and not raw.split_before
            and not raw.split_after
        ):
            view = self._shared.view(raw.data_offset, raw.file_size)
            try:
                data = view.read()
            finally:
                view.close()
            member.link_target = data.decode("utf-8", errors="surrogateescape")
            return
        # Encrypted / compressed target without usable direct bytes: leave unset.
        return

    def _open_member(self, member: ArchiveMember) -> ArchiveStream:
        raw = member._raw
        assert isinstance(raw, RarMemberInfo)

        if self._can_direct_read(raw):
            inner: BinaryIO = self._direct_view(raw)
            return self._wrap_payload_stream(inner, member)

        path = self._ensure_archive_path()
        # unrar needs the archive-stored path (forward slashes), not the normalized name.
        proc, stdout = open_unrar_p(
            path,
            password=self._unrar_password,
            member=raw.filename,
        )
        self._live_unrar = proc
        owned = _UnrarOwnedStream(stdout, proc)
        size = _member_stream_size(member)
        sliced: BinaryIO = SlicingStream(owned, length=size, own_source=True)
        try:
            return self._wrap_payload_stream(sliced, member)
        except BaseException:
            sliced.close()
            self._live_unrar = None
            raise

    def _get_archive_info(self) -> ArchiveInfo:
        is_solid = self._archive.is_solid
        cost = CostReceipt(
            listing_cost=ListingCost.INDEXED,
            access_cost=AccessCost.SOLID if is_solid else AccessCost.DIRECT,
            stream_capability=StreamCapability.SEEKABLE,
            # RAR solid is one continuous compression context; block count is unknown.
            solid_block_count=None,
        )
        any_encrypted = any(m.is_encrypted for m in self._archive.members)
        is_multivolume = (
            self._archive.is_volume
            or self._volume_count > 1
            or len(self._volume_paths) > 1
        )
        return ArchiveInfo(
            format=ArchiveFormat.RAR,
            format_version=str(self._archive.version),
            is_solid=is_solid,
            member_count=len(self._members),
            comment=self._archive.comment,
            is_encrypted=self._archive.has_header_encryption or any_encrypted,
            is_multivolume=is_multivolume,
            cost=cost,
            extra={
                "rar.volume_count": max(self._volume_count, len(self._volume_paths))
            },
        )

    def _close_archive(self) -> None:
        terminate_unrar(self._live_unrar)
        self._live_unrar = None
        self._shared.close()
        if self._owned_concat is not None:
            try:
                self._owned_concat.close()
            except OSError:
                pass
            self._owned_concat = None
        if self._temp_path is not None:
            try:
                self._temp_path.unlink(missing_ok=True)
            except OSError:
                pass
            self._temp_path = None
        if self._temp_dir is not None:
            shutil.rmtree(self._temp_dir, ignore_errors=True)
            self._temp_dir = None


class RarReadBackend(ReadBackend):
    """Backend factory for RAR archives."""

    FORMATS: tuple[ArchiveFormat, ...] = (ArchiveFormat.RAR,)
    EXTENSIONS: Mapping[str, ArchiveFormat] = {".rar": ArchiveFormat.RAR}
    MAGIC: tuple[MagicSignature, ...] = (
        MagicSignature(0, RAR5_ID, ArchiveFormat.RAR),
        MagicSignature(0, RAR_ID, ArchiveFormat.RAR),
    )
    SUPPORTS_PASSWORD = True
    SUPPORTS_STREAMING_NON_SEEKABLE = False
    OPTIONAL_DEPENDENCY = None

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
    ) -> RarReader:
        del format
        return RarReader(
            source,
            streaming,
            passwords,
            encoding,
            archive_name,
            config,
            collector=collector,
            member_streams=member_streams,
            open_site=open_site,
        )


register_reader(RarReadBackend)
