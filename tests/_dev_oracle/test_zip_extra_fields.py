import struct
import zipfile
from datetime import datetime, timezone

from archivey.core import open_archive


def test_zip_extra_field_before_timestamp(tmp_path) -> None:
    path = tmp_path / "extra.zip"
    modtime = int(datetime(2020, 1, 2, 3, 4, 5, tzinfo=timezone.utc).timestamp())
    zi = zipfile.ZipInfo("file.txt", date_time=(2020, 1, 2, 3, 4, 5))
    zi.extra = (
        struct.pack("<HH4s", 0x1234, 4, b"abcd")
        + struct.pack("<HHB", 0x5455, 5, 1)
        + struct.pack("<I", modtime)
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(zi, b"data")
    with open_archive(str(path)) as archive:
        info = archive.get_members()[0]
        assert info.mtime_with_tz == datetime(2020, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
