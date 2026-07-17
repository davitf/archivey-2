#!/usr/bin/env python3
"""OPTIONAL local end-to-end check for F5 (RAR3 >4 GiB packed member).

The F5 fix (skip the full 64-bit HIGH_PACK_SIZE, not just the low 32 bits) only
matters when a member's *packed* size exceeds 4 GiB — which means the .rar file
itself is >4 GiB. That cannot be a committed fixture, and the committed coverage is
the synthetic ``test_rar3_large_packed_member_skips_full_64bit_size`` unit test.

This script is for maintainers who want to confirm the fix end-to-end against real
RARLAB ``rar`` + ``unrar`` on an actual >4 GiB archive. It:
  1. writes a >4 GiB STORED (``-m0``) member (zeros; store ignores content, so packed
     == size > 4 GiB, which sets FILE_LARGE + HIGH_PACK_SIZE), plus a small canary;
  2. builds a nonsolid RAR4 archive of them;
  3. opens it with archivey and asserts BOTH members list (the walk skipped the full
     >4 GiB region to find the canary) and the canary reads correctly.

It needs the RARLAB ``rar`` writer, ~9 GiB of free scratch disk, and a few minutes.
It does NOT copy anything into tests/fixtures/ — do not commit the archive.

    python review/next/01-rar-reader-findings/make_large_packed_fixture.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_BIG = "big.bin"
_CANARY = "canary.txt"
_CANARY_BYTES = b"CANARY-AFTER-4GB\n" * 64
# Just over 4 GiB so the packed size needs HIGH_PACK_SIZE (the low 32 bits alone
# would under-seek by exactly 4 GiB).
_BIG_SIZE = (4 << 30) + 4096


def _find(tool: str) -> str:
    path = shutil.which(tool)
    if path is None:
        sys.exit(f"error: {tool!r} not on PATH (need the RARLAB rar writer + unrar).")
    return path


def _write_big(path: Path) -> None:
    chunk = b"\0" * (8 << 20)
    written = 0
    with path.open("wb") as fh:
        while written < _BIG_SIZE:
            n = min(len(chunk), _BIG_SIZE - written)
            fh.write(chunk[:n])
            written += n


def main() -> None:
    rar = _find("rar")
    _find("unrar")
    try:
        from archivey import open_archive
    except Exception as exc:  # noqa: BLE001
        sys.exit(f"error: cannot import archivey ({exc}); run via `uv run`.")

    with tempfile.TemporaryDirectory(prefix="rar-large-") as tmp:
        work = Path(tmp)
        print(f"writing {_BIG_SIZE / (1 << 30):.2f} GiB stored member …")
        _write_big(work / _BIG)
        (work / _CANARY).write_bytes(_CANARY_BYTES)

        out = work / "large_packed__rar4.rar"
        # -m0 store (packed == size > 4 GiB), -s- nonsolid, -ep bare names, quiet.
        print("building archive with rar -m0 …")
        proc = subprocess.run(
            [rar, "a", "-ma4", "-m0", "-s-", "-ep", "-o+", "-idq", str(out),
             f"./{_BIG}", f"./{_CANARY}"],
            cwd=work,
            capture_output=True,
            check=False,
        )
        if proc.returncode != 0 or not out.exists():
            sys.exit(
                f"error: rar failed (exit {proc.returncode}):\n"
                f"{proc.stdout.decode(errors='replace')}\n"
                f"{proc.stderr.decode(errors='replace')}"
            )
        print(f"built {out.name} ({out.stat().st_size / (1 << 30):.2f} GiB)")

        with open_archive(out) as arc:
            members = {m.name: m for m in arc.members()}
            names = sorted(members)
            assert names == [_BIG, _CANARY], (
                f"F5 regression: expected both members, got {names} — the parser "
                "under-seeked the >4 GiB packed region and lost the trailing member."
            )
            assert members[_BIG].size == _BIG_SIZE, members[_BIG].size
            assert arc.read(members[_CANARY]) == _CANARY_BYTES
        print("OK: both members listed and the post-4GiB canary read correctly (F5).")


if __name__ == "__main__":
    main()
