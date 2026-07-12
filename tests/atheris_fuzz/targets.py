"""Atheris target callables (typed ArchiveyError → soft return)."""

from __future__ import annotations

import io
import os
from collections.abc import Callable
from typing import Any

from archivey import (
    AcceleratorMode,
    ArchiveFormat,
    ArchiveyConfig,
    ArchiveyError,
    detect_format,
    format_availability,
    open_archive,
)
from archivey.internal.backends.rar_parser import parse_rar_archive
from archivey.internal.backends.sevenzip_parser import (
    SevenZipFolder,
    parse_sevenzip_archive,
)
from archivey.internal.backends.sevenzip_reader import decode_folder_to_bytes
from archivey.internal.registry import FormatSupport
from archivey.internal.streams.crypto import SevenZipKeyCache
from tests.atheris_fuzz.crc_fixup import (
    fixup_rar_header_crcs,
    fixup_sevenzip_header_crcs,
    fixup_zip_local_and_cd_crc,
)
from tests.atheris_fuzz.seeds import (
    detect_format_seeds,
    iso_seeds,
    rar_seeds,
    sevenzip_seeds,
    tar_seeds,
    zip_seeds,
)

_FUZZ_CONFIG = ArchiveyConfig(
    use_rapidgzip=AcceleratorMode.OFF, use_indexed_bzip2=AcceleratorMode.OFF
)

# Cap listing work so a pathological member table cannot burn the whole slice.
_MAX_MEMBERS = 10_000


def _decode_folder(
    source: Any,
    folder: SevenZipFolder,
    compressed_size: int,
    uncompressed_size: int,
) -> bytes:
    return decode_folder_to_bytes(
        source,
        folder,
        compressed_size=compressed_size,
        uncompressed_size=uncompressed_size,
        password=None,
        key_cache=SevenZipKeyCache(),
    )


def sevenzip_header_one(data: bytes) -> None:
    try:
        parse_sevenzip_archive(io.BytesIO(data), decode_folder=_decode_folder)
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
    try:
        with open_archive(
            io.BytesIO(data), format=ArchiveFormat.ZIP, config=_FUZZ_CONFIG
        ) as arc:
            for i, member in enumerate(arc):
                if i >= _MAX_MEMBERS:
                    break
                _ = member.name
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


def zip_tar_one(data: bytes) -> None:
    """Shallow ZIP then TAR over the same bytes (wrapper/translation coverage)."""
    fixed = fixup_zip_local_and_cd_crc(data)
    zip_open_one(fixed)
    tar_open_one(data)


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


def rar_header_one(data: bytes) -> None:
    try:
        parse_rar_archive(io.BytesIO(data))
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


TargetSpec = tuple[str, Callable[[bytes], None], Callable[[], list[bytes]], dict]


def iter_target_specs() -> list[dict]:
    """Descriptor dicts consumed by ``__main__`` / the CI runner."""
    return [
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
            "name": "zip_tar",
            "fn": zip_tar_one,
            "seeds": lambda: zip_seeds() + tar_seeds(),
            "fixup": None,  # ZIP fixup applied inside zip_tar_one for the ZIP half
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
            "skip_unless": rar_available,
        },
    ]
