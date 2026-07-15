"""Atheris target callables (typed ArchiveyError → soft return)."""

from __future__ import annotations

import io
import os
from collections.abc import Callable

from archivey import (
    AcceleratorMode,
    ArchiveFormat,
    ArchiveyConfig,
    ArchiveyError,
    detect_format,
    format_availability,
    open_archive,
)
from archivey.exceptions import PackageNotInstalledError
from archivey.internal.backends.rar_parser import parse_rar_archive
from archivey.internal.backends.rar_unrar import find_rarlab_unrar
from archivey.internal.backends.sevenzip_pipeline import parse_sevenzip_archive
from archivey.internal.config import StreamConfig
from archivey.internal.registry import FormatSupport
from archivey.internal.streams.codecs import (
    Codec,
    is_codec_available,
    open_codec_stream,
)
from tests.atheris_fuzz.crc_fixup import (
    fixup_rar_header_crcs,
    fixup_sevenzip_header_crcs,
    fixup_zip_local_and_cd_crc,
)
from tests.atheris_fuzz.seeds import (
    brotli_seeds,
    bzip2_seeds,
    deflate64_seeds,
    detect_format_seeds,
    gzip_seeds,
    iso_seeds,
    lz4_seeds,
    lzip_seeds,
    lzma_alone_seeds,
    rar_seeds,
    sevenzip_seeds,
    tar_seeds,
    unix_compress_seeds,
    xz_seeds,
    zip_seeds,
    zlib_seeds,
    zstd_seeds,
)

_FUZZ_CONFIG = ArchiveyConfig(
    use_rapidgzip=AcceleratorMode.OFF, use_indexed_bzip2=AcceleratorMode.OFF
)

# Seekable indexing on (interesting crash class); accelerators forced off.
_STREAM_CONFIG = StreamConfig(
    streaming=False,
    seekable=True,
    use_rapidgzip=AcceleratorMode.OFF,
    use_indexed_bzip2=AcceleratorMode.OFF,
)

# Cap listing work so a pathological member table cannot burn the whole slice.
_MAX_MEMBERS = 10_000
# Bounded ZIP member reads — exercise codec/AES without full extract.
_MAX_ZIP_READ_MEMBERS = 8
_MAX_ZIP_READ_BYTES = 64 * 1024
_MAX_STREAM_READ_BYTES = 256 * 1024

# Empty + common corpus password for encrypted ZIP seeds.
_ZIP_PASSWORD_CANDIDATES: list[str | bytes] = ["", "password"]


def sevenzip_header_one(data: bytes) -> None:
    try:
        parse_sevenzip_archive(io.BytesIO(data))
    except ArchiveyError:
        return


def sevenzip_open_one(data: bytes) -> None:
    try:
        with open_archive(
            io.BytesIO(data), format=ArchiveFormat.SEVEN_Z, config=_FUZZ_CONFIG
        ) as arc:
            for i, member in enumerate(arc):
                if i >= _MAX_MEMBERS:
                    break
                _ = member.name
    except ArchiveyError:
        return


def detect_format_one(data: bytes) -> None:
    try:
        detect_format(io.BytesIO(data))
    except ArchiveyError:
        return


def zip_open_one(data: bytes) -> None:
    """ZIP open → list a few members → bounded ``open``/``read`` (native codec/AES)."""
    fixed = fixup_zip_local_and_cd_crc(data)
    try:
        with open_archive(
            io.BytesIO(fixed),
            format=ArchiveFormat.ZIP,
            config=_FUZZ_CONFIG,
            password=_ZIP_PASSWORD_CANDIDATES,
        ) as arc:
            reads = 0
            for i, member in enumerate(arc):
                if i >= _MAX_MEMBERS:
                    break
                _ = member.name
                if reads >= _MAX_ZIP_READ_MEMBERS or not member.is_file:
                    continue
                try:
                    with arc.open(member) as stream:
                        _ = stream.read(_MAX_ZIP_READ_BYTES)
                    reads += 1
                except ArchiveyError:
                    # Wrong password / unsupported codec / truncated — keep listing.
                    continue
    except ArchiveyError:
        return


def tar_open_one(data: bytes) -> None:
    try:
        with open_archive(
            io.BytesIO(data), format=ArchiveFormat.TAR, config=_FUZZ_CONFIG
        ) as arc:
            for i, member in enumerate(arc):
                if i >= _MAX_MEMBERS:
                    break
                _ = member.name
    except ArchiveyError:
        return


def iso_open_one(data: bytes) -> None:
    try:
        with open_archive(
            io.BytesIO(data), format=ArchiveFormat.ISO, config=_FUZZ_CONFIG
        ) as arc:
            for i, member in enumerate(arc):
                if i >= _MAX_MEMBERS:
                    break
                _ = member.name
    except ArchiveyError:
        return


def rar_available() -> bool:
    availability = format_availability(ArchiveFormat.RAR)
    return availability.support is not FormatSupport.NONE


def unrar_available() -> bool:
    try:
        find_rarlab_unrar()
    except PackageNotInstalledError:
        return False
    return True


def rar_open_available() -> bool:
    """Open+list RAR target: backend registered and RARLAB ``unrar`` present.

    Header-only fuzz does not need ``unrar``; the open target gates on it so CI
    and local runs without RARLAB unrar skip rather than thrashing open paths
    that are only fully meaningful with the data backend available.
    """
    return rar_available() and unrar_available()


def rar_header_one(data: bytes) -> None:
    try:
        archive = parse_rar_archive(io.BytesIO(data))
        # Bound listing work even if parse succeeded with a huge table (defense
        # in depth; parser also caps at ListingLimits default).
        if len(archive.members) > _MAX_MEMBERS:
            return
    except ArchiveyError:
        return


def rar_open_one(data: bytes) -> None:
    try:
        with open_archive(
            io.BytesIO(data), format=ArchiveFormat.RAR, config=_FUZZ_CONFIG
        ) as arc:
            for i, member in enumerate(arc):
                if i >= _MAX_MEMBERS:
                    break
                _ = member.name
    except ArchiveyError:
        return


def iso_per_input_timeout() -> float:
    raw = os.environ.get("ARCHIVEY_FUZZ_ISO_INPUT_TIMEOUT", "2.0")
    try:
        return max(0.1, float(raw))
    except ValueError:
        return 2.0


def stream_per_input_timeout() -> float:
    """Per-input kill for hang-prone codecs (LZW / xz / bzip2, …)."""
    raw = os.environ.get("ARCHIVEY_FUZZ_STREAM_INPUT_TIMEOUT", "2.0")
    try:
        return max(0.1, float(raw))
    except ValueError:
        return 2.0


def make_codec_one(codec: Codec) -> Callable[[bytes], None]:
    """Build an ``open_codec_stream`` target for ``codec`` (seekable, accelerators off)."""

    def codec_one(data: bytes) -> None:
        try:
            with open_codec_stream(
                codec, io.BytesIO(data), config=_STREAM_CONFIG
            ) as stream:
                _ = stream.read(_MAX_STREAM_READ_BYTES)
                # Hit seek-index / CLEAR paths when the backend exposes them.
                if stream.seekable():
                    try:
                        stream.seek(0)
                        _ = stream.read(min(4096, _MAX_STREAM_READ_BYTES))
                    except (OSError, io.UnsupportedOperation, ArchiveyError):
                        pass
        except ArchiveyError:
            return

    codec_one.__name__ = f"{codec.value}_one"
    codec_one.__qualname__ = f"{codec.value}_one"
    return codec_one


def _codec_available(codec: Codec) -> Callable[[], bool]:
    def _check() -> bool:
        return is_codec_available(codec)

    _check.__name__ = f"{codec.value}_available"
    return _check


TargetSpec = tuple[str, Callable[[bytes], None], Callable[[], list[bytes]], dict]


def iter_target_specs() -> list[dict]:
    """Descriptor dicts consumed by ``__main__`` / the CI runner."""
    stream_timeout = stream_per_input_timeout()
    specs: list[dict] = [
        {
            "name": "sevenzip_header",
            "fn": sevenzip_header_one,
            "seeds": sevenzip_seeds,
            "fixup": fixup_sevenzip_header_crcs,
            "per_input_timeout": None,
        },
        {
            "name": "sevenzip_open",
            "fn": sevenzip_open_one,
            "seeds": sevenzip_seeds,
            "fixup": fixup_sevenzip_header_crcs,
            "per_input_timeout": None,
        },
        {
            "name": "detect_format",
            "fn": detect_format_one,
            "seeds": detect_format_seeds,
            "fixup": None,
            "per_input_timeout": None,
        },
        {
            "name": "zip",
            "fn": zip_open_one,
            "seeds": zip_seeds,
            "fixup": None,  # CRC fixup applied inside zip_open_one
            "per_input_timeout": None,
        },
        {
            "name": "tar",
            "fn": tar_open_one,
            "seeds": tar_seeds,
            "fixup": None,
            "per_input_timeout": None,
        },
        {
            "name": "iso",
            "fn": iso_open_one,
            "seeds": iso_seeds,
            "fixup": None,
            "per_input_timeout": iso_per_input_timeout(),
        },
        {
            "name": "rar_header",
            "fn": rar_header_one,
            "seeds": rar_seeds,
            "fixup": fixup_rar_header_crcs,
            "per_input_timeout": None,
            "skip_unless": rar_available,
        },
        {
            "name": "rar",
            "fn": rar_open_one,
            "seeds": rar_seeds,
            "fixup": fixup_rar_header_crcs,
            "per_input_timeout": None,
            "skip_unless": rar_open_available,
        },
    ]

    # Required standalone stream/codec targets (always registered).
    required_streams: list[tuple[str, Codec, Callable[[], list[bytes]]]] = [
        ("unix_compress", Codec.UNIX_COMPRESS, unix_compress_seeds),
        ("xz", Codec.XZ, xz_seeds),
        ("lzip", Codec.LZIP, lzip_seeds),
        ("gzip", Codec.GZIP, gzip_seeds),
        ("bzip2", Codec.BZIP2, bzip2_seeds),
        ("lzma_alone", Codec.LZMA_ALONE, lzma_alone_seeds),
        ("zlib", Codec.ZLIB, zlib_seeds),
    ]
    for name, codec, seeds_fn in required_streams:
        specs.append(
            {
                "name": name,
                "fn": make_codec_one(codec),
                "seeds": seeds_fn,
                "fixup": None,
                "per_input_timeout": stream_timeout,
            }
        )

    # Optional extras — skip-clean when the backend is absent.
    optional_streams: list[tuple[str, Codec, Callable[[], list[bytes]]]] = [
        ("zstd", Codec.ZSTD, zstd_seeds),
        ("brotli", Codec.BROTLI, brotli_seeds),
        ("lz4", Codec.LZ4, lz4_seeds),
        ("deflate64", Codec.DEFLATE64, deflate64_seeds),
    ]
    for name, codec, seeds_fn in optional_streams:
        specs.append(
            {
                "name": name,
                "fn": make_codec_one(codec),
                "seeds": seeds_fn,
                "fixup": None,
                "per_input_timeout": stream_timeout,
                "skip_unless": _codec_available(codec),
            }
        )

    return specs
