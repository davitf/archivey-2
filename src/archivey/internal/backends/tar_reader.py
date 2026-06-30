"""TAR backend on the v2 ABC, backed by the stdlib ``tarfile`` module.

This phase implements **random-access reading** of a seekable TAR: ``tarfile`` scans the
512-byte headers (decompressing first for a compressed tar) to build the member index, and
any member is then opened on demand. A plain ``.tar`` reads directly from the (seekable)
source — ``DIRECT`` access, ``REQUIRES_SCANNING`` listing; a compressed tar is read through
the codec layer — ``SOLID`` access, ``REQUIRES_DECOMPRESSION`` listing.

TAR is the **only** container that composes with the stream compressors: the codec is
opened internally (``tar.gz``/``tar.bz2``/``tar.xz``/``tar.zst``/``tar.lz4`` and, because
the codec layer is generic, ``tar.lz``/``tar.zst``/``tar.br``/… too) rather than via
``tarfile``'s native ``r:gz``/``r:bz2`` modes, so every codec archivey knows works
uniformly (see ``format-tar`` and tasks.md §3b).

**Out of scope here (Phase 4):** forward-only ``stream_members()`` over a non-seekable
``tar.gz``, the ``ExtractionCoordinator`` / safe-extraction, and the ``strict_eof``
end-of-archive verification (it needs the public config surface that arrives in Phase 5).
Because only random-access read lands now, a non-seekable source is rejected at open via
``REQUIRES_SEEK``.
"""

from __future__ import annotations

import stat
import tarfile
from datetime import datetime, timezone
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
from archivey.internal.naming import normalize_member_name
from archivey.internal.registry import register_reader
from archivey.internal.streams.codecs import (
    SINGLE_FILE_CODECS,
    codec_for_stream_format,
    open_codec_stream,
)
from archivey.internal.streams.streamtools import ensure_binaryio, ensure_bufferedio
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
    ) -> None:
        super().__init__(format, streaming, archive_name)
        self._encoding = encoding
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
            codec_source: str | BinaryIO = (
                str(source) if isinstance(source, Path) else source
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
            return self._tarfile_open(fileobj=self._owned_stream)
        if isinstance(source, Path):
            return self._tarfile_open(name=str(source))
        return self._tarfile_open(fileobj=source)

    def _tarfile_open(
        self, *, name: str | None = None, fileobj: BinaryIO | None = None
    ) -> tarfile.TarFile:
        # mode="r:" reads an *uncompressed* tar stream — we feed it either the raw file (plain
        # tar) or our own decompressor (compressed tar), never tarfile's native r:gz/r:bz2.
        # errorlevel=1 makes tarfile raise on fatal read errors (so truncation/corruption
        # surfaces rather than being silently skipped); we translate those below. encoding=None
        # is accepted (tarfile then applies its own utf-8 default).
        return tarfile.open(
            name=name,
            fileobj=fileobj,
            mode="r:",
            errorlevel=1,
            encoding=self._encoding,
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

    def _to_member(self, info: tarfile.TarInfo) -> ArchiveMember:
        member_type = _member_type(info)
        name = normalize_member_name(info.name, member_type)
        # Re-encode the decoded name with the archive's own codec to recover the stored bytes
        # (tarfile decodes with surrogateescape, which round-trips losslessly).
        raw_name = info.name.encode(self._tar.encoding, errors="surrogateescape")

        link_target = (
            info.linkname
            if member_type in (MemberType.SYMLINK, MemberType.HARDLINK)
            else None
        )

        # tarfile folds a PAX mtime (sub-second/timezone) into TarInfo.mtime already, so this
        # one field honors both the standard ustar mtime and the PAX override.
        modified = datetime.fromtimestamp(info.mtime, tz=timezone.utc)

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
            from io import BytesIO

            raw = BytesIO(b"")
        return self._wrap_member_stream(ensure_binaryio(raw), member.name)

    def _get_archive_info(self) -> ArchiveInfo:
        if self._compressed:
            cost = CostReceipt(
                listing_cost=ListingCost.REQUIRES_DECOMPRESSION,
                access_cost=AccessCost.SOLID,  # one compression stream over all members
                stream_capability=StreamCapability.SEEKABLE,
                solid_block_count=1,
            )
        else:
            cost = CostReceipt(
                listing_cost=ListingCost.REQUIRES_SCANNING,  # walk 512-byte headers, no index
                access_cost=AccessCost.DIRECT,  # each member is at a known, independent offset
                stream_capability=StreamCapability.SEEKABLE,
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
    # Random-access read needs a seekable source; non-seekable forward-only streaming is
    # Phase 4 (which will relax this for streaming=True).
    REQUIRES_SEEK = True

    def open_read(
        self,
        source: Path | BinaryIO,
        format: ArchiveFormat,
        streaming: bool,
        password: bytes | None,
        encoding: str | None,
        archive_name: str | None,
    ) -> TarReader:
        # `format` carries the concrete (TAR, <stream>) variant the detector/caller resolved;
        # the backend uses its stream to pick the codec to decompress with.
        return TarReader(source, format, streaming, password, encoding, archive_name)


register_reader(TarReadBackend)
