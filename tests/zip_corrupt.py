"""Helpers for building ZIPs with a flipped central-directory CRC.

Used by extraction / CLI tests that need a structurally openable archive whose
named member fails integrity checks at extract/read time.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path


def zip_with_flipped_cd_crc(
    path: Path,
    entries: dict[str, bytes],
    *,
    corrupt_name: str,
) -> Path:
    """Write ``entries`` to ``path``, flipping the CD CRC32 for ``corrupt_name``."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    data = bytearray(buf.getvalue())
    target = corrupt_name.encode("utf-8")
    pos = 0
    while True:
        i = data.find(b"PK\x01\x02", pos)
        if i < 0:
            raise LookupError(f"central-directory entry not found for {corrupt_name!r}")
        name_len = int.from_bytes(data[i + 28 : i + 30], "little")
        name = bytes(data[i + 46 : i + 46 + name_len])
        if name == target:
            data[i + 16] ^= 0xFF
            break
        pos = i + 4
    path.write_bytes(bytes(data))
    return path
