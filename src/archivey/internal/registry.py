"""Backend registry: maps ArchiveFormat to ReadBackend/WriteBackend classes."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from archivey.internal.reader import ReadBackend, WriteBackend
    from archivey.internal.types import ArchiveFormat

from archivey.internal.errors import (
    UnsupportedFormatError,
    UnsupportedOperationError,
)


class BackendRegistry:
    """Central registry mapping formats to their read and write backends."""

    def __init__(self) -> None:
        self._readers: dict[ArchiveFormat, type[ReadBackend]] = {}
        self._writers: dict[ArchiveFormat, type[WriteBackend]] = {}

    def register_reader(self, backend_cls: type[ReadBackend]) -> None:
        for fmt in backend_cls.FORMATS:
            self._readers[fmt] = backend_cls

    def register_writer(self, backend_cls: type[WriteBackend]) -> None:
        for fmt in backend_cls.FORMATS:
            self._writers[fmt] = backend_cls

    def reader_for_format(self, fmt: ArchiveFormat) -> type[ReadBackend]:
        if fmt not in self._readers:
            raise UnsupportedFormatError(
                f"No read backend registered for format {fmt!r}",
                source_format=fmt,
            )
        return self._readers[fmt]

    def writer_for_format(self, fmt: ArchiveFormat) -> type[WriteBackend]:
        if fmt not in self._writers:
            raise UnsupportedOperationError(
                f"No write backend registered for format {fmt!r}",
                source_format=fmt,
            )
        return self._writers[fmt]

    def list_formats(self) -> list[ArchiveFormat]:
        return list(self._readers.keys())

    def list_writable_formats(self) -> list[ArchiveFormat]:
        return list(self._writers.keys())


# Module-level singleton
_registry = BackendRegistry()


def register_reader(backend_cls: type[ReadBackend]) -> None:
    _registry.register_reader(backend_cls)


def register_writer(backend_cls: type[WriteBackend]) -> None:
    _registry.register_writer(backend_cls)


def get_registry() -> BackendRegistry:
    return _registry
