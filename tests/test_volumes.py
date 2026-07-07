"""Tests for multi-source input and volume discovery (Phase 5 stage 3)."""

from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest

from archivey import extract, open_archive
from archivey.exceptions import UnsupportedFeatureError
from archivey.internal.volumes import discover_volume_siblings
from archivey.types import ArchiveFormat

_7Z_MAGIC = bytes.fromhex("377abcaf271c")
_RAR_MAGIC = b"Rar!\x1a\x07\x00"


def test_discover_7z_volume_siblings_natural_order(tmp_path: Path) -> None:
    for name in ("set.7z.010", "set.7z.002", "set.7z.001"):
        (tmp_path / name).write_bytes(b"")
    siblings = discover_volume_siblings(tmp_path / "set.7z.002")
    assert siblings is not None
    assert [p.name for p in siblings] == ["set.7z.001", "set.7z.002", "set.7z.010"]


def test_discover_rar_part_volumes(tmp_path: Path) -> None:
    for name in ("data.part2.rar", "data.part1.rar", "data.part10.rar"):
        (tmp_path / name).write_bytes(b"")
    siblings = discover_volume_siblings(tmp_path / "data.part10.rar")
    assert siblings is not None
    assert [p.name for p in siblings] == [
        "data.part1.rar",
        "data.part2.rar",
        "data.part10.rar",
    ]


def test_discover_old_rar_rnn_volumes(tmp_path: Path) -> None:
    (tmp_path / "archive.rar").write_bytes(b"")
    for name in ("archive.r01", "archive.r00"):
        (tmp_path / name).write_bytes(b"")
    siblings = discover_volume_siblings(tmp_path / "archive.r01")
    assert siblings is not None
    assert [p.name for p in siblings] == ["archive.rar", "archive.r00", "archive.r01"]


def test_multi_volume_7z_raises_phase7_message(tmp_path: Path) -> None:
    for name in ("vol.7z.001", "vol.7z.002"):
        (tmp_path / name).write_bytes(_7Z_MAGIC)
    with pytest.raises(UnsupportedFeatureError, match="Phase 7"):
        open_archive(tmp_path / "vol.7z.002", format=ArchiveFormat.SEVEN_Z)


def test_multi_volume_rar_raises_phase7_message(tmp_path: Path) -> None:
    for name in ("set.part1.rar", "set.part2.rar"):
        (tmp_path / name).write_bytes(_RAR_MAGIC)
    with pytest.raises(UnsupportedFeatureError, match="Phase 7"):
        open_archive(tmp_path / "set.part1.rar", format=ArchiveFormat.RAR)


def test_explicit_multi_source_tar_raises_not_multivolume(tmp_path: Path) -> None:
    a = tmp_path / "a.tar"
    b = tmp_path / "b.tar"
    for path in (a, b):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            info = tarfile.TarInfo("x.txt")
            info.size = 1
            tar.addfile(info, io.BytesIO(b"x"))
        path.write_bytes(buf.getvalue())
    with pytest.raises(UnsupportedFeatureError, match="does not support multi-volume"):
        open_archive([a, b])


def test_extract_non_utf8_tar_with_explicit_encoding(tmp_path: Path) -> None:
    archive = tmp_path / "names.tar"
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", encoding="utf-8") as tar:
        info = tarfile.TarInfo("caf\xe9.txt")
        info.size = 3
        tar.addfile(info, io.BytesIO(b"tea"))
    archive.write_bytes(buf.getvalue())

    dest = tmp_path / "out"
    extract(archive, dest, encoding="latin-1")
    assert (dest / "café.txt").read_bytes() == b"tea"


def test_single_member_sequence_equivalent_to_scalar(tmp_path: Path) -> None:
    path = tmp_path / "one.tar"
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        info = tarfile.TarInfo("only.txt")
        info.size = 2
        tar.addfile(info, io.BytesIO(b"ok"))
    path.write_bytes(buf.getvalue())

    with open_archive([path]) as ar:
        assert ar.read("only.txt") == b"ok"
