"""On-demand benchmark fixtures (not committed).

Two scales:

- ``ci`` — small archives for the PR structural gate (fast; solid O(n²) still visible).
- ``realistic`` — multi-MiB corpora for wall-time ratios vs stdlib (VISION ≤1.3× surface).

Large solid 7z/RAR archives are generated when py7zr / ``rar`` are available.
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

# ---------------------------------------------------------------------------
# Scale profiles
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Scale:
    name: str
    common_members: int
    common_member_size: int
    gzip_size: int
    solid_members: int
    solid_member_size: int


SCALES: dict[str, Scale] = {
    # ~32 KiB ZIP/TAR, 64 KiB gzip, ~2 MiB solid — PR structural gate.
    "ci": Scale(
        name="ci",
        common_members=8,
        common_member_size=4 * 1024,
        gzip_size=64 * 1024,
        solid_members=32,
        solid_member_size=64 * 1024,
    ),
    # ~16 MiB ZIP/TAR, 32 MiB gzip, ~16 MiB solid — wall-time vs stdlib.
    "realistic": Scale(
        name="realistic",
        common_members=64,
        common_member_size=256 * 1024,
        gzip_size=32 * 1024 * 1024,
        solid_members=64,
        solid_member_size=256 * 1024,
    ),
}

DEFAULT_SCALE = "ci"


@dataclass(frozen=True)
class FixtureSet:
    """Paths to archives used by one harness run."""

    root: Path
    scale: Scale
    zip_path: Path
    tar_path: Path
    gzip_path: Path
    solid_7z: Path | None
    solid_rar: Path | None
    unpacked_solid_7z: int
    unpacked_solid_rar: int


def _payload(i: int, size: int) -> bytes:
    """Semi-compressible member payload (not pure zeros; not fully random).

    A 512-byte unique header plus a repeating 4 KiB patterned block — closer to
    text/log-ish ratios than ``os.urandom``, while still exercising the decompressor.
    """
    header = f"archivey-bench-{i}\n".encode()
    pattern = bytes((j * 17 + i) % 256 for j in range(4096))
    body_len = max(0, size - len(header))
    reps, rem = divmod(body_len, len(pattern))
    return header + pattern * reps + pattern[:rem]


def build_zip(path: Path, scale: Scale) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for i in range(scale.common_members):
            zf.writestr(f"f{i:04d}.bin", _payload(i, scale.common_member_size))


def build_tar(path: Path, scale: Scale) -> None:
    with tarfile.open(path, "w") as tf:
        for i in range(scale.common_members):
            data = _payload(i, scale.common_member_size)
            info = tarfile.TarInfo(name=f"f{i:04d}.bin")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))


def build_gzip(path: Path, scale: Scale) -> None:
    path.write_bytes(gzip.compress(_payload(0, scale.gzip_size), compresslevel=6))


def build_solid_7z(path: Path, scale: Scale) -> int:
    """Build a solid LZMA2 7z; return total unpacked file bytes."""
    import py7zr

    src = path.parent / f"{path.stem}-src"
    if src.exists():
        shutil.rmtree(src)
    src.mkdir(parents=True)
    total = 0
    files: dict[str, bytes] = {}
    for i in range(scale.solid_members):
        data = _payload(i, scale.solid_member_size)
        name = f"m{i:03d}.bin"
        files[name] = data
        total += len(data)
        (src / name).write_bytes(data)
    with py7zr.SevenZipFile(path, "w", filters=[{"id": py7zr.FILTER_LZMA2}]) as archive:
        for name in sorted(files):
            archive.write(src / name, arcname=name)
    return total


def build_solid_rar(path: Path, scale: Scale) -> int | None:
    """Build a solid RAR via the ``rar`` binary; return unpacked bytes or None if unavailable."""
    if shutil.which("rar") is None:
        return None
    src = path.parent / f"{path.stem}-src"
    if src.exists():
        shutil.rmtree(src)
    src.mkdir(parents=True)
    total = 0
    for i in range(scale.solid_members):
        data = _payload(i, scale.solid_member_size)
        total += len(data)
        (src / f"m{i:03d}.bin").write_bytes(data)
    cmd = ["rar", "a", "-m3", "-s", "-ep1", str(path), str(src / "*")]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return total if path.exists() else None


def _unpacked_solid(scale: Scale) -> int:
    return sum(
        len(_payload(i, scale.solid_member_size)) for i in range(scale.solid_members)
    )


def materialize_fixtures(
    root: Path | None = None,
    *,
    scale: str | Scale = DEFAULT_SCALE,
) -> FixtureSet:
    """Create (or reuse) the comparison corpus under ``root``."""
    if isinstance(scale, str):
        if scale not in SCALES:
            raise ValueError(f"unknown scale {scale!r}; choose from {sorted(SCALES)}")
        scale_obj = SCALES[scale]
    else:
        scale_obj = scale

    if root is None:
        cache = os.environ.get("ARCHIVEY_BENCH_CACHE")
        root = (
            Path(cache) if cache else Path(tempfile.mkdtemp(prefix="archivey-bench-"))
        )
    # Isolate scales so a cached ``ci`` zip is never reused as ``realistic``.
    root = root / scale_obj.name
    root.mkdir(parents=True, exist_ok=True)

    zip_path = root / "common.zip"
    tar_path = root / "common.tar"
    gzip_path = root / "common.gz"
    if not zip_path.exists():
        build_zip(zip_path, scale_obj)
    if not tar_path.exists():
        build_tar(tar_path, scale_obj)
    if not gzip_path.exists():
        build_gzip(gzip_path, scale_obj)

    solid_7z: Path | None = None
    unpacked_7z = 0
    try:
        import py7zr  # noqa: F401
    except ImportError:
        py7zr = None  # type: ignore[assignment]
    if py7zr is not None:
        solid_7z = root / "solid-large.7z"
        if not solid_7z.exists():
            unpacked_7z = build_solid_7z(solid_7z, scale_obj)
        else:
            unpacked_7z = _unpacked_solid(scale_obj)

    solid_rar: Path | None = None
    unpacked_rar = 0
    rar_path = root / "solid-large.rar"
    if rar_path.exists():
        solid_rar = rar_path
        unpacked_rar = _unpacked_solid(scale_obj)
    else:
        built = build_solid_rar(rar_path, scale_obj)
        if built is not None:
            solid_rar = rar_path
            unpacked_rar = built

    return FixtureSet(
        root=root,
        scale=scale_obj,
        zip_path=zip_path,
        tar_path=tar_path,
        gzip_path=gzip_path,
        solid_7z=solid_7z,
        solid_rar=solid_rar,
        unpacked_solid_7z=unpacked_7z,
        unpacked_solid_rar=unpacked_rar,
    )
