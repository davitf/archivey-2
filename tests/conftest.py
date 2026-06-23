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

        @requires("zstandard")
        def test_zstd_stream(): ...

    Tests asserting the *degradation* behavior (a missing lib raising
    PackageNotInstalledError) should instead run unconditionally and assert that error.
    """
    missing = [p for p in packages if importlib.util.find_spec(p) is None]
    return pytest.mark.skipif(
        bool(missing),
        reason=f"requires optional package(s): {', '.join(missing)}",
    )


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """(temp diagnostic) Report live/unclosed accelerator streams before shutdown.

    The macOS [all] leg aborts (exit 134) at interpreter finalization from a still-running
    rapidgzip/indexed_bzip2 thread. This runs well before finalization: it forces a GC,
    reports how many accelerator streams are still tracked and how many are unclosed, then
    closes them — distinguishing "an object escapes our atexit tracking" from "close() is
    ineffective on macOS" (if it still aborts after this, close itself is the problem).
    """
    import gc
    import sys

    try:
        from archivey.internal.streams import codecs
    except Exception as e:  # pragma: no cover - diagnostic only
        print(f"[accel-diag] could not import codecs: {e!r}", file=sys.stderr)
        return

    gc.collect()
    live = list(codecs._live_accelerator_streams)
    unclosed = [s for s in live if not getattr(s, "closed", True)]
    print(
        f"[accel-diag] tracked-live={len(live)} unclosed={len(unclosed)}",
        file=sys.stderr,
    )
    for s in live:
        try:
            s.close()
        except Exception as e:  # pragma: no cover - diagnostic only
            print(f"[accel-diag] close raised: {e!r}", file=sys.stderr)
    gc.collect()


@pytest.fixture
def test_dir(tmp_path: Path) -> Path:
    """Create a test directory with some files and subdirectories."""
    (tmp_path / "file1.txt").write_bytes(b"hello world")
    (tmp_path / "file2.txt").write_bytes(b"foo bar baz")
    subdir = tmp_path / "subdir"
    subdir.mkdir()
    (subdir / "nested.txt").write_bytes(b"nested content")
    return tmp_path
