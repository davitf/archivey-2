"""Compare Archivey's native 7z reader against ``py7zr`` on py7zr's own test archives.

Off by default. Point ``ARCHIVEY_PY7ZR_TEST_FILES`` at py7zr's ``tests/data``
directory (clone https://github.com/miurahr/py7zr and use ``…/tests/data``)::

    ARCHIVEY_PY7ZR_TEST_FILES=/path/to/py7zr/tests/data \\
      uv run --no-sync pytest tests/test_py7zr_corpus.py -q

Skips archives py7zr cannot open (BCJ2, some LZ4/Brotli fixtures, intentionally
corrupt inputs) and continuation volumes (open the first ``.7z.001`` only).

7z triage (2026-07, vs py7zr ``tests/data``) — known failures marked ``xfail``:

* **BUG** ``empty.7z`` — ``nextHeaderSize==0`` should open as an empty archive;
  parser currently raises ``CorruptionError``.
* **BUG** ``copy_bcj_1.7z``, ``p7zip-zstd.7z`` — BCJ paired with a non-LZMA codec
  (COPY / Zstd) is mis-routed through the LZMA-family path and raises
  ``CorruptionError`` instead of staging BCJ via ``pybcj`` (or a clear
  ``UnsupportedFeatureError``).
* **BUG** ``lzma_bcj_2.7z`` — LZMA1+BCJ solid folder still silently truncates large
  members despite ``pybcj`` staging (BPO-21872 residual).
* **SEMANTIC** ``github_14.7z``, ``github_14_multi.7z`` — archive has no NAME
  property; Archivey normalizes the empty name to ``"."`` (per
  ``archive-data-model``), while py7zr synthesizes the archive stem. Not a
  decode bug.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from archivey import MemberType, open_archive
from tests.conftest import requires

_ENV = "ARCHIVEY_PY7ZR_TEST_FILES"

# Passwords from py7zr's own tests (tests/test_encryption.py, test_info.py, …).
_PASSWORDS: dict[str, str] = {
    "encrypted_1.7z": "secret",
    "encrypted_2.7z": "secret",
    "encrypted_3.7z": "secret",
    "encrypted_4.7z": "secret",
    "encrypted_5.7z": "secret",
    "encrypted_6.7z": "secret",
    "filename_encryption.7z": "hello",
}

# Intentionally corrupt or non-primary inputs — not oracle targets.
_SKIP_NAMES = frozenset(
    {
        "crc_corrupted.7z",
        "data_corrupted.7z",
        "archive.7z.002",
    }
)

# Triaged known divergences. Values are (strict, reason).
_XFAIL: dict[str, tuple[bool, str]] = {
    "empty.7z": (
        True,
        "BUG: nextHeaderSize==0 empty archive raises CorruptionError",
    ),
    "copy_bcj_1.7z": (
        True,
        "BUG: BCJ+COPY mis-routed through LZMA path (needs standalone BCJ stage)",
    ),
    "p7zip-zstd.7z": (
        True,
        "BUG: Zstd+BCJ mis-routed through LZMA path (needs standalone BCJ stage)",
    ),
    "lzma_bcj_2.7z": (
        True,
        "BUG: LZMA1+BCJ solid still truncates large members (BPO-21872 residual)",
    ),
    "github_14.7z": (
        True,
        "SEMANTIC: no NAME property → Archivey '.' vs py7zr archive-stem synthesis",
    ),
    "github_14_multi.7z": (
        True,
        "SEMANTIC: no NAME property → Archivey '.' vs py7zr archive-stem synthesis",
    ),
}


def _files_dir() -> Path | None:
    raw = os.environ.get(_ENV)
    if not raw:
        return None
    path = Path(raw).expanduser()
    return path if path.is_dir() else None


def _password_for(name: str) -> str | None:
    return _PASSWORDS.get(name)


def _is_primary_archive(path: Path) -> bool:
    if path.suffix.lower() != ".7z" and not path.name.lower().endswith(".7z.001"):
        return False
    if path.name in _SKIP_NAMES:
        return False
    # Continuation volumes: *.7z.NNN for NNN != 001
    lower = path.name.lower()
    if lower.endswith(".7z.001"):
        return True
    if ".7z." in lower:
        return False
    return True


def _normalize_name(name: str) -> str:
    normalized = name.replace("\\", "/").lstrip("/").rstrip("/")
    return normalized or "."


def _py7zr_expect_type(info: object) -> MemberType:
    if getattr(info, "is_directory", False):
        return MemberType.DIRECTORY
    if getattr(info, "is_symlink", False):
        return MemberType.SYMLINK
    return MemberType.FILE


def _oracle_link_target(extract_root: Path, member_name: str) -> str | None:
    path = extract_root / member_name
    if not path.is_symlink():
        return None
    return os.readlink(path)


def _iter_archives(root: Path) -> Iterator[Path]:
    for path in sorted(root.iterdir()):
        if _is_primary_archive(path):
            yield path


def _archive_param(path: Path) -> pytest.ParameterSet:
    xfail = _XFAIL.get(path.name)
    if xfail is None:
        return pytest.param(path, id=path.name)
    strict, reason = xfail
    return pytest.param(
        path, id=path.name, marks=pytest.mark.xfail(strict=strict, reason=reason)
    )


_ROOT = _files_dir()
_ARCHIVES = list(_iter_archives(_ROOT)) if _ROOT is not None else []

pytestmark = [
    pytest.mark.skipif(
        _ROOT is None,
        reason=f"set {_ENV} to py7zr's tests/data directory to run this corpus",
    ),
    requires("py7zr"),
]


@pytest.fixture(scope="module")
def py7zr_mod():
    return pytest.importorskip("py7zr")


@pytest.mark.parametrize(
    "archive",
    [_archive_param(p) for p in _ARCHIVES],
)
def test_native_matches_py7zr_on_py7zr_corpus(
    archive: Path, py7zr_mod: object, tmp_path: Path
) -> None:
    py7zr = py7zr_mod
    password = _password_for(archive.name)

    if password is not None and archive.name.startswith("encrypted_"):
        pytest.importorskip("cryptography")

    oracle_dir = tmp_path / "py7zr"
    try:
        with py7zr.SevenZipFile(archive, "r", password=password) as oracle:  # type: ignore[attr-defined]
            oracle_infos = {
                _normalize_name(info.filename): info for info in oracle.list()
            }
            oracle.extractall(oracle_dir)
    except Exception as exc:  # noqa: BLE001 — oracle may reject unsupported fixtures
        pytest.skip(f"py7zr cannot open {archive.name}: {exc}")

    with open_archive(archive, password=password) as native:
        native_by_name = {_normalize_name(m.name): m for m in native.members()}

        assert set(native_by_name) == set(oracle_infos), (
            f"member name mismatch for {archive.name}: "
            f"only_native={sorted(set(native_by_name) - set(oracle_infos))} "
            f"only_py7zr={sorted(set(oracle_infos) - set(native_by_name))}"
        )

        for key, info in oracle_infos.items():
            member = native_by_name[key]
            expect_type = _py7zr_expect_type(info)
            assert member.type is expect_type, (
                f"{archive.name}:{key}: type {member.type} != {expect_type}"
            )
            if expect_type is MemberType.FILE:
                assert member.size == info.uncompressed, (
                    f"{archive.name}:{key}: size {member.size} != {info.uncompressed}"
                )
                oracle_path = oracle_dir / key
                assert native.read(member) == oracle_path.read_bytes(), (
                    f"{archive.name}:{key}: byte mismatch"
                )
            elif expect_type is MemberType.SYMLINK:
                expected_target = _oracle_link_target(oracle_dir, key)
                assert member.link_target == expected_target, (
                    f"{archive.name}:{key}: link_target "
                    f"{member.link_target!r} != {expected_target!r}"
                )


def test_py7zr_corpus_discovered_archives() -> None:
    """Sanity: the env-pointed directory actually contains py7zr fixtures."""
    assert _ROOT is not None
    assert _ARCHIVES, f"no primary .7z archives found under {_ROOT}"
    names = {p.name for p in _ARCHIVES}
    assert "test_1.7z" in names or "solid.7z" in names
