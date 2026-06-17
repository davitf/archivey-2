import io
import logging

import pytest

from archivey.config import ArchiveyConfig
from archivey.core import open_archive
from archivey.internal.utils import ensure_not_none
from archivey.types import ArchiveFormat
from tests.archivey.sample_archives import (
    BASIC_ARCHIVES,
    LARGE_ARCHIVES,
    SINGLE_FILE_ARCHIVES,
    SampleArchive,
    filter_archives,
)

logger = logging.getLogger(__name__)


class SizeLimitedReader(io.RawIOBase):
    def __init__(self, data: bytes, max_bytes: int = 1):
        self._stream = io.BytesIO(data)
        self._max_bytes = max_bytes

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        return self._stream.seek(offset, whence)

    def tell(self) -> int:
        return self._stream.tell()

    def seekable(self) -> bool:
        return True

    def readable(self) -> bool:
        return True

    def readinto(self, b: bytearray | memoryview) -> int:  # type: ignore[override]
        data = ensure_not_none(self._stream.read(min(len(b), self._max_bytes)))
        n = len(data)
        b[:n] = data
        return n

    def read(self, n: int = -1, /) -> bytes:
        return ensure_not_none(self._stream.read(min(n, self._max_bytes)))

    def close(self) -> None:
        self._stream.close()
        super().close()


@pytest.mark.sample_archives(
    archives=filter_archives(
        BASIC_ARCHIVES + SINGLE_FILE_ARCHIVES + LARGE_ARCHIVES,
        custom_filter=lambda a: a.creation_info.format not in (ArchiveFormat.FOLDER,),
    ),
    configs=["default", "altlibs"],
)
@pytest.mark.parametrize("streaming_only", [False, True], ids=["random", "stream"])
def test_open_archive_small_reads(
    sample_archive: SampleArchive,
    sample_archive_path: str,
    archivey_config: ArchiveyConfig | None,
    streaming_only: bool,
):
    config = archivey_config or ArchiveyConfig()

    with open(sample_archive_path, "rb") as f:
        data = f.read()

    max_bytes = 250 if "large" in sample_archive_path else 1
    stream = SizeLimitedReader(data, max_bytes=max_bytes)

    with open_archive(stream, streaming_only=streaming_only, config=config) as archive:
        has_member = False
        for member, member_stream in archive.iter_members_with_streams():
            has_member = True
            if member_stream is not None:
                member_stream.read()
        assert has_member
