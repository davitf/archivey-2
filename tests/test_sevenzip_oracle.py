"""Native 7z reader cross-checks against py7zr."""

from __future__ import annotations

from pathlib import Path

import pytest

from archivey import MemberType, open_archive
from tests.sample_archives import CORPUS, CorpusEntry, corpus_archive_path

_ORACLE_ENTRY_IDS = {"basic", "encoding", "permissions", "large", "encrypted"}
_PARAMS = [
    pytest.param(entry, id=entry.id)
    for entry in CORPUS
    if entry.id in _ORACLE_ENTRY_IDS and "7z" in entry.formats
]


def _py7zr():
    return pytest.importorskip("py7zr")


def _password(entry: CorpusEntry) -> str | None:
    return entry.passwords[0] if entry.passwords else None


@pytest.mark.parametrize("entry", _PARAMS)
def test_native_sevenzip_matches_py7zr_metadata_and_bytes(
    entry: CorpusEntry, tmp_path: Path
) -> None:
    py7zr = _py7zr()
    archive = corpus_archive_path(entry, "7z", tmp_path)
    password = _password(entry)

    with py7zr.SevenZipFile(archive, "r", password=password) as oracle:
        oracle_infos = {
            info.filename: info
            for info in oracle.list()
            if not getattr(info, "is_directory", False)
        }
        oracle_dir = tmp_path / f"{entry.id}-py7zr"
        oracle.extractall(oracle_dir)

    with open_archive(archive, password=password) as native:
        native_members = {
            member.name: member
            for member in native.members()
            if member.type is MemberType.FILE
        }
        assert set(native_members) == set(oracle_infos)
        for name, oracle_info in oracle_infos.items():
            member = native_members[name]
            assert member.size == oracle_info.uncompressed
            if oracle_info.compressed is not None:
                assert member.compressed_size == oracle_info.compressed
            assert member.hashes.get("crc32") == oracle_info.crc32
            assert native.read(member) == (oracle_dir / name).read_bytes()
