"""Assert the zero-dependency core: importable and functional with no third-party deps.

Run in the `core-only` CI leg, where archivey is installed with no extras and no dev
group. It proves two things the full pytest suite (which always has the dev libs) cannot:

1. importing/using the core pulls in **no** third-party runtime package, and
2. the spine works end-to-end on stdlib alone (the directory backend).

Exits non-zero with a clear message on any violation.
"""

from __future__ import annotations

import os
import sys
import tempfile

import archivey
from archivey import open_archive

# 1. Third-party runtime packages MUST NOT be importable in a core-only install.
FORBIDDEN = (
    "backports.zstd",
    "zstandard",
    "lz4",
    "cryptography",
    "py7zr",
    "rarfile",
    "pycdlib",
    "tqdm",
    "pyppmd",
    "inflate64",
    "brotli",
    "rapidgzip",
    "indexed_bzip2",
)
present = []
for mod in FORBIDDEN:
    try:
        __import__(mod)
        present.append(mod)
    except ImportError:
        pass
if present:
    sys.exit(f"FAIL: third-party package(s) present in a core-only install: {present}")

print(f"archivey {archivey.__version__}: zero-dep core import OK")

# 2. The spine works on stdlib alone (directory backend round-trip).
with tempfile.TemporaryDirectory() as d:
    with open(os.path.join(d, "hello.txt"), "wb") as f:
        f.write(b"hi")
    with open_archive(d) as ar:
        names = [m.name for m in ar]
        if "hello.txt" not in names:
            sys.exit(f"FAIL: directory backend did not list hello.txt: {names}")
        data = ar.read("hello.txt")
        if data != b"hi":
            sys.exit(f"FAIL: directory backend read wrong data: {data!r}")

print("directory backend round-trip OK")

# 3. Native unix-compress (.Z) is core — no third-party decoder.
# Precomputed ncompress fixture for b"hi" (avoids needing ncompress in core-only).
_Z_HI = bytes.fromhex("1f9d9068d200")
with tempfile.TemporaryDirectory() as d:
    zpath = os.path.join(d, "hi.Z")
    with open(zpath, "wb") as f:
        f.write(_Z_HI)
    with open_archive(zpath) as ar:
        data = ar.read(ar.members()[0])
        if data != b"hi":
            sys.exit(f"FAIL: unix-compress core decode wrong data: {data!r}")

print("unix-compress (.Z) core decode OK")
