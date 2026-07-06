"""TAR backend on the v2 ABC, backed by the stdlib ``tarfile`` module.

Random-access reading (``streaming=False``) scans 512-byte headers on a seekable source
(decompressing first for a compressed tar) and opens any member on demand. Forward-only
reading (``streaming=True``) walks the archive in one progressive pass — including on a
non-seekable source for plain and compressed tars — via ``_iter_with_data()`` /
``stream_members()``. End-of-archive truncation is checked after a full scan or streaming
pass when ``strict_eof`` is configured on open.
"""

from __future__ import annotations

import stat
import tarfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import BinaryIO, Iterator, Mapping, cast

from archivey.cost import (
    AccessCost,
    CostReceipt,
    ListingCost,
    StreamCapability,
)
from archivey.exceptions import (
    ArchiveyError,
    CorruptionError,
    TruncatedError,
)
from archivey.internal.base_reader import BaseArchiveReader, ReadBackend
from archivey.internal.config import StreamConfig
from archivey.internal.logs import backends as backends_logger
from archivey.internal.naming import normalize_member_name
from archivey.internal.registry import register_reader
from archivey.internal.streams.codecs import (
    SINGLE_FILE_CODECS,
    codec_for_stream_format,
    open_codec_stream,
)
from archivey.internal.streams.streamtools import (
    ensure_binaryio,
    ensure_bufferedio,
    is_seekable,
)
from archivey.types import (
    ArchiveFormat,
    ArchiveInfo,
    ArchiveMember,
    CompressionAlgorithm,
    CompressionMethod,
    ContainerFormat,
    MagicSignature,
    MemberType,
    StreamFormat,
)

# Every compressed-tar combination the codec layer can decode: TAR composed with each
# standalone stream codec (gz/bz2/xz/zst/lz4/lzip/zlib/brotli/unix-compress). The common
# ones have named ArchiveFormat constants; the rest are equal-by-value on-demand instances.
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
        password: bytes | None,
        encoding: str | None,
        archive_name: str | None,
        strict_eof: bool = False,
    ) -> None:
        # password rejection is central: open_archive checks ReadBackend.SUPPORTS_PASSWORD.
        super().__init__(format, streaming, archive_name)
        self._encoding = encoding
        self._strict_eof = strict_eof
        self._source = source
        self._compressed = format.stream != StreamFormat.UNCOMPRESSED
        # A decompression stream we open and therefore must close (compressed tars only);
        # for a plain tar from a path, tarfile owns and closes the file handle itself.
        self._owned_stream: BinaryIO | None = None

        try:
            self._tar = self._open_tarfile(source, format, streaming)
        except tarfile.TarError as exc:
            # Only tarfile's own (format) errors are translated; a genuine OSError from the
            # underlying handle propagates unchanged (see error-handling: "Genuine runtime
            # and I/O errors are not reclassified").
            raise self._translate_open_error(exc) from exc

    def _open_tarfile(
        self, source: Path | BinaryIO, format: ArchiveFormat, streaming: bool
    ) -> tarfile.TarFile:
        if self._compressed:
            codec = codec_for_stream_format(format.stream)
            # A non-seekable stream source is wrapped so the live decompression-ratio guard
            # can see compressed bytes consumed; a path / seekable stream (cheap size known)
            # is returned unchanged and uses the static ratio.
            counted = self._wrap_compressed_input(source)
            codec_source: str | BinaryIO = (
                str(counted) if isinstance(counted, Path) else counted
            )
            stream = open_codec_stream(
                codec,
                codec_source,
                config=StreamConfig(streaming=streaming),
                stamp=lambda exc: self._stamp_error_context(exc),
            )
            # tarfile can mis-handle a short read() (fewer bytes than requested) from a
            # decompressor; a BufferedReader in front guarantees full-sized reads.
            self._owned_stream = cast("BinaryIO", ensure_bufferedio(stream))
            return self._tarfile_open(fileobj=self._owned_stream, streaming=streaming)
        if isinstance(source, Path):
            return self._tarfile_open(name=str(source), streaming=streaming)
        return self._tarfile_open(fileobj=source, streaming=streaming)

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
        try:
            members = self._tar.getmembers()  # forces the full header scan
        except tarfile.TarError as exc:
            # A genuine OSError from the source is not caught here, so it propagates unchanged.
            translated = self._translate_exception(exc)
            if translated is not None:
                self._stamp_error_context(translated)
                raise translated from exc
            raise
        for info in members:
            yield self._to_member(info)
        self._verify_tar_eof()

    def _iter_members_progressive(self) -> Iterator[ArchiveMember]:
        """Forward-only member walk — never calls ``getmembers()``.

        Yields bare members; the base's shared progressive pass stamps ids and resolves
        backward links.
        """
        try:
            for info in self._tar:
                yield self._to_member(info)
        except tarfile.TarError as exc:
            translated = self._translate_exception(exc)
            if translated is not None:
                self._stamp_error_context(translated)
                raise translated from exc
            raise
        self._verify_tar_eof()

    def _iter_with_data(self) -> Iterator[tuple[ArchiveMember, BinaryIO | None]]:
        if not self._streaming:
            yield from super()._iter_with_data()
            return
        # Pull from the shared instance-held progressive pass so __iter__,
        # stream_members, and scan_members share one cursor and finalization.
        try:
            for member in self._begin_forward_pass():
                if member.is_file:
                    info = cast("tarfile.TarInfo", member._raw)
                    raw = self._tar.extractfile(info)
                    if raw is None:
                        raw = BytesIO(b"")
                    yield member, self._wrap_member_stream(
                        ensure_binaryio(raw), member.name, size=member.size
                    )
                else:
                    yield member, None
        except tarfile.TarError as exc:
            translated = self._translate_exception(exc)
            if translated is not None:
                self._stamp_error_context(translated)
                raise translated from exc
            raise

    def _verify_tar_eof(self) -> None:
        """Verify the two-block null end-of-archive marker.

        The POSIX trailer is two null-filled 512-byte blocks, but ``tarfile`` has
        already consumed the *first* one by the time we get here — stopping on a null
        block (``EOFHeaderError``) is exactly how it detects the end of the archive,
        and with the default ``ignore_zeros=False`` it stops after that single block.
        So ``fileobj`` is positioned just past the first marker block, and we only need
        to confirm the *second* one follows. (Reading two blocks here would demand a
        third block of trailing zeros and wrongly flag a valid archive whose trailer is
        the minimal two blocks with no record padding — e.g. ``tar -b1``.)

        A short or non-zero read means the marker is missing or truncated: a truncation
        right after the last member leaves no first block for ``tarfile`` to consume,
        so ``fileobj`` is at EOF and this read comes up empty.
        """
        fileobj = self._tar.fileobj
        if fileobj is None:
            return
        chunk = fileobj.read(512)
        if len(chunk) == 512 and chunk == b"\x00" * 512:
            return
        msg = (
            "TAR archive may be truncated: missing or invalid "
            "end-of-archive marker block(s)"
        )
        if self._strict_eof:
            err = TruncatedError(msg)
            self._stamp_error_context(err)
            raise err
        backends_logger.warning(msg)

    def _source_stream_capability(self) -> StreamCapability:
        if isinstance(self._source, Path):
            return StreamCapability.SEEKABLE
        if is_seekable(self._source):
            return StreamCapability.SEEKABLE
        return StreamCapability.FORWARD_ONLY

    def _to_member(self, info: tarfile.TarInfo) -> ArchiveMember:
        member_type = _member_type(info)
        # TAR is a POSIX format: a backslash is a legal filename character, not a separator.
        name = normalize_member_name(info.name, member_type, backslash_is_separator=False)
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
        try:
            modified = datetime.fromtimestamp(info.mtime, tz=timezone.utc)
        except (ValueError, OverflowError, OSError):
            backends_logger.warning(
                "Invalid TAR mtime for %r: %r", info.name, info.mtime
            )
            modified = None

        compression = (
            (CompressionMethod(algo=CompressionAlgorithm.STORED),)
            if member_type in (MemberType.FILE, MemberType.HARDLINK)
            else ()
        )

        extra: dict[str, object] = {"tar.type": info.type}
        if info.pax_headers:
            extra["tar.pax_headers"] = dict(info.pax_headers)
        if info.isdev():
            extra["tar.devmajor"] = info.devmajor
            extra["tar.devminor"] = info.devminor

        return ArchiveMember(
            type=member_type,
            name=name,
            raw_name=raw_name,
            size=info.size if member_type == MemberType.FILE else None,
            compressed_size=None,  # tar stores members uncompressed; no per-member figure
            modified=modified,
            accessed=_pax_time(info, "atime"),
            created=_pax_time(info, "ctime"),
            mode=stat.S_IMODE(info.mode),
            uid=info.uid,
            gid=info.gid,
            uname=info.uname or None,
            gname=info.gname or None,
            link_target=link_target,
            compression=compression,
            is_encrypted=False,  # TAR has no encryption
            is_sparse=info.type == tarfile.GNUTYPE_SPARSE,
            extra=extra,
            _raw=info,  # carry the TarInfo so _open_member needs no name/id lookup table
        )

    def _open_member(self, member: ArchiveMember) -> BinaryIO:
        info = member._raw
        assert isinstance(info, tarfile.TarInfo), "TAR member is missing its TarInfo handle"
        try:
            raw = self._tar.extractfile(info)
        except tarfile.TarError as exc:
            # A genuine OSError from the source is not caught here, so it propagates unchanged.
            translated = self._translate_exception(exc)
            if translated is not None:
                self._stamp_error_context(translated, member.name)
                raise translated from exc
            raise
        if raw is None:
            # Only FILE members reach here (the base follows links/skips non-data members),
            # so a None stream means a zero-length or special entry; present an empty stream.
            raw = BytesIO(b"")
        return self._wrap_member_stream(ensure_binaryio(raw), member.name, size=member.size)

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
        self._tar.close()
        # tarfile never closes an external fileobj, so close the decompression stream we
        # opened ourselves (which in turn closes a path source it owns). A plain-tar path is
        # opened by tarfile via name= and closed by tar.close() above.
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
    # Random-access open needs a seekable source; forward-only streaming is allowed on
    # non-seekable sources when streaming=True (see SUPPORTS_STREAMING_NON_SEEKABLE).
    REQUIRES_SEEK = True
    SUPPORTS_STREAMING_NON_SEEKABLE = True

    def open_read(
        self,
        source: Path | BinaryIO,
        format: ArchiveFormat,
        streaming: bool,
        password: bytes | None,
        encoding: str | None,
        archive_name: str | None,
        strict_eof: bool = False,
    ) -> TarReader:
        # `format` carries the concrete (TAR, <stream>) variant the detector/caller resolved;
        # the backend uses its stream to pick the codec to decompress with.
        return TarReader(
            source, format, streaming, password, encoding, archive_name, strict_eof
        )


register_reader(TarReadBackend)
