"""Single-file compressor backend — one multi-format reader for every standalone codec.

A bare ``.gz`` / ``.bz2`` / ``.xz`` / ``.zst`` / ``.lz4`` / ``.lz`` (lzip) / ``.zz`` (zlib)
/ ``.br`` (brotli) / ``.Z`` (unix-compress) stream is presented as a one-member
pseudo-archive: a single ``FILE`` member whose name is inferred from the source filename,
decompressed through the ``compressed-streams`` codec layer. The backend is codec-agnostic;
the only per-format logic lives in small **per-codec metadata hooks** (gzip's stored
filename/mtime, xz/lzip decompressed size), so a new standalone codec becomes readable by
adding the codec + enum + detection entry — no new backend class (see
``format-single-file-compressors``).

ZST and LZ4 are first-class standalone formats here (their codecs already exist from
Phase 2); only their *seekable-decompressor* refinements remain for Phase 8.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Callable, ClassVar, Iterator, Mapping

from archivey.internal.config import StreamConfig
from archivey.internal.cost import (
    AccessCost,
    CostReceipt,
    ListingCost,
    StreamCapability,
)
from archivey.internal.errors import (
    ArchiveyError,
    UnsupportedOperationError,
)
from archivey.internal.reader import BaseArchiveReader, ReadBackend
from archivey.internal.registry import register_reader
from archivey.internal.streams.codecs import (
    codec_for_stream_format,
    open_codec_stream,
    resolve_codec,
)
from archivey.internal.streams.decompressor_stream import DecompressorStream
from archivey.internal.streams.streamtools import is_seekable, is_stream
from archivey.internal.types import (
    ArchiveFormat,
    ArchiveInfo,
    ArchiveMember,
    MagicSignature,
    MemberType,
    StreamFormat,
)

# Standalone-stream extensions recognized for member-name inference + extension detection.
# (The combined `tar.gz`/`.tgz` names are a format-detection concern, not single-file naming.)
_EXTENSIONS: dict[str, ArchiveFormat] = {
    ".gz": ArchiveFormat.GZ,
    ".bz2": ArchiveFormat.BZ2,
    ".xz": ArchiveFormat.XZ,
    ".zst": ArchiveFormat.ZST,
    ".lz4": ArchiveFormat.LZ4,
    ".lz": ArchiveFormat.LZIP,
    ".zz": ArchiveFormat.ZLIB,
    ".br": ArchiveFormat.BROTLI,
    ".Z": ArchiveFormat.Z,
}

# Lowercased recognized compression extensions, for the "strip vs append .uncompressed" rule.
_COMPRESSION_EXTS: frozenset[str] = frozenset(ext.lower() for ext in _EXTENSIONS)

_FORMATS: tuple[ArchiveFormat, ...] = (
    ArchiveFormat.GZ,
    ArchiveFormat.BZ2,
    ArchiveFormat.XZ,
    ArchiveFormat.ZST,
    ArchiveFormat.LZ4,
    ArchiveFormat.LZIP,
    ArchiveFormat.ZLIB,
    ArchiveFormat.BROTLI,
    ArchiveFormat.Z,
)

# How many header bytes to peek for cheap metadata (gzip FNAME/mtime). Long stored names
# beyond this are simply not surfaced.
_HEADER_PEEK = 512


def _infer_member_name(archive_name: str | None) -> str:
    """Infer the single member's name from the source filename (see the spec)."""
    if archive_name is None:
        return "data"
    base = os.path.basename(archive_name)
    root, ext = os.path.splitext(base)
    if ext.lower() in _COMPRESSION_EXTS and root:
        return root
    return base + ".uncompressed"


class SingleFileReader(BaseArchiveReader):
    """Presents one standalone compressed stream as a one-member archive."""

    _SUPPORTS_RANDOM_ACCESS = True
    _MEMBER_LIST_UPFRONT = True

    def __init__(
        self,
        source: Path | BinaryIO,
        format: ArchiveFormat,
        streaming: bool,
        password: bytes | None,
        encoding: str | None,
        archive_name: str | None,
    ) -> None:
        if password is not None:
            raise UnsupportedOperationError(
                "Single-file compressors do not support passwords (they carry no encryption)."
            )
        super().__init__(format, streaming, archive_name)
        self._source = source
        self._codec = codec_for_stream_format(format.stream)
        self._seekable = not is_stream(source) or is_seekable(source)

        # A non-seekable source cannot be randomly accessed, so engaging a random-access
        # accelerator (rapidgzip) is pointless — and would in fact fail at *open*: rapidgzip
        # needs either a seekable stream or a real OS fileno, and archivey wraps a non-seekable
        # source in a PeekableStream that has neither (so it raises StreamNotSeekableError).
        # Keep the codec sequential for such a source regardless of the archive's streaming flag.
        self._codec_config = StreamConfig(streaming=self._streaming or not self._seekable)

        self._member = self._build_member(archive_name)
        # Open the decompression stream eagerly so format/seekability errors (e.g. a
        # non-seekable unix-compress source, which the codec rejects via the translator)
        # surface here at open time rather than on a later read. The opened stream is cached
        # and served by the first _open_member(); subsequent opens build fresh streams.
        self._first_stream: BinaryIO | None = self._open_codec_stream()

    def _build_member(self, archive_name: str | None) -> ArchiveMember:
        compressed_size = (
            os.path.getsize(self._source)
            if isinstance(self._source, Path) and self._source.exists()
            else None
        )
        member = ArchiveMember(
            type=MemberType.FILE,
            name=_infer_member_name(archive_name),
            raw_name=None,
            size=None,  # filled per-codec below where cheaply known
            compressed_size=compressed_size,
            modified=None,
        )
        hook = self._METADATA_HOOKS.get(self._format.stream)
        if hook is not None:
            hook(self, member)
        return member

    # --- per-codec metadata hooks (dispatch table, not an if/elif chain) ------------------

    def _gzip_metadata(self, member: ArchiveMember) -> None:
        """Surface gzip's stored filename (FNAME) and mtime from the fixed/optional header.

        RFC 1952 specifies the FNAME field as ISO-8859-1 (Latin-1), so the decoded value in
        ``extra`` uses that encoding; ``raw_name`` keeps the verbatim stored bytes.
        """
        header = self._peek_header()
        if len(header) < 10 or header[:2] != b"\x1f\x8b":
            return
        flg = header[3]
        mtime = int.from_bytes(header[4:8], "little")
        if mtime != 0:
            member.modified = datetime.fromtimestamp(mtime, tz=timezone.utc)

        pos = 10
        if flg & 0x04:  # FEXTRA: 2-byte length + data
            if pos + 2 > len(header):
                return
            xlen = int.from_bytes(header[pos : pos + 2], "little")
            pos += 2 + xlen
        if flg & 0x08:  # FNAME: null-terminated stored filename (Latin-1 per RFC 1952)
            end = header.find(b"\x00", pos)
            if end != -1:
                name_bytes = header[pos:end]
                member.raw_name = name_bytes
                member.extra["gzip.original_filename"] = name_bytes.decode("latin-1")

    def _sized_stream_metadata(self, member: ArchiveMember) -> None:
        """Fill the decompressed size for formats whose index/trailer records it (xz, lzip)."""
        member.size = self._probe_decompressed_size()

    # StreamFormat -> metadata hook. A codec with no extra metadata registers no hook.
    _METADATA_HOOKS: ClassVar[
        dict[StreamFormat, Callable[["SingleFileReader", ArchiveMember], None]]
    ] = {
        StreamFormat.GZIP: _gzip_metadata,
        StreamFormat.XZ: _sized_stream_metadata,
        StreamFormat.LZIP: _sized_stream_metadata,
    }

    # --- metadata helpers ----------------------------------------------------------------

    def _peek_header(self) -> bytes:
        """The first bytes of the compressed stream, without consuming the source."""
        from archivey.internal.streams.peekable import PeekableStream

        src = self._source
        if isinstance(src, Path):
            with open(src, "rb") as f:
                return f.read(_HEADER_PEEK)
        if isinstance(src, PeekableStream):
            return src.peek(_HEADER_PEEK)
        if is_seekable(src):
            data = src.read(_HEADER_PEEK)
            src.seek(0)
            return data
        return b""

    def _probe_decompressed_size(self) -> int | None:
        """Decompressed size from the stream index/trailer, when cheaply available.

        Only attempted for a path source (a fresh handle the probe fully owns), so it never
        disturbs a caller-provided stream's position or lifetime.
        """
        if not isinstance(self._source, Path):
            return None
        try:
            backend = resolve_codec(self._codec, self._codec_config)
            stream = backend.open(str(self._source))
        except (ArchiveyError, OSError, ValueError):
            return None
        try:
            if isinstance(stream, DecompressorStream):
                return stream.try_get_size()
            return None
        finally:
            stream.close()

    # --- reader hooks --------------------------------------------------------------------

    def _iter_members(self) -> Iterator[ArchiveMember]:
        yield self._member

    def _open_codec_stream(self) -> BinaryIO:
        """Open a fresh decompression stream over the source (rewinding a seekable one)."""
        src = self._source
        if is_stream(src) and is_seekable(src):
            src.seek(0)
        codec_source = str(src) if isinstance(src, Path) else src
        return open_codec_stream(
            self._codec,
            codec_source,
            config=self._codec_config,
            stamp=lambda exc: self._stamp_error_context(exc, self._member.name),
        )

    def _open_member(self, member: ArchiveMember) -> BinaryIO:
        # Serve the stream opened eagerly at init on the first call; build fresh ones after.
        if self._first_stream is not None:
            stream = self._first_stream
            self._first_stream = None
            return stream
        return self._open_codec_stream()

    def _get_archive_info(self) -> ArchiveInfo:
        cost = CostReceipt(
            listing_cost=ListingCost.INDEXED,  # exactly one member, always
            access_cost=AccessCost.DIRECT,  # one member -> no solid-block dependency
            stream_capability=(
                StreamCapability.SEEKABLE if self._seekable else StreamCapability.FORWARD_ONLY
            ),
            solid_block_count=None,
        )
        return ArchiveInfo(
            format=self._format,
            format_version=None,
            is_solid=False,
            member_count=1,
            comment=None,
            is_encrypted=False,
            is_multivolume=False,
            cost=cost,
        )

    def _close_archive(self) -> None:
        # Close the eagerly-opened stream if it was never served; opened member streams are
        # the caller's to close, and the source itself is the caller's.
        if self._first_stream is not None:
            self._first_stream.close()
            self._first_stream = None


class SingleFileBackend(ReadBackend):
    """One backend serving every standalone single-file compressor."""

    FORMATS: tuple[ArchiveFormat, ...] = _FORMATS
    EXTENSIONS: Mapping[str, ArchiveFormat] = _EXTENSIONS
    MAGIC: tuple[MagicSignature, ...] = (
        MagicSignature(0, b"\x1f\x8b", ArchiveFormat.GZ),
        MagicSignature(0, b"BZh", ArchiveFormat.BZ2),
        MagicSignature(0, b"\xfd7zXZ\x00", ArchiveFormat.XZ),
        MagicSignature(0, b"\x28\xb5\x2f\xfd", ArchiveFormat.ZST),
        MagicSignature(0, b"\x04\x22\x4d\x18", ArchiveFormat.LZ4),
        MagicSignature(0, b"LZIP", ArchiveFormat.LZIP),
        MagicSignature(0, b"\x1f\x9d", ArchiveFormat.Z),
        # zlib's 2-byte CMF/FLG header: weak — the detector confirms it with a content probe.
        MagicSignature(0, b"\x78\x01", ArchiveFormat.ZLIB, weak=True),
        MagicSignature(0, b"\x78\x5e", ArchiveFormat.ZLIB, weak=True),
        MagicSignature(0, b"\x78\x9c", ArchiveFormat.ZLIB, weak=True),
        MagicSignature(0, b"\x78\xda", ArchiveFormat.ZLIB, weak=True),
    )
    # Brotli has no signature; the detector confirms it by decoding a bounded prefix.
    CONTENT_PROBE_FORMATS: tuple[ArchiveFormat, ...] = (ArchiveFormat.BROTLI,)
    REQUIRES_SEEK = False  # only unix-compress needs seek; the codec rejects a bad source

    def open_read(
        self,
        source: Path | BinaryIO,
        format: ArchiveFormat,
        streaming: bool,
        password: bytes | None,
        encoding: str | None,
        archive_name: str | None,
    ) -> SingleFileReader:
        # `format` is the resolved single-file format (from detection or the caller); its
        # stream codec is exactly what to decompress with — no re-inspection needed.
        return SingleFileReader(
            source, format, streaming, password, encoding, archive_name
        )


register_reader(SingleFileBackend)
