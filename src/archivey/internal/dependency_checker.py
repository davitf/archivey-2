import shutil
import subprocess
import sys
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from typing import Optional


@dataclass
class DependencyVersions:
    """Versions of optional dependencies used by archivey."""

    python_version: Optional[str] = None
    rarfile_version: Optional[str] = None
    py7zr_version: Optional[str] = None
    cryptography_version: Optional[str] = None
    lz4_version: Optional[str] = None
    zstandard_version: Optional[str] = None
    pycdlib_version: Optional[str] = None
    rapidgzip_version: Optional[str] = None
    indexed_bzip2_version: Optional[str] = None
    uncompresspy_version: Optional[str] = None
    brotli_version: Optional[str] = None
    unrar_version: Optional[str] = None
    pyzstd_version: Optional[str] = None
    stdlib_zstd_version: Optional[str] = None
    python_xz_version: Optional[str] = None


def get_dependency_versions() -> DependencyVersions:
    """Get versions of all optional dependencies.

    Returns:
        DependencyVersions: A dataclass containing version information for all dependencies.
    """
    versions = DependencyVersions()

    versions.python_version = (
        f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    )

    # Check optional dependencies
    for package, attr in [
        ("rarfile", "rarfile_version"),
        ("py7zr", "py7zr_version"),
        ("cryptography", "cryptography_version"),
        ("lz4", "lz4_version"),
        ("zstandard", "zstandard_version"),
        ("pycdlib", "pycdlib_version"),
        ("rapidgzip", "rapidgzip_version"),
        ("indexed_bzip2", "indexed_bzip2_version"),
        ("uncompresspy", "uncompresspy_version"),
        ("brotli", "brotli_version"),
        ("pyzstd", "pyzstd_version"),
        ("python-xz", "python_xz_version"),
    ]:
        try:
            setattr(versions, attr, version(package))
        except PackageNotFoundError:
            pass

    try:
        import compression.zstd  # type: ignore[import-not-found]  # noqa: F401

        versions.stdlib_zstd_version = f"stdlib ({versions.python_version})"
    except ImportError:
        pass

    # Check if the unrar command is available
    unrar_path = shutil.which("unrar")
    if unrar_path:
        try:
            proc = subprocess.run(
                [unrar_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            if proc.stdout:
                lines = [
                    line.strip()
                    for line in proc.stdout.splitlines()
                    if "unrar" in line.lower()
                ]
                versions.unrar_version = lines[0].split("   ")[0] if lines else None
        except (subprocess.SubprocessError, OSError):
            versions.unrar_version = "available"
    else:
        versions.unrar_version = None

    return versions


def format_dependency_versions(versions: DependencyVersions) -> str:
    """Format dependency versions as a string.

    Args:
        versions: The DependencyVersions instance to format

    Returns:
        str: A formatted string showing all dependency versions
    """
    lines = ["Dependency Versions:"]
    for field in versions.__dataclass_fields__:
        value = getattr(versions, field)
        if value is not None:
            lines.append(f"  {field}: {value}")
        else:
            lines.append(f"  {field}: not installed")
    return "\n".join(lines)


if __name__ == "__main__":
    versions = get_dependency_versions()
    print(format_dependency_versions(versions))
