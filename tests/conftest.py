"""Top-level pytest configuration and shared fixtures."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

# Test archive cache dir (configurable via env var)
ARCHIVEY_TEST_CACHE = os.environ.get(
    "ARCHIVEY_TEST_CACHE",
    str(Path(__file__).parent.parent / ".pytest_cache" / "archivey-archives"),
)


def requires(*packages: str) -> pytest.MarkDecorator:
    """Skip a test (or parametrization) when an optional package is not importable.

    This is what lets the whole suite run in the `core-only` CI leg: tests that need
    an optional format library are skipped cleanly there rather than erroring, while
    they run normally in the `[all]` leg. Use it as a decorator::

        @requires_zstd()
        def test_zstd_stream(): ...

    Tests asserting the *degradation* behavior (a missing lib raising
    PackageNotInstalledError) should instead run unconditionally and assert that error.
    """
    missing = [p for p in packages if importlib.util.find_spec(p) is None]
    return pytest.mark.skipif(
        bool(missing),
        reason=f"requires optional package(s): {', '.join(missing)}",
    )


def requires_zstd() -> pytest.MarkDecorator:
    """Skip when neither stdlib ``compression.zstd`` nor ``backports.zstd`` is importable."""
    has = _has_zstd_backend()
    return pytest.mark.skipif(
        not has,
        reason="requires zstd backend (compression.zstd or backports.zstd)",
    )


def _has_zstd_backend() -> bool:
    for name in ("compression.zstd", "backports.zstd"):
        try:
            if importlib.util.find_spec(name) is not None:
                return True
        except ModuleNotFoundError:
            continue
    return False


def zstd_backend():
    """Return the installed zstd codec module (stdlib on 3.14+, else backports)."""
    import importlib

    for name in ("compression.zstd", "backports.zstd"):
        try:
            return importlib.import_module(name)
        except ImportError:
            continue
    raise RuntimeError("no zstd backend installed")


@pytest.fixture
def test_dir(tmp_path: Path) -> Path:
    """Create a test directory with some files and subdirectories."""
    (tmp_path / "file1.txt").write_bytes(b"hello world")
    (tmp_path / "file2.txt").write_bytes(b"foo bar baz")
    subdir = tmp_path / "subdir"
    subdir.mkdir()
    (subdir / "nested.txt").write_bytes(b"nested content")
    return tmp_path
