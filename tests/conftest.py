"""Top-level pytest configuration and shared fixtures."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

# Test archive cache dir (configurable via env var)
ARCHIVEY_TEST_CACHE = os.environ.get(
    "ARCHIVEY_TEST_CACHE",
    str(Path(__file__).parent.parent / ".pytest_cache" / "archivey-archives"),
)


@pytest.fixture
def test_dir(tmp_path: Path) -> Path:
    """Create a test directory with some files and subdirectories."""
    (tmp_path / "file1.txt").write_bytes(b"hello world")
    (tmp_path / "file2.txt").write_bytes(b"foo bar baz")
    subdir = tmp_path / "subdir"
    subdir.mkdir()
    (subdir / "nested.txt").write_bytes(b"nested content")
    return tmp_path
