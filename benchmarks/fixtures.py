"""On-demand benchmark fixtures (not committed).

Small common-path archives (ZIP/TAR/gzip) are built in a temp/cache dir for wall-time
ratios. Large solid 7z/RAR archives are generated when py7zr / ``rar`` are available so
the O(n²) solid-block signal is visible.
"""

from __future__ import annotations

import gzip
import io
import os
import shutil
import subprocess
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

# Deliberately large enough that a from-start-per-member re-decode is obvious.
SOLID_MEMBER_COUNT = 32
SOLID_MEMBER_SIZE = 64 * 1024  # 64 KiB → ~2 MiB unpacked


@dataclass(frozen=True)
class FixtureSet:
    """Paths to archives used by one harness run."""

    root: Path
    zip_path: Path
    tar_path: Path
    gzip_path: Path
    solid_7z: Path | None
    solid_rar: Path | None
    unpacked_solid_7z: int
    unpacked_solid_rar: int


def _payload(i: int, size: int = 4096) -> bytes:
    return (bytes([i % 256]) * size) + f"-{i}".encode()


def build_zip(path: Path, *, n: int = 8, size: int = 4096) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for i in range(n):
            zf.writestr(f"f{i}.txt", _payload(i, size))


def build_tar(path: Path, *, n: int = 8, size: int = 4096) -> None:
    with tarfile.open(path, "w") as tf:
        for i in range(n):
            data = _payload(i, size)
            info = tarfile.TarInfo(name=f"f{i}.txt")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))


def build_gzip(path: Path, *, size: int = 64 * 1024) -> None:
    path.write_bytes(gzip.compress(_payload(0, size), compresslevel=6))


def build_solid_7z(path: Path) -> int:
    """Build a solid LZMA2 7z; return total unpacked file bytes."""
    import py7zr

    src = path.parent / f"{path.stem}-src"
    if src.exists():
        shutil.rmtree(src)
    src.mkdir(parents=True)
    total = 0
    files: dict[str, bytes] = {}
    for i in range(SOLID_MEMBER_COUNT):
        data = _payload(i, SOLID_MEMBER_SIZE)
        files[f"m{i:03d}.bin"] = data
        total += len(data)
        (src / f"m{i:03d}.bin").write_bytes(data)
    with py7zr.SevenZipFile(path, "w", filters=[{"id": py7zr.FILTER_LZMA2}]) as archive:
        for name in sorted(files):
            archive.write(src / name, arcname=name)
    return total


def build_solid_rar(path: Path) -> int | None:
    """Build a solid RAR via the ``rar`` binary; return unpacked bytes or None if unavailable."""
    if shutil.which("rar") is None:
        return None
    src = path.parent / f"{path.stem}-src"
    if src.exists():
        shutil.rmtree(src)
    src.mkdir(parents=True)
    total = 0
    for i in range(SOLID_MEMBER_COUNT):
        data = _payload(i, SOLID_MEMBER_SIZE)
        total += len(data)
        (src / f"m{i:03d}.bin").write_bytes(data)
    # -m3 default compression; -s solid; -ep1 strip base path.
    cmd = ["rar", "a", "-m3", "-s", "-ep1", str(path), str(src / "*")]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return total if path.exists() else None


def materialize_fixtures(root: Path | None = None) -> FixtureSet:
    """Create (or reuse) the comparison corpus under ``root``."""
    if root is None:
        cache = os.environ.get("ARCHIVEY_BENCH_CACHE")
        root = (
            Path(cache) if cache else Path(tempfile.mkdtemp(prefix="archivey-bench-"))
        )
    root.mkdir(parents=True, exist_ok=True)

    zip_path = root / "common.zip"
    tar_path = root / "common.tar"
    gzip_path = root / "common.gz"
    if not zip_path.exists():
        build_zip(zip_path)
    if not tar_path.exists():
        build_tar(tar_path)
    if not gzip_path.exists():
        build_gzip(gzip_path)

    solid_7z: Path | None = None
    unpacked_7z = 0
    try:
        import py7zr  # noqa: F401
    except ImportError:
        py7zr = None  # type: ignore[assignment]
    if py7zr is not None:
        solid_7z = root / "solid-large.7z"
        if not solid_7z.exists():
            unpacked_7z = build_solid_7z(solid_7z)
        else:
            unpacked_7z = SOLID_MEMBER_COUNT * (SOLID_MEMBER_SIZE + len(b"-0"))
            # payload helper appends b"-{i}"; recompute exactly if regenerating is skipped
            unpacked_7z = sum(
                len(_payload(i, SOLID_MEMBER_SIZE)) for i in range(SOLID_MEMBER_COUNT)
            )

    solid_rar: Path | None = None
    unpacked_rar = 0
    rar_path = root / "solid-large.rar"
    if rar_path.exists():
        solid_rar = rar_path
        unpacked_rar = sum(
            len(_payload(i, SOLID_MEMBER_SIZE)) for i in range(SOLID_MEMBER_COUNT)
        )
    else:
        built = build_solid_rar(rar_path)
        if built is not None:
            solid_rar = rar_path
            unpacked_rar = built

    return FixtureSet(
        root=root,
        zip_path=zip_path,
        tar_path=tar_path,
        gzip_path=gzip_path,
        solid_7z=solid_7z,
        solid_rar=solid_rar,
        unpacked_solid_7z=unpacked_7z,
        unpacked_solid_rar=unpacked_rar,
    )
