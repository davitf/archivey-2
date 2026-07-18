"""TAR backend on the v2 ABC, backed by the stdlib ``tarfile`` module.

Random-access reading (``streaming=False``) scans 512-byte headers on a seekable source
(decompressing first for a compressed tar) and opens any member on demand. Forward-only
reading (``streaming=True``) walks the archive in one progressive pass — including on a
non-seekable source for plain and compressed tars — via ``_iter_with_data()`` /
``stream_members()``. After a full scan or streaming pass the end-of-archive is checked
(``_verify_tar_eof``): a rejected header raises ``CorruptionError`` regardless of config,
while a missing two-block null trailer warns unless ``config.strict_archive_eof`` escalates
it to ``TruncatedError``.
"""

from __future__ import annotations

import stat
import tarfile
import threading
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import BinaryIO, Iterator, Literal, Mapping, cast

from archivey.config import ArchiveyConfig
from archivey.cost import (
    AccessCost,
    CostReceipt,
    ListingCost,
    StreamCapability,
)
from archivey.diagnostics import (
    ArchiveEofContext,
    DiagnosticCode,
    MemberTimestampContext,
)
from archivey.exceptions import (
    ArchiveyError,
    CorruptionError,
    TruncatedError,
)
from archivey.internal.base_reader import BaseArchiveReader, ReadBackend
from archivey.internal.config import stream_config_from_archivey
from archivey.internal.diagnostics_collector import DiagnosticCollector
from archivey.internal.logs import backends as backends_logger
from archivey.internal.naming import emit_member_name_normalized, normalize_member_name
from archivey.internal.open_site import OpenSite
from archivey.internal.password import _PasswordCandidates
from archivey.internal.registry import register_reader
from archivey.internal.streams.archive_stream import ArchiveStream
from archivey.internal.streams.codecs import (
    SINGLE_FILE_CODECS,
    codec_for_stream_format,
    open_codec_stream,
)
from archivey.internal.streams.streamtools import (
    LockedStream,
    ensure_binaryio,
    ensure_bufferedio,
    is_seekable,
    is_stream,
)
from archivey.types import (
    ArchiveFormat,
    ArchiveInfo,
    ArchiveMember,
    CompressionAlgorithm,
    CompressionMethod,
    ContainerFormat,
    MagicSignature,
    MemberStreams,
    MemberType,
    StreamFormat,
)

# Every compressed-tar combination the codec layer can decode: TAR composed with each
# standalone stream codec (gz/bz2/xz/zst/lz4/lzip/lzma-alone/zlib/brotli/unix-compress).
# The common ones have named ArchiveFormat constants; the rest are equal-by-value
# on-demand instances.
_TAR_COMPRESSED: tuple[ArchiveFormat, ...] = tuple(
    ArchiveFormat(ContainerFormat.TAR, codec.stream_format)
    for codec in SINGLE_FILE_CODECS
    if codec.stream_format is not None
)

# Plain TAR plus every compressed combination the codec layer can decode.
_TAR_FORMATS: tuple[ArchiveFormat, ...] = (ArchiveFormat.TAR, *_TAR_COMPRESSED)

# Canonical extensions are derived from each format (TAR -> ".tar", TAR_GZ -> ".tar.gz",
# (TAR, LZIP) -> ".tar.lz", …); only the short aliases (.tgz/.tbz/…) are listed by hand.
# (Built at module scope: a dict comprehension in the class body can't see class-level names.)
_TAR_EXTENSIONS: dict[str, ArchiveFormat] = {
    f".{fmt.file_extension()}": fmt for fmt in _TAR_FORMATS
}
_TAR_EXTENSIONS.update(
    {
        ".tgz": ArchiveFormat.TAR_GZ,
        ".tbz2": ArchiveFormat.TAR_BZ2,
        ".tbz": ArchiveFormat.TAR_BZ2,
        ".txz": ArchiveFormat.TAR_XZ,
        ".tzst": ArchiveFormat.TAR_ZST,
        ".tlz": ArchiveFormat(ContainerFormat.TAR, StreamFormat.LZIP),
    }
)


def _member_type(info: tarfile.TarInfo) -> MemberType:
    if info.isdir():
        return MemberType.DIRECTORY
    if info.issym():
        return MemberType.SYMLINK
    if info.islnk():
        return MemberType.HARDLINK
    if info.isfile():
        return MemberType.FILE
    # Character/block devices, FIFOs, contiguous files, GNU long-name placeholders, …
    return MemberType.OTHER


# Shared across FILE/HARDLINK members — avoid per-member CompressionMethod construction.
_STORED_COMPRESSION: tuple[CompressionMethod, ...] = (
    CompressionMethod(algo=CompressionAlgorithm.STORED),
)


def _pax_time(info: tarfile.TarInfo, key: str) -> datetime | None:
    """Parse a PAX ``atime``/``ctime`` (float Unix seconds) into a tz-aware UTC datetime.

    ``tarfile`` folds the PAX ``mtime`` into ``TarInfo.mtime`` itself, but leaves the
    access/creation times only in ``pax_headers``; surface them here for completeness.
    """
    raw = info.pax_headers.get(key)
    if raw is None:
        return None
    try:
        return datetime.fromtimestamp(float(raw), tz=timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None


class _EofProbeStream:
    """Transparent read/seek proxy over the seekable fileobj handed to stdlib
    ``tarfile`` in random-access mode, remembering the ``(offset, bytes)`` of the most
    recent ``read`` (empty reads included).

    After the header scan, that read is tarfile's attempt to parse a header at the
    end-of-archive position. Comparing its offset to the last member's block-aligned end
    lets :meth:`TarReader._verify_tar_eof` inspect the exact block tarfile stopped on —
    telling a rejected header (corruption) apart from a merely missing trailer — without
    seeking backwards, which on a compressed source would force a re-decompression.

    tarfile treats this as an external fileobj (``read``/``seek``/``tell``/``seekable``
    only) and never closes it; the reader closes the wrapped stream via ``_owned_stream``.
    """

    def __init__(self, inner: BinaryIO) -> None:
        self._inner = inner
        # Offsets share tarfile's coordinate space (both anchored at the wrapped
        # stream's current position), so they compare directly to TarInfo offsets.
        self._pos = inner.tell() if inner.seekable() else 0
        self.last_read: tuple[int, bytes] = (-1, b"")

    def read(self, size: int = -1) -> bytes:
        offset = self._pos
        chunk = self._inner.read(size)
        self._pos += len(chunk)
        self.last_read = (offset, chunk)
        return chunk

    def seek(self, offset: int, whence: int = 0) -> int:
        self._inner.seek(offset, whence)
        self._pos = self._inner.tell()
        return self._pos

    def tell(self) -> int:
        return self._pos

    def seekable(self) -> bool:
        return self._inner.seekable()

    def readable(self) -> bool:
        return True

    def close(self) -> None:
        # No-op: the reader owns the wrapped stream's lifetime (``_owned_stream``); a
        # stray tarfile call must not tear the shared handle down early.
        pass


class TarReader(BaseArchiveReader):
    """Reads a TAR archive (plain or compressed) via stdlib ``tarfile``."""

    _SUPPORTS_RANDOM_ACCESS = True
    # TAR has no central directory: the member list only exists after a scan, so it is not
    # "available without scanning" (listing cost is REQUIRES_SCANNING / REQUIRES_DECOMPRESSION,
    # not INDEXED). Once iterated, the base serves the cached list anyway.
    _MEMBER_LIST_UPFRONT = False

    def __init__(
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
    ) -> None:
        # password rejection is central: open_archive checks ReadBackend.SUPPORTS_PASSWORD.
        super().__init__(
            format,
            streaming,
            archive_name,
            config,
            collector=collector,
            member_streams=member_streams,
            open_site=open_site,
        )
        self._encoding = encoding
        self._source = source
        self._compressed = format.stream != StreamFormat.UNCOMPRESSED
        # Random-access EOF probe: set when the fileobj is wrapped (non-streaming opens),
        # snapshotted into ``_eof_header_rejected`` right after the header scan.
        self._eof_probe_stream: _EofProbeStream | None = None
        self._eof_header_rejected: bool = False
        # A decompression stream we open and therefore must close (compressed tars only);
        # for a plain tar from a path, tarfile owns and closes the file handle itself.
        self._owned_stream: BinaryIO | None = None
        # Shared-handle lock: CONCURRENT readers serialize every shared-fileobj op;
        # streaming readers also take a lock (exclusive / normally uncontended) so the
        # same critical-section shape covers init, progressive walk, extractfile, EOF,
        # and close (tar-concurrent-open 2.6).
        self._handle_lock = (
            threading.Lock()
            if MemberStreams.CONCURRENT in member_streams or streaming
            else None
        )

        try:
            with self._handle_guard():
                self._tar = self._open_tarfile(
                    source, format, streaming, member_streams=member_streams
                )
        except tarfile.TarError as exc:
            # Only tarfile's own (format) errors are translated; a genuine OSError from the
            # underlying handle propagates unchanged (see error-handling: "Genuine runtime
            # and I/O errors are not reclassified").
            raise self._translate_open_error(exc) from exc

    def _open_tarfile(
        self,
        source: Path | BinaryIO,
        format: ArchiveFormat,
        streaming: bool,
        *,
        member_streams: MemberStreams,
    ) -> tarfile.TarFile:
        if self._compressed:
            codec = codec_for_stream_format(format.stream)
            # A non-seekable stream source is wrapped so the live decompression-ratio guard
            # can see compressed bytes consumed; a path / seekable stream (cheap size known)
            # is returned unchanged and uses the static ratio.
            counted = self._wrap_compressed_input(source)
            if self._measure and is_stream(counted):
                counted = self._track_source_seeks(counted)
            codec_source: str | BinaryIO = (
                str(counted) if isinstance(counted, Path) else counted
            )
            stream = open_codec_stream(
                codec,
                codec_source,
                config=stream_config_from_archivey(
                    self._config,
                    streaming=streaming,
                    seekable=MemberStreams.SEEKABLE in member_streams,
                ),
                stamp=lambda exc: self._stamp_error_context(exc),
                collector=self._diagnostics_collector,
            )
            # tarfile can mis-handle a short read() (fewer bytes than requested) from a
            # decompressor; a BufferedReader in front guarantees full-sized reads.
            self._owned_stream = cast("BinaryIO", ensure_bufferedio(stream))
            return self._tarfile_open(
                fileobj=self._wrap_eof_probe(self._owned_stream, streaming),
                streaming=streaming,
            )
        if isinstance(source, Path):
            if not self._measure and streaming:
                # Forward-only from a path with no measurement: let tarfile own and close
                # the handle. The EOF probe only applies to random access, so nothing is
                # lost by keeping this fast path.
                return self._tarfile_open(name=str(source), streaming=streaming)
            # Open the handle ourselves so random access can carry the EOF probe (and, when
            # measuring, the seek instrumentation); we own the fp for close.
            fp: BinaryIO = open(source, "rb")
            if self._measure:
                fp = cast("BinaryIO", self._track_source_seeks(fp))
            self._owned_stream = fp
            return self._tarfile_open(
                name=str(source),
                fileobj=self._wrap_eof_probe(fp, streaming),
                streaming=streaming,
            )
        return self._tarfile_open(
            fileobj=self._wrap_eof_probe(
                cast("BinaryIO", self._track_source_seeks(source)), streaming
            ),
            streaming=streaming,
        )

    def _wrap_eof_probe(self, fileobj: BinaryIO, streaming: bool) -> BinaryIO:
        """Wrap a random-access fileobj so the end-of-archive check can inspect the block
        tarfile stopped on. Forward-only (streaming) opens get no probe — tarfile's
        ``_Stream`` hides its header reads and a consumed block cannot be recovered there.
        """
        if streaming:
            return fileobj
        probe = _EofProbeStream(fileobj)
        self._eof_probe_stream = probe
        return cast("BinaryIO", probe)

    def _tarfile_open(
        self,
        *,
        name: str | None = None,
        fileobj: BinaryIO | None = None,
        streaming: bool = False,
    ) -> tarfile.TarFile:
        # mode="r:" reads an *uncompressed* tar stream with random access; mode="r|" is
        # forward-only (required for non-seekable sources). We feed either the raw file
        # (plain tar) or our own decompressor (compressed tar), never tarfile's native
        # r:gz/r:bz2 modes.
        mode = "r|" if streaming else "r:"
        return tarfile.open(
            name=name,
            fileobj=fileobj,
            mode=mode,
            errorlevel=1,  # raise on fatal read errors (truncation/corruption surface below)
            encoding=self._encoding,  # None → tarfile applies its utf-8 default
        )

    def _translate_open_error(self, exc: Exception) -> ArchiveyError:
        translated = self._translate_exception(exc)
        if translated is not None:
            self._stamp_error_context(translated)
            return translated
        err = CorruptionError(f"Could not open TAR archive: {exc!r}")
        self._stamp_error_context(err)
        return err

    def _translate_exception(self, exc: Exception) -> ArchiveyError | None:
        if isinstance(exc, tarfile.ReadError):
            text = str(exc).lower()
            if "end of data" in text or "truncat" in text or "empty file" in text:
                return TruncatedError(f"TAR archive is truncated: {exc!r}")
            return CorruptionError(f"Error reading TAR archive: {exc!r}")
        if isinstance(exc, EOFError):
            return TruncatedError(f"TAR archive is truncated: {exc!r}")
        return None

    def _iter_members(self) -> Iterator[ArchiveMember]:
        if self._streaming:
            yield from self._iter_members_progressive()
            return
        # The error boundary sits OUTSIDE the handle guard, so translation/stamping
        # never run under the shared-fileobj lock. An exception the translator does
        # not recognize (a genuine OSError from the source) propagates unchanged.
        with self._translated_errors():
            # Pinned-library audit: TarFile.getmembers() drives seek/tell/read through
            # _load()/next() on the shared fileobj — must run under the handle lock.
            with self._handle_guard():
                members = self._tar.getmembers()  # forces the full header scan
                # Snapshot the EOF probe now, while the handle sits just past the scan and
                # before any member extraction can move it.
                self._capture_eof_probe(members)
        for info in members:
            yield self._to_member(info)
        self._verify_tar_eof()

    def _iter_members_progressive(self) -> Iterator[ArchiveMember]:
        """Forward-only member walk — never calls ``getmembers()``.

        Yields bare members; the base's shared progressive pass stamps ids and resolves
        backward links. Shared-handle ops run under ``_handle_lock`` when present.
        """
        with self._translated_errors():
            if self._handle_lock is not None:
                # Hold the lock only around each next() so a yielded consumer can open
                # the current member without deadlock (streaming is single-owner).
                tar_iter = iter(self._tar)
                while True:
                    with self._handle_lock:
                        try:
                            info = next(tar_iter)
                        except StopIteration:
                            break
                    yield self._to_member(info)
            else:
                for info in self._tar:
                    yield self._to_member(info)
        self._verify_tar_eof()

    def _iter_with_data(self) -> Iterator[tuple[ArchiveMember, ArchiveStream | None]]:
        if not self._streaming:
            yield from super()._iter_with_data()
            return
        # Pull from the shared instance-held progressive pass so __iter__,
        # stream_members, and scan_members share one cursor and finalization.
        with self._translated_errors():
            for member in self._begin_forward_pass():
                if member.is_file:
                    info = cast("tarfile.TarInfo", member._raw)
                    with self._handle_guard():
                        raw = self._tar.extractfile(info)
                    if raw is None:
                        raw = BytesIO(b"")
                    stream: BinaryIO = ensure_binaryio(raw)
                    if self._handle_lock is not None:
                        stream = LockedStream(stream, self._handle_lock)
                    yield (
                        member,
                        self._wrap_member_stream(stream, member.name, size=member.size),
                    )
                else:
                    yield member, None

    def _capture_eof_probe(self, members: list[tarfile.TarInfo]) -> None:
        """Snapshot whether tarfile stopped the header scan on a *rejected* (non-null)
        header block, using the random-access EOF probe.

        Reads the probe's recorded block rather than the live handle position, so it is
        robust against later member extraction moving the shared handle. A full non-null
        block sitting exactly at the last member's block-aligned end is what tarfile read
        and rejected when it treated a corrupt member header as a clean end of archive —
        including when that bad header is the archive's final block (which the
        trailing-block check in :meth:`_verify_tar_eof` reads past and cannot see).
        """
        self._eof_header_rejected = False
        probe = self._eof_probe_stream
        if probe is None or not members:
            return
        offset, chunk = probe.last_read
        last = members[-1]
        next_header = last.offset_data + ((last.size + 511) & ~511)
        if offset == next_header and len(chunk) == 512 and chunk != b"\x00" * 512:
            self._eof_header_rejected = True

    def _verify_tar_eof(self) -> None:
        """Verify the two-block null end-of-archive marker and surface a rejected header
        as corruption.

        In random-access mode ``_capture_eof_probe`` has already inspected the block
        tarfile stopped on. A full non-null block there means tarfile rejected a header —
        a corrupt member header after the first, treated as a silent early end, including
        when it is the archive's *final* block — which escalates to ``CorruptionError``
        regardless of ``strict_archive_eof``.

        Otherwise (and for forward-only streaming, which has no probe) it inspects the
        block following tarfile's stop. ``tarfile`` has already consumed the *first* null
        trailer block (stopping on it via ``EOFHeaderError`` with ``ignore_zeros=False``),
        so we only confirm the *second*: reading two blocks here would demand a third
        block of trailing zeros and wrongly flag a minimal ``tar -b1`` trailer. Two null
        blocks are valid; a non-null block is corruption (a rejected trailer/header); a
        short or empty read is a truncated or absent trailer, which stays a warning unless
        ``strict_archive_eof`` escalates it to ``TruncatedError``.

        Streaming cannot see a rejected *final* header (tarfile's ``_Stream`` hides the
        block and it cannot be recovered without re-reading), so that one case surfaces as
        a missing-trailer warning there rather than corruption — see
        ``docs/internal/known-issues.md``.
        """
        if self._eof_header_rejected:
            self._emit_eof_marker(
                observed_bytes=512, observed_kind="nonzero", corrupt=True
            )
            return
        fileobj = self._tar.fileobj
        if fileobj is None:
            return
        with self._handle_guard():
            chunk = fileobj.read(512)
        if len(chunk) == 512 and chunk == b"\x00" * 512:
            return
        if len(chunk) == 512:
            # A non-null block where the second trailer block belongs: tarfile treated a
            # bad block as a clean end (or trailing junk followed a lone zero block).
            self._emit_eof_marker(
                observed_bytes=512, observed_kind="nonzero", corrupt=True
            )
            return
        observed_kind: Literal["absent", "short"] = (
            "absent" if len(chunk) == 0 else "short"
        )
        self._emit_eof_marker(
            observed_bytes=len(chunk), observed_kind=observed_kind, corrupt=False
        )

    def _emit_eof_marker(
        self,
        *,
        observed_bytes: int,
        observed_kind: Literal["absent", "short", "nonzero"],
        corrupt: bool,
    ) -> None:
        if corrupt:
            message = (
                "TAR archive is corrupt: a non-null block appears where the "
                "end-of-archive marker was expected. Stdlib tarfile treats a corrupt "
                "member header after the first as a clean end of archive, so a silently "
                "shortened listing surfaces here."
            )
            escalate_as: type[BaseException] | None = CorruptionError
        else:
            message = (
                "TAR archive may be truncated: missing or short end-of-archive marker "
                "block(s)."
            )
            escalate_as = TruncatedError if self._config.strict_archive_eof else None
        escalate_kwargs: dict[str, object] | None = None
        if escalate_as is not None:
            escalate_kwargs = {
                "source_format": self._format,
                "archive_name": self._archive_name,
            }
        self._diagnostics_collector.emit(
            code=DiagnosticCode.ARCHIVE_EOF_MARKER_MISSING,
            message=message,
            context=ArchiveEofContext(
                archive_name=self._archive_name,
                format="tar",
                expected_marker="two_zero_blocks",
                expected_bytes=1024,
                observed_bytes=observed_bytes,
                observed_kind=observed_kind,
            ),
            logger=backends_logger,
            escalate_as=escalate_as,
            escalate_kwargs=escalate_kwargs,
        )

    def _source_stream_capability(self) -> StreamCapability:
        if isinstance(self._source, Path):
            return StreamCapability.SEEKABLE
        if is_seekable(self._source):
            return StreamCapability.SEEKABLE
        return StreamCapability.FORWARD_ONLY

    def _to_member(self, info: tarfile.TarInfo) -> ArchiveMember:
        member_type = _member_type(info)
        # TAR is a POSIX format: a backslash is a legal filename character, not a separator.
        presented = info.name
        name = normalize_member_name(
            presented, member_type, backslash_is_separator=False
        )
        # Re-encode the decoded name with the archive's own codec to recover the stored bytes
        # (tarfile decodes with surrogateescape, which round-trips losslessly).
        raw_name = info.name.encode(self._tar.encoding, errors="surrogateescape")

        link_target = (
            info.linkname
            if member_type in (MemberType.SYMLINK, MemberType.HARDLINK)
            else None
        )

        # tarfile folds a PAX mtime (sub-second/timezone) into TarInfo.mtime already, so this
        # one field honors both the standard ustar mtime and the PAX override. A hostile
        # out-of-range value (e.g. a crafted PAX mtime beyond datetime's range) must not
        # sink the whole listing, so it degrades to None like _pax_time does.
        mtime_invalid = False
        try:
            modified = datetime.fromtimestamp(info.mtime, tz=timezone.utc)
        except (ValueError, OverflowError, OSError):
            mtime_invalid = True
            modified = None

        compression = (
            _STORED_COMPRESSION
            if member_type in (MemberType.FILE, MemberType.HARDLINK)
            else ()
        )

        extra: dict[str, object] = {"tar.type": info.type}
        if info.pax_headers:
            extra["tar.pax_headers"] = dict(info.pax_headers)
        if info.isdev():
            extra["tar.devmajor"] = info.devmajor
            extra["tar.devminor"] = info.devminor

        member = ArchiveMember(
            type=member_type,
            name=name,
            raw_name=raw_name,
            size=info.size if member_type == MemberType.FILE else None,
            modified=modified,
            mode=stat.S_IMODE(info.mode),
            uid=info.uid,
            gid=info.gid,
            compression=compression,
            extra=extra,
            _raw=info,  # carry the TarInfo so _open_member needs no name/id lookup table
        )
        # Skip defaulted None/False fields on the listing hot path (perf review L2).
        accessed = _pax_time(info, "atime")
        if accessed is not None:
            member.accessed = accessed
        created = _pax_time(info, "ctime")
        if created is not None:
            member.created = created
        if info.uname:
            member.uname = info.uname
        if info.gname:
            member.gname = info.gname
        if link_target is not None:
            member.link_target = link_target
        if info.type == tarfile.GNUTYPE_SPARSE:
            member.is_sparse = True
        emit_member_name_normalized(
            self._diagnostics_collector,
            member=member,
            presented_name=presented,
            archive_name=self._archive_name,
        )
        if mtime_invalid:
            self._diagnostics_collector.emit(
                code=DiagnosticCode.MEMBER_TIMESTAMP_INVALID,
                message=f"Invalid TAR mtime for {info.name!r}: {info.mtime!r}",
                context=MemberTimestampContext(
                    archive_name=self._archive_name,
                    member_name=member.name,
                    member_id=member._member_id,
                    field="mtime",
                    source="tar",
                    value_repr=repr(info.mtime),
                ),
                member=member,
                attach_to_member=True,
                logger=backends_logger,
            )
        return member

    def _open_member(self, member: ArchiveMember) -> ArchiveStream:
        info = member._raw
        assert isinstance(info, tarfile.TarInfo), (
            "TAR member is missing its TarInfo handle"
        )
        # Boundary outside the guard: translation/stamping never run while the
        # shared-fileobj lock is held.
        with self._translated_errors(member.name):
            with self._handle_guard():
                extracted = self._tar.extractfile(info)
        # tarfile stubs ``extractfile`` as ``IO[bytes] | None``; we need BinaryIO.
        raw = cast(BinaryIO | None, extracted)
        if raw is None:
            # Only FILE members reach here (the base follows links/skips non-data members),
            # so a None stream means a zero-length or special entry; present an empty stream.
            raw = BytesIO(b"")
        stream: BinaryIO = ensure_binaryio(raw)
        if self._handle_lock is not None:
            stream = LockedStream(stream, self._handle_lock)
        return self._wrap_member_stream(stream, member.name, size=member.size)

    def _get_archive_info(self) -> ArchiveInfo:
        stream_cap = self._source_stream_capability()
        if self._compressed:
            cost = CostReceipt(
                listing_cost=ListingCost.REQUIRES_DECOMPRESSION,
                access_cost=AccessCost.SOLID,  # one compression stream over all members
                stream_capability=stream_cap,
                solid_block_count=1,
            )
        else:
            cost = CostReceipt(
                listing_cost=ListingCost.REQUIRES_SCANNING,  # walk 512-byte headers, no index
                access_cost=AccessCost.DIRECT,  # each member is at a known, independent offset
                stream_capability=stream_cap,
                solid_block_count=None,
            )
        return ArchiveInfo(
            format=self._format,
            format_version=None,
            is_solid=self._compressed,
            member_count=None,  # no central directory: a count requires a full scan
            comment=None,
            is_encrypted=False,
            is_multivolume=False,
            cost=cost,
        )

    def _close_archive(self) -> None:
        with self._handle_guard():
            self._tar.close()
            # tarfile never closes an external fileobj, so close the decompression stream we
            # opened ourselves (which in turn closes a path source it owns). A plain-tar path
            # is opened by tarfile via name= and closed by tar.close() above.
            if self._owned_stream is not None:
                self._owned_stream.close()
                self._owned_stream = None


class TarReadBackend(ReadBackend):
    """Backend factory for TAR archives (plain and compressed)."""

    FORMATS: tuple[ArchiveFormat, ...] = _TAR_FORMATS
    EXTENSIONS: Mapping[str, ArchiveFormat] = _TAR_EXTENSIONS
    # Plain tar is recognized by the POSIX/GNU "ustar" magic at offset 257. Compressed tars
    # carry only their outer codec's magic; detection's inner-TAR probe (see internal/
    # detection.py) decompresses a prefix and finds this same signature to report TAR_GZ etc.
    MAGIC: tuple[MagicSignature, ...] = (
        MagicSignature(257, b"ustar", ArchiveFormat.TAR),
    )
    # TAR is walkable front-to-back, so streaming=True works on a non-seekable source
    # (random access always needs a seekable one — that side is format-independent).
    SUPPORTS_STREAMING_NON_SEEKABLE = True

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
    ) -> TarReader:
        # `format` carries the concrete (TAR, <stream>) variant the detector/caller resolved;
        # the backend uses its stream to pick the codec to decompress with.
        return TarReader(
            source,
            format,
            streaming,
            passwords,
            encoding,
            archive_name,
            config,
            collector=collector,
            member_streams=member_streams,
            open_site=open_site,
        )


register_reader(TarReadBackend)
