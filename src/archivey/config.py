from __future__ import annotations

import contextvars
import sys
from contextlib import contextmanager
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Iterator, Literal, TypeAlias, TypedDict

from .types import ExtractionFilter as ExtractionFilter
from .types import FilterFunc

if TYPE_CHECKING or sys.version_info >= (3, 11):
    from enum import StrEnum
    from typing import Unpack
else:
    from backports.strenum import StrEnum
    from typing_extensions import Unpack


class OverwriteMode(StrEnum):
    OVERWRITE = "overwrite"
    SKIP = "skip"
    ERROR = "error"


@dataclass
class ArchiveyConfig:
    """Configuration for :func:`archivey.open_archive`."""

    use_rapidgzip: bool = False
    "Alternative library that can be used instead of the builtin gzip module to read gzip streams. Provides multithreaded decompression and random access support (i.e. jumping to arbitrary positions in the stream without re-decompressing the entire stream), which is particularly useful for accessing random members in compressed tar files."

    use_indexed_bzip2: bool = False
    "Alternative library that can be used instead of the builtin bzip2 module to read bzip2 streams. Provides multithreaded decompression and random access support."

    use_zstandard: bool = False
    "An alternative to pyzstd. Not as good at error reporting."

    use_rar_stream: bool = False
    "If set, use an alternative approach instead of calling rarfile when iterating over RAR archive members. This supports decompressing multiple members in a solid archive by going through the archive only once, instead of once per member."

    use_python_xz: bool = False
    "Use python-xz library for XZ streams instead of the default XzDecompressorStream (stdlib only). Raises PackageNotInstalledError if enabled but python-xz is not installed."

    use_single_file_stored_metadata: bool = False
    "If set, data stored in compressed stream headers is set in the ArchiveMember object for single-file compressed archives, instead of basing it only on the file itself. (filename and modification time for gzip archives only)"

    tar_check_integrity: bool = True
    "If a tar archive is corrupted in a metadata section, tarfile simply stops reading further and acts as if the file has ended. If set, we perform a check that the tar archive has actually been read fully, and raise an error if it's actually corrupted."

    overwrite_mode: OverwriteMode = OverwriteMode.ERROR
    "What to do with existing files when extracting. OVERWRITE: overwrite existing files. SKIP: skip existing files. ERROR: raise an error if a file already exists, and stop extracting."

    extraction_filter: ExtractionFilter | FilterFunc = ExtractionFilter.DATA
    "A filter function that can be used to filter members when iterating over an archive. It can be a function that takes an ArchiveMember and returns a possibly-modified ArchiveMember object, or None to skip the member."


# Allow both enum and string literals for StrEnum fields
OverwriteModeLiteral: TypeAlias = Literal["overwrite", "skip", "error"]
ExtractionFilterLiteral: TypeAlias = Literal["data", "tar", "fully_trusted"]


# TypedDict for config field overrides - allows type checking of kwargs
class ConfigOverrides(TypedDict, total=False):
    use_rapidgzip: bool | None
    use_indexed_bzip2: bool | None
    use_zstandard: bool | None
    use_python_xz: bool | None
    use_rar_stream: bool | None
    use_single_file_stored_metadata: bool | None
    tar_check_integrity: bool | None
    overwrite_mode: OverwriteMode | OverwriteModeLiteral | None
    extraction_filter: ExtractionFilter | FilterFunc | ExtractionFilterLiteral | None


def _convert_str_enum_literals(overrides: Any) -> dict[str, Any]:
    """Convert string literals to their corresponding StrEnum values for ArchiveyConfig fields."""
    # Map field names to their corresponding enum classes
    enum_fields = {
        "overwrite_mode": OverwriteMode,
        "extraction_filter": ExtractionFilter,
    }

    converted = {}
    for k, v in overrides.items():
        if v is None:
            continue
        if k in enum_fields and isinstance(v, str):
            try:
                v = enum_fields[k](v)
            except ValueError:
                raise ValueError(f"Invalid {k} literal: {v!r}")
        converted[k] = v
    return converted


_default_config_var: contextvars.ContextVar[ArchiveyConfig] = contextvars.ContextVar(
    "archivey_default_config", default=ArchiveyConfig()
)


def get_archivey_config() -> ArchiveyConfig:
    """Return the current default configuration."""
    return _default_config_var.get()


def set_archivey_config(config: ArchiveyConfig) -> None:
    """Set the default configuration for :func:`open_archive` and :func:`open_compressed_stream`."""
    _default_config_var.set(config)


def set_archivey_config_fields(
    **overrides: Unpack[ConfigOverrides],
) -> None:
    """Set fields in the default configuration for :func:`open_archive` and :func:`open_compressed_stream`."""
    config = get_archivey_config()
    updates = _convert_str_enum_literals(overrides)
    config = replace(config, **updates)
    set_archivey_config(config)


@contextmanager
def archivey_config(
    config: ArchiveyConfig | None = None,
    **overrides: Unpack[ConfigOverrides],
) -> Iterator[ArchiveyConfig]:
    """Temporarily use ``config`` and/or override fields as the default configuration for :func:`open_archive` and :func:`open_compressed_stream`.

    Example:
    ```python
    with archivey_config(use_rapidgzip=True):
        archive1 = open_archive("path/to/archive.zip")
        archive2 = open_archive("path/to/archive.zip")
        ...
    ```
    """
    if config is None:
        config = get_archivey_config()

    updates = _convert_str_enum_literals(overrides)
    if updates:
        config = replace(config, **updates)

    token = _default_config_var.set(config)
    try:
        yield config
    finally:
        _default_config_var.reset(token)


if __name__ == "__main__":
    with archivey_config(
        use_rapidgzip=True, extraction_filter="data", overwrite_mode="skip"
    ):
        print(get_archivey_config())
