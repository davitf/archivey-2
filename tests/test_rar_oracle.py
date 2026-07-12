"""Native RAR reader cross-checks against rarfile."""

from __future__ import annotations

from pathlib import Path

import pytest

from archivey import MemberType, open_archive
from tests.conftest import requires, requires_binary
from tests.sample_archives import CORPUS, CorpusEntry, corpus_archive_path

_FIXTURES = Path(__file__).parent / "fixtures" / "rar"

_ORACLE_FIXTURES = [
    pytest.param("basic_nonsolid__.rar", None, id="basic-rar5"),
    pytest.param("basic_nonsolid__rar4.rar", None, id="basic-rar4"),
    pytest.param("basic_solid__.rar", None, id="solid-rar5"),
    pytest.param("basic_solid__rar4.rar", None, id="solid-rar4"),
    pytest.param("encryption__.rar", "password", id="enc-rar5"),
    pytest.param("encrypted_header__.rar", "header_password", id="hdr-rar5"),
]


def _rarfile():
    return pytest.importorskip("rarfile")


def _fixture(name: str) -> Path:
    path = _FIXTURES / name
    if not path.is_file():
        pytest.skip(f"missing vendored fixture {name}")
    return path


@requires("rarfile")
@requires_binary("unrar")
@pytest.mark.parametrize(("name", "password"), _ORACLE_FIXTURES)
def test_native_rar_matches_rarfile_metadata_and_bytes(
    name: str, password: str | None
) -> None:
    rarfile = _rarfile()
    archive = _fixture(name)
    if "header" in name:
        pytest.importorskip("cryptography")

    with rarfile.RarFile(archive) as oracle:
        if password:
            oracle.setpassword(password)
        oracle_infos = {
            info.filename.replace("\\", "/").rstrip("/"): info
            for info in oracle.infolist()
            if not info.is_dir()
        }
        oracle_bytes = {
            filename: oracle.read(info)
            for filename, info in oracle_infos.items()
            if not getattr(info, "is_symlink", lambda: False)()
            and not getattr(info, "redir_type", None)
        }

    with open_archive(archive, password=password) as native:
        native_files = {
            member.name.rstrip("/"): member
            for member in native.members()
            if member.type is MemberType.FILE
        }
        # Compare file members that rarfile exposes as regular files.
        common = set(native_files) & set(oracle_bytes)
        assert common, "expected overlapping file members with rarfile"
        for filename in common:
            member = native_files[filename]
            info = oracle_infos[filename]
            assert member.size == info.file_size
            assert native.read(member) == oracle_bytes[filename]


@requires("rarfile")
@requires_binary("unrar")
@requires_binary("rar")
@pytest.mark.parametrize(
    "entry",
    [
        pytest.param(entry, id=entry.id)
        for entry in CORPUS
        if entry.id in {"basic", "encoding", "permissions"} and "rar" in entry.formats
    ],
)
def test_corpus_rar_matches_rarfile(entry: CorpusEntry, tmp_path: Path) -> None:
    rarfile = _rarfile()
    archive = corpus_archive_path(entry, "rar", tmp_path)
    password = entry.passwords[0] if entry.passwords else None

    with rarfile.RarFile(archive) as oracle:
        if password:
            oracle.setpassword(password)
        oracle_files = {
            info.filename.replace("\\", "/"): oracle.read(info)
            for info in oracle.infolist()
            if not info.is_dir()
        }

    with open_archive(archive, password=password) as native:
        native_files = {
            member.name: native.read(member)
            for member in native.members()
            if member.type is MemberType.FILE
        }
        assert native_files == oracle_files
