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
from pathlib import Path
from typing import BinaryIO, Iterator

from archivey.config import ArchiveyConfig
from archivey.cost import (
    AccessCost,
    CostReceipt,
    ListingCost,
    StreamCapability,
)
from archivey.exceptions import ArchiveyError, StreamNotSeekableError
from archivey.internal.base_reader import BaseArchiveReader, ReadBackend
from archivey.internal.config import stream_config_from_archivey
from archivey.internal.password import _PasswordCandidates
from archivey.internal.registry import register_reader
from archivey.internal.streams.codecs import (
    SINGLE_FILE_CODECS,
    MetadataContext,
    open_codec_stream,
    resolve_codec,
    stream_codec_for_format,
)
from archivey.internal.streams.decompressor_stream import DecompressorStream
from archivey.internal.streams.streamtools import (
    SharedSource,
    is_seekable,
    is_stream,
    read_exact,
)
from archivey.types import (
    ArchiveFormat,
    ArchiveInfo,
    ArchiveMember,
    MemberType,
)

# Lowercased standalone-compression extensions, for the "strip vs append .uncompressed" rule
# in member-name inference. Sourced from the codec objects. (The combined `tar.gz`/`.tgz`
# names are a format-detection concern, not single-file naming.)
_COMPRESSION_EXTS: frozenset[str] = frozenset(
    ext.lower() for c in SINGLE_FILE_CODECS for ext in c.extensions
)


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
        passwords: _PasswordCandidates | None,
        encoding: str | None,
        archive_name: str | None,
        config: ArchiveyConfig,
    ) -> None:
        # password rejection is central: open_archive checks ReadBackend.SUPPORTS_PASSWORD.
        super().__init__(format, streaming, archive_name, config)
        self._source = source
        self._stream_codec = stream_codec_for_format(format.stream)
        self._codec = self._stream_codec.codec
        self._seekable = not is_stream(source) or is_seekable(source)

        # A non-seekable source cannot be randomly accessed, so engaging a random-access
        # accelerator (rapidgzip) is pointless — and would in fact fail at *open*: rapidgzip
        # needs either a seekable stream or a real OS fileno, and archivey wraps a non-seekable
        # source in a PeekableStream that has neither (so it raises StreamNotSeekableError).
        # Keep the codec sequential for such a source regardless of the archive's streaming flag.
        self._codec_config = stream_config_from_archivey(
            self._config,
            streaming=self._streaming or not self._seekable,
        )

        # The compressed-source header, read at most once and cached (only the gzip metadata
        # hook needs it; codecs without header metadata never trigger a read). See _peek_header.
        self._header_cache: bytes | None = None
        self._member = self._build_member(archive_name)

        # Concurrent/re-entrant member open (no ``_first_stream`` scratch):
        # - Path source: each open hands the path to the codec (independent FD; keeps
        #   path-only accelerator features such as the rapidgzip ISIZE truncation
        #   backstop). Concurrent opens are naturally isolated — same shape as ZIP path.
        # - Seekable stream: SharedSource.view(0) + a fresh codec per open, so interleaved
        #   opens never clobber the single shared handle position.
        # - Non-seekable: one forward pass; a second open fails loudly once consumed.
        self._shared: SharedSource | None = None
        self._pending_stream: BinaryIO | None = None
        if self._seekable and is_stream(source):
            self._shared = SharedSource(source)
        if self._seekable:
            # Eagerly open+close a codec stream so format/seekability errors surface at
            # archive-open time rather than on a later read. Not cached — every
            # _open_member builds a fresh codec stream.
            probe = self._open_codec_stream()
            probe.close()
        else:
            self._pending_stream = self._open_codec_stream()

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
        # Per-codec metadata extraction lives on the codec object (gzip FNAME/mtime, xz/lzip
        # decompressed size); the reader stays codec-agnostic and just supplies the
        # source-reading hooks the extractor may need (the base method is a no-op).
        self._stream_codec.extract_metadata(self._metadata_context(), member)
        return member

    # --- metadata helpers ----------------------------------------------------------------

    def _metadata_context(self) -> MetadataContext:
        return MetadataContext(
            peek_header=self._peek_header,
            probe_decompressed_size=self._probe_decompressed_size,
        )

    def _peek_header(self, length: int) -> bytes:
        """The first ``length`` bytes of the compressed source, read once and cached.

        The first call (or one needing more than is cached) reads the source a single time;
        later calls serve from the cache without re-opening or re-seeking. For a non-seekable
        source this reuses the prefix detection already buffered in the ``PeekableStream``; for
        a path it opens a fresh handle; for a seekable stream it reads and rewinds once.
        """
        if self._header_cache is None or len(self._header_cache) < length:
            self._header_cache = self._read_source_prefix(length)
        return self._header_cache[:length]

    def _read_source_prefix(self, length: int) -> bytes:
        from archivey.internal.streams.peekable import PeekableStream

        src = self._source
        assert src is not None  # always set in __init__
        if isinstance(src, Path):
            with open(src, "rb") as f:
                return f.read(length)
        if isinstance(src, PeekableStream):
            return src.peek(length)
        if is_seekable(src):
            # open_archive normalizes the origin (a mid-positioned stream arrives wrapped
            # with tell() == 0 at the archive's first byte), so 0 is the archive start.
            pos = src.tell()
            src.seek(0)
            data = read_exact(src, length)
            src.seek(pos)
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
        """Open a fresh decompression stream over the source.

        A seekable stream source goes through a whole-source ``SharedSource`` view so
        concurrent / re-entrant opens never clobber the shared handle's position. A path
        source is passed through as a path (the codec opens an independent handle — the
        same concurrent-open shape as ZIP path-source). A non-seekable source is read
        once, forward-only.
        """
        if self._shared is not None:
            # Whole-source view + fresh codec per open (no per-member byte range for a
            # single-file archive). The view is non-owning; the SharedSource outlives it.
            view = self._shared.view(0)
            counted = self._wrap_compressed_input(view)
            assert not isinstance(counted, Path)  # view is always a stream
            return open_codec_stream(
                self._codec,
                counted,
                config=self._codec_config,
                stamp=lambda exc: self._stamp_error_context(exc, self._member.name),
            )

        src = self._source
        assert src is not None  # always set in __init__
        # Count compressed bytes pulled from a non-seekable stream so the live ratio guard
        # has a denominator (a path / seekable stream keeps its cheap static size).
        counted = self._wrap_compressed_input(src)
        codec_source = str(counted) if isinstance(counted, Path) else counted
        return open_codec_stream(
            self._codec,
            codec_source,
            config=self._codec_config,
            stamp=lambda exc: self._stamp_error_context(exc, self._member.name),
        )

    def _open_member(self, member: ArchiveMember) -> BinaryIO:
        if self._seekable:
            # Reentrant: every open builds a fresh codec (path → independent FD; stream →
            # SharedSource view). No per-open scratch on self.
            return self._open_codec_stream()

        # Non-seekable: serve the one-shot stream opened at init; a second open fails loudly.
        if self._pending_stream is not None:
            stream = self._pending_stream
            self._pending_stream = None
            return stream
        err = StreamNotSeekableError(
            "Cannot open this member again: the source is non-seekable and its "
            "single decompression pass has already been consumed. Buffer the "
            "source to disk or a BytesIO to re-read it.",
        )
        self._stamp_error_context(err, member.name)
        raise err

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
        # Close an unserved non-seekable pending stream. The SharedSource is deliberately
        # NOT closed: it owns nothing (only stream sources are wrapped, and the caller
        # owns those), and marking it closed would poison still-open member streams —
        # which are the caller's to close, keep working after reader close everywhere
        # else (ZIP, path sources), and, over the rapidgzip accelerator, ABORT the
        # process if their source dies underneath them (rapidgzip 0.16 raises C++
        # std::invalid_argument through terminate() when a Python-file callback raises —
        # on read, close, and the GC-time guard alike; see docs/known-issues.md). Reads
        # after the *caller* closes their source surface as a typed error via the
        # ArchiveStream closed-handle mapping (stdlib codec paths).
        if self._pending_stream is not None:
            self._pending_stream.close()
            self._pending_stream = None


class SingleFileBackend(ReadBackend):
    """One backend serving every standalone single-file compressor.

    Only the format list is declared here, derived from the codec objects. The detection
    tables (magic, extensions, content probes) are not duplicated onto this backend — the
    detector reads them straight from ``STREAM_CODECS`` via the registry — so adding a
    standalone codec is a single ``StreamCodec`` subclass (see ``compressed-streams`` /
    ``format-detection``).
    """

    FORMATS: tuple[ArchiveFormat, ...] = tuple(
        c.single_file_format for c in SINGLE_FILE_CODECS if c.single_file_format is not None
    )
    # A compressed stream decodes front-to-back, so streaming=True works on a
    # non-seekable source (except unix-compress, whose codec itself needs seek and
    # rejects it at open). Random access always needs a seekable source.
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
    ) -> SingleFileReader:
        # `format` is the resolved single-file format (from detection or the caller); its
        # stream codec is exactly what to decompress with — no re-inspection needed.
        return SingleFileReader(
            source, format, streaming, passwords, encoding, archive_name, config
        )


register_reader(SingleFileBackend)
