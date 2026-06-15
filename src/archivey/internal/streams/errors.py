"""Error-raising stream and exception translation helpers."""

import io
import logging
from typing import (
    BinaryIO,
    Callable,
    Optional,
    TypeVar,
)

from archivey.exceptions import ArchiveError

logger = logging.getLogger(__name__)


ExceptionTranslatorFn = Callable[[Exception], Optional[ArchiveError]]


class ErrorIOStream(io.RawIOBase, BinaryIO):
    """
    An I/O stream that always raises a predefined exception on any I/O operation.

    This is useful for testing error handling paths or for representing
    unreadable members within an archive without returning None.
    """

    def __init__(self, exc: Exception):
        """Initialize the error stream."""
        self._exc = exc

    def read(self, size: int = -1) -> bytes:
        """Raise the stored exception."""
        raise self._exc

    def write(self, b: bytes) -> int:  # type: ignore[override]
        """Raise the stored exception."""
        raise self._exc

    def readable(self) -> bool:
        return True  # pragma: no cover - trivial

    def writable(self) -> bool:
        return False  # pragma: no cover - trivial

    def seekable(self) -> bool:
        return False  # pragma: no cover - trivial


T = TypeVar("T")


def run_with_exception_translation(
    func: Callable[[], T],
    exception_translator: Callable[[Exception], Optional[ArchiveError]],
    archive_path: str | None = None,
    member_name: str | None = None,
) -> T:
    try:
        return func()
    except ArchiveError as e:
        if archive_path is not None:
            e.archive_path = archive_path
        if member_name is not None:
            e.member_name = member_name
        raise e

    except Exception as e:
        translated = exception_translator(e)
        if translated is not None:
            translated.archive_path = archive_path
            translated.member_name = member_name
            logger.debug(
                "Translated exception: %r -> %r",
                e,
                translated,
            )
            raise translated from e
        raise e
