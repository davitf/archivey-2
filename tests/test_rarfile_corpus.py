"""Compare Archivey's native RAR reader against ``rarfile`` on rarfile's own test archives.

Off by default. Point ``ARCHIVEY_RARFILE_TEST_FILES`` at rarfile's ``test/files``
directory (clone https://github.com/markokr/rarfile and use ``…/test/files``)::

    ARCHIVEY_RARFILE_TEST_FILES=/path/to/rarfile/test/files \\
      uv run --no-sync pytest tests/test_rarfile_corpus.py -q

Archives that Archivey intentionally rejects (RAR2 / extract version ≤ 20) are
asserted to raise ``UnsupportedFeatureError``. SFX archives are opened with an
explicit ``format=ArchiveFormat.RAR`` because ``.sfx`` is not a registered
extension and the magic may sit past offset 0.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from archivey import ArchiveFormat, MemberType, open_archive
from archivey.exceptions import UnsupportedFeatureError
from tests.conftest import requires, requires_binary

_ENV = "ARCHIVEY_RARFILE_TEST_FILES"

# Password used throughout rarfile's own test suite for *psw* / *hpsw* fixtures.
_PASSWORD = "password"

# RAR2-era / extract_version ≤ 20 archives Archivey rejects by design.
_UNSUPPORTED_RAR2 = frozenset(
    {
        "rar15-comment-lock.rar",
        "rar15-comment.rar",
        "rar202-comment-nopsw.rar",
        "rar202-comment-psw.rar",
        "rar3-old.rar",
        "rar3-seektest.sfx",
        "rar3-vols.part1.rar",
        "seektest.rar",
    }
)

# Continuation volumes — open via the first volume only.
_SKIP_NAMES = frozenset(
    {
        "rar3-old.r00",
        "rar3-old.r01",
        "rar3-vols.part2.rar",
        "rar3-vols.part3.rar",
        "rar5-vols.part2.rar",
        "rar5-vols.part3.rar",
    }
)


def _files_dir() -> Path | None:
    raw = os.environ.get(_ENV)
    if not raw:
        return None
    path = Path(raw).expanduser()
    return path if path.is_dir() else None


def _password_for(name: str) -> str | None:
    if "psw" in name or "hpsw" in name:
        return _PASSWORD
    return None


def _is_primary_archive(path: Path) -> bool:
    if path.suffix.lower() not in {".rar", ".sfx"}:
        return False
    if path.name.endswith(".exp"):
        return False
    if path.name in _SKIP_NAMES:
        return False
    # Mid-set volumes: *.partN.rar for N != 1
    lower = path.name.lower()
    if ".part" in lower and not lower.endswith(".part1.rar"):
        return False
    return True


def _normalize_name(name: str) -> str:
    return name.replace("\\", "/").rstrip("/")


def _rarfile_expect_type(info: object) -> MemberType:
    is_dir = getattr(info, "is_dir", lambda: False)()
    is_symlink = getattr(info, "is_symlink", lambda: False)()
    redir = getattr(info, "file_redir", None)
    if is_dir:
        return MemberType.DIRECTORY
    if redir is not None:
        redir_type = redir[0]
        if redir_type in (4, 5):  # HARD_LINK / FILE_COPY
            return MemberType.HARDLINK
        if redir_type in (1, 2, 3):  # unix/win symlink / junction
            return MemberType.SYMLINK
    if is_symlink:
        return MemberType.SYMLINK
    return MemberType.FILE


def _iter_archives(root: Path) -> Iterator[Path]:
    for path in sorted(root.iterdir()):
        if _is_primary_archive(path):
            yield path


_ROOT = _files_dir()
_ARCHIVES = list(_iter_archives(_ROOT)) if _ROOT is not None else []

pytestmark = [
    pytest.mark.skipif(
        _ROOT is None,
        reason=f"set {_ENV} to rarfile's test/files directory to run this corpus",
    ),
    requires("rarfile"),
    requires_binary("unrar"),
]


@pytest.fixture(scope="module")
def rarfile_mod():
    return pytest.importorskip("rarfile")


@pytest.mark.parametrize(
    "archive",
    [pytest.param(p, id=p.name) for p in _ARCHIVES],
)
def test_native_matches_rarfile_on_rarfile_corpus(
    archive: Path, rarfile_mod: object
) -> None:
    rarfile = rarfile_mod
    password = _password_for(archive.name)

    if archive.name in _UNSUPPORTED_RAR2:
        with pytest.raises(UnsupportedFeatureError, match="extract version|RAR2"):
            open_archive(
                archive,
                password=password,
                format=ArchiveFormat.RAR if archive.suffix.lower() == ".sfx" else None,
            )
        return

    # Header-encrypted archives need cryptography.
    if "hpsw" in archive.name:
        pytest.importorskip("cryptography")

    fmt = ArchiveFormat.RAR if archive.suffix.lower() == ".sfx" else None

    with rarfile.RarFile(archive) as oracle:  # type: ignore[attr-defined]
        if password:
            oracle.setpassword(password)
        oracle_infos = {
            _normalize_name(info.filename): info for info in oracle.infolist()
        }
        oracle_bytes: dict[str, bytes] = {}
        oracle_link_targets: dict[str, str] = {}
        for key, info in oracle_infos.items():
            expect_type = _rarfile_expect_type(info)
            if expect_type is MemberType.FILE:
                oracle_bytes[key] = oracle.read(info)
            elif expect_type is MemberType.SYMLINK:
                redir = getattr(info, "file_redir", None)
                if redir is not None:
                    oracle_link_targets[key] = redir[2]
                else:
                    # RAR4: link target is member data.
                    oracle_link_targets[key] = oracle.read(info).decode(
                        "utf-8", errors="surrogateescape"
                    )
            elif expect_type is MemberType.HARDLINK:
                redir = getattr(info, "file_redir", None)
                if redir is not None:
                    oracle_link_targets[key] = redir[2]

    with open_archive(archive, password=password, format=fmt) as native:
        native_by_name = {_normalize_name(m.name): m for m in native.members()}

        assert set(native_by_name) == set(oracle_infos), (
            f"member name mismatch for {archive.name}: "
            f"only_native={sorted(set(native_by_name) - set(oracle_infos))} "
            f"only_rarfile={sorted(set(oracle_infos) - set(native_by_name))}"
        )

        for key, info in oracle_infos.items():
            member = native_by_name[key]
            expect_type = _rarfile_expect_type(info)
            assert member.type is expect_type, (
                f"{archive.name}:{key}: type {member.type} != {expect_type}"
            )
            if expect_type is MemberType.FILE:
                assert member.size == info.file_size, (
                    f"{archive.name}:{key}: size {member.size} != {info.file_size}"
                )
                assert native.read(member) == oracle_bytes[key], (
                    f"{archive.name}:{key}: byte mismatch"
                )
            elif expect_type in (MemberType.SYMLINK, MemberType.HARDLINK):
                expected_target = oracle_link_targets.get(key)
                assert member.link_target == expected_target, (
                    f"{archive.name}:{key}: link_target "
                    f"{member.link_target!r} != {expected_target!r}"
                )


def test_rarfile_corpus_discovered_archives() -> None:
    """Sanity: the env-pointed directory actually contains rarfile fixtures."""
    assert _ROOT is not None
    assert _ARCHIVES, f"no primary .rar/.sfx archives found under {_ROOT}"
    names = {p.name for p in _ARCHIVES}
    # A few well-known rarfile fixtures that must be present if the path is right.
    assert "rar5-solid.rar" in names or "rar3-solid.rar" in names
