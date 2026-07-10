"""Top-level pytest configuration and shared fixtures."""

from __future__ import annotations

import importlib.util
import os
import shutil
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


def requires_binary(*names: str) -> pytest.MarkDecorator:
    """Skip a test when an external tool (e.g. the ``7z`` or ``unrar`` CLI) is not on PATH.

    The oracle-availability rule (see PLAN.md): a test that shells out to an external
    binary must *skip*, not fail, where that binary is absent, so CI legs and dev
    machines without it stay green.
    """
    missing = [n for n in names if shutil.which(n) is None]
    return pytest.mark.skipif(
        bool(missing),
        reason=f"requires external binary(ies): {', '.join(missing)}",
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


def pytest_exception_interact(node, call, report) -> None:
    """Print the active fuzz mutation when a parametrized case times out or errors."""
    if call.when != "call" or "test_mutation_fuzz.py" not in node.nodeid:
        return
    from tests.test_mutation_fuzz import active_mutation_report

    msg = active_mutation_report()
    if msg is None:
        return
    tr = node.config.pluginmanager.get_plugin("terminalreporter")
    if tr is not None:
        tr.write_line(msg, red=True, bold=True)


def pytest_configure(config: pytest.Config) -> None:
    """Register the shared Hypothesis settings profile used by property-safety tests.

    Default: ``max_examples=100``, ``deadline=None``, ``derandomize=True`` (reproducible
    CI budget). ``ARCHIVEY_FUZZ_EXAMPLES`` selects a deeper local/nightly sweep — mirrors
    the mutation harness's ``ARCHIVEY_FUZZ_MUTATIONS`` pattern. Hypothesis is a ``dev``
    dependency; under ``[core-only]`` the import is absent and this is a no-op (the
    property module itself skips collection).
    """
    try:
        from hypothesis import settings
    except ImportError:
        return
    raw = os.environ.get("ARCHIVEY_FUZZ_EXAMPLES", "100")
    try:
        max_examples = int(raw)
    except ValueError as exc:
        raise pytest.UsageError(
            f"ARCHIVEY_FUZZ_EXAMPLES must be an integer, got {raw!r}"
        ) from exc
    settings.register_profile(
        "archivey",
        max_examples=max_examples,
        deadline=None,
        derandomize=True,
    )
    settings.load_profile("archivey")


@pytest.fixture
def test_dir(tmp_path: Path) -> Path:
    """Create a test directory with some files and subdirectories."""
    (tmp_path / "file1.txt").write_bytes(b"hello world")
    (tmp_path / "file2.txt").write_bytes(b"foo bar baz")
    subdir = tmp_path / "subdir"
    subdir.mkdir()
    (subdir / "nested.txt").write_bytes(b"nested content")
    return tmp_path
