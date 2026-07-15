"""Internal stream-layer view derived from :class:`ArchiveyConfig`."""

from __future__ import annotations

from dataclasses import dataclass

from archivey.config import (
    DEFAULT_ARCHIVEY_CONFIG,
    RAPIDGZIP_AUTO_MIN_COMPRESSED_SIZE,
    AcceleratorMode,
    ArchiveyConfig,
)

__all__ = [
    "AcceleratorMode",
    "DEFAULT_ARCHIVEY_CONFIG",
    "DEFAULT_STREAM_CONFIG",
    "RAPIDGZIP_AUTO_MIN_COMPRESSED_SIZE",
    "ArchiveyConfig",
    "StreamConfig",
    "stream_config_from_archivey",
]


@dataclass(frozen=True)
class StreamConfig:
    """Options that influence how compressed streams are opened.

    ``seekable`` is declared seek demand (``MemberStreams.SEEKABLE``): accelerator
    ``AUTO`` resolution and index construction key off it. ``streaming`` remains the
    archive access mode for backends that still need to know forward-only vs random.
    ``compressed_input_size`` is the known compressed byte length of the source (path
    size, slice length, …), used by ``use_rapidgzip`` AUTO's minimum-size gate; ``None``
    means unknown (AUTO keeps pre-threshold behaviour).
    """

    streaming: bool = False
    seekable: bool = False
    use_rapidgzip: AcceleratorMode = AcceleratorMode.AUTO
    use_indexed_bzip2: AcceleratorMode = AcceleratorMode.AUTO
    compressed_input_size: int | None = None


def stream_config_from_archivey(
    config: ArchiveyConfig,
    *,
    streaming: bool,
    seekable: bool = False,
) -> StreamConfig:
    """Derive the codec-layer view from the public config and declared seek demand."""
    return StreamConfig(
        streaming=streaming,
        seekable=seekable,
        use_rapidgzip=config.use_rapidgzip,
        use_indexed_bzip2=config.use_indexed_bzip2,
    )


DEFAULT_STREAM_CONFIG = stream_config_from_archivey(
    DEFAULT_ARCHIVEY_CONFIG, streaming=False, seekable=False
)
