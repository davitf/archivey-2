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
    """(temp diagnostic) Hunt the source of the macOS shutdown abort (exit 134).

    The prior round proved our *tracked* streams are all gone by now (tracked-live=0), yet
    macOS still aborts at interpreter finalization "from a running rapidgzip thread". So the
    culprit is an orphaned worker thread (its Python object already collected) or an
    untracked accelerator object surviving to shutdown. This scans the live GC graph for any
    rapidgzip/indexed_bzip2 objects and reports leftover non-main threads, then forces a GC.
    """
    import gc
    import sys
    import threading

    def _accel_type(o: object) -> bool:
        tp = type(o)
        mod = getattr(tp, "__module__", "")
        mod = mod.lower() if isinstance(mod, str) else ""
        return (
            tp.__name__ in ("RapidgzipFile", "IndexedBzip2File")
            or "rapidgzip" in mod
            or "indexed_bzip2" in mod
        )

    gc.collect()
    accel_objs = [o for o in gc.get_objects() if _accel_type(o)]
    threads = [t for t in threading.enumerate() if t is not threading.main_thread()]
    print(
        f"[accel-diag] live-accel-objs={len(accel_objs)} "
        f"types={sorted({type(o).__name__ for o in accel_objs})} "
        f"nonmain-threads={len(threads)} names={[t.name for t in threads]}",
        file=sys.stderr,
    )
    for o in accel_objs:
        for meth in ("join_threads", "close"):
            fn = getattr(o, meth, None)
            if fn is not None:
                try:
                    fn()
                except Exception as e:  # noqa: BLE001 - diagnostic only
                    print(f"[accel-diag] {meth} raised: {e!r}", file=sys.stderr)
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
