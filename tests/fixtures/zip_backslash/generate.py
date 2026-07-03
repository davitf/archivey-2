"""Generate ZIP fixtures with backslash-bearing member names for cross-platform tests.

Python's ``zipfile`` rewrites ``ZipInfo.filename`` in a platform-dependent way — on
Windows it replaces ``os.sep`` (``\\``) with ``/`` and truncates at a null byte — at both
write and read time. That makes a backslash-in-a-name scenario impossible to construct
*at runtime* on Windows. Committing statically generated archives (the dev-oracle pattern)
sidesteps that: the stored bytes are identical everywhere, and archivey reads the raw name
from ``orig_filename`` (which zipfile preserves on every OS), so the tests run the same on
Linux, macOS, and Windows.

The two fixtures:

* ``dos_backslash.zip`` — a DOS/Windows-origin entry (``create_system=0``, FAT) named
  ``dir\\sub\\file.txt``. Here ``\\`` is a path separator, so archivey normalizes the name
  to ``dir/sub/file.txt``.
* ``unix_backslash.zip`` — a Unix-origin entry (``create_system=3``) named
  ``weird\\name.txt``. Here ``\\`` is a legal filename character, so archivey keeps it
  literal.

Both are produced deterministically on **any** OS (the member name is assigned to
``ZipInfo.filename`` *after* construction, so ``__init__``'s ``os.sep`` rewrite never runs).
Regenerate with::

    uv run python tests/fixtures/zip_backslash/generate.py
"""

from __future__ import annotations

import zipfile
from pathlib import Path

_DATE = (1980, 1, 1, 0, 0, 0)  # fixed timestamp -> reproducible bytes


def _write(path: Path, arcname: str, create_system: int) -> None:
    info = zipfile.ZipInfo(date_time=_DATE)
    # Assign after construction so the "\" survives on Windows too (ZipInfo.__init__ would
    # otherwise replace os.sep with "/").
    info.filename = arcname
    info.create_system = create_system  # 0 = FAT/DOS, 3 = Unix
    info.compress_type = zipfile.ZIP_STORED
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(info, b"data")


def main() -> None:
    here = Path(__file__).parent
    _write(here / "dos_backslash.zip", "dir\\sub\\file.txt", create_system=0)
    _write(here / "unix_backslash.zip", "weird\\name.txt", create_system=3)


if __name__ == "__main__":
    main()
