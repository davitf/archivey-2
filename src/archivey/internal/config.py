"""Internal stream-layer view derived from :class:`ArchiveyConfig`."""

from __future__ import annotations

from dataclasses import dataclass

from archivey.config import (
    DEFAULT_ARCHIVEY_CONFIG,
    AcceleratorMode,
    ArchiveyConfig,
)

__all__ = [
    "AcceleratorMode",
    "DEFAULT_ARCHIVEY_CONFIG",
    "DEFAULT_STREAM_CONFIG",
    "ArchiveyConfig",
    "StreamConfig",
    "stream_config_from_archivey",
]


@dataclass(frozen=True)
class StreamConfig:
    """Options that influence how compressed streams are opened.

    ``streaming`` mirrors the archive's access mode (``open_archive(streaming=...)``) so
    the accelerator modes can resolve ``AUTO`` against it.
    """

    streaming: bool = False
    use_rapidgzip: AcceleratorMode = AcceleratorMode.AUTO
    use_indexed_bzip2: AcceleratorMode = AcceleratorMode.AUTO


def stream_config_from_archivey(
    config: ArchiveyConfig, *, streaming: bool
) -> StreamConfig:
    """Derive the codec-layer view from the public config and access mode."""
    return StreamConfig(
        streaming=streaming,
        use_rapidgzip=config.use_rapidgzip,
        use_indexed_bzip2=config.use_indexed_bzip2,
    )


DEFAULT_STREAM_CONFIG = stream_config_from_archivey(
    DEFAULT_ARCHIVEY_CONFIG, streaming=False
)
