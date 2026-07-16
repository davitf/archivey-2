#!/usr/bin/env python3
"""Regenerate ``tests/fixtures/rar/`` archives with the RARLAB ``rar`` CLI.

Usage (from the repo root)::

    uv run python scripts/gen_rar_fixtures.py

Requires the RARLAB ``rar`` binary on ``PATH``. RAR 7 dropped ``-ma4`` (RAR4
writing); when the system ``rar`` cannot write RAR4, this script downloads a
pinned RAR 6.24 linux-x64 binary into the user cache and uses that for RAR4
(and, when selected, all) fixture builds.

Legacy RAR 1.5 / 2.x archives (``rar15-comment.rar``, ``rar202-comment-nopsw.rar``)
cannot be produced by modern ``rar`` and are left untouched — they were copied
from markokr/rarfile's ``test/files`` (ISC).
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = REPO_ROOT / "tests" / "fixtures" / "rar"

# Pinned RARLAB build that still supports ``-ma4``.
_RAR624_URL = "https://www.rarlab.com/rar/rarlinux-x64-624.tar.gz"
_RAR624_SHA256 = "88e22a8e84125c947637bbf28c746e338a0a63279d80f9f9d7373603875db1eb"
_RAR624_MEMBER = "rar/rar"

# Fixtures modern ``rar`` cannot recreate — do not delete/overwrite.
_LEGACY_KEEP = frozenset(
    {
        "rar15-comment.rar",
        "rar202-comment-nopsw.rar",
    }
)


@dataclass(frozen=True)
class _File:
    name: str  # archive-relative path; dirs end with /
    data: bytes | None = None  # None => directory
    link_target: str | None = None  # symlink target
    hardlink_to: str | None = None  # path of existing file to link


_BASIC: tuple[_File, ...] = (
    _File("file1.txt", b"Hello, world!"),
    _File("subdir/", None),
    _File("empty_file.txt", b""),
    _File("empty_subdir/", None),
    _File("subdir/file2.txt", b"Hello, universe!"),
    _File("implicit_subdir/file3.txt", b"Hello there!"),
)

_COMMENT: tuple[_File, ...] = (
    _File("abc.txt", b"ABC"),
    _File("subdir/", None),
    _File("subdir/123.txt", b"1234567890"),
)

_ENCRYPTION: tuple[_File, ...] = (
    _File("secret.txt", b"This is secret"),
    _File("also_secret.txt", b"This is also secret"),
)

_SYMLINKS: tuple[_File, ...] = (
    _File("file1.txt", b"Hello, world!"),
    _File("symlink_to_file1.txt", link_target="file1.txt"),
    _File("subdir/", None),
    _File("subdir/link_to_file1.txt", link_target="../file1.txt"),
    _File("subdir_link", link_target="subdir"),
    _File("subdir_link_with_slash", link_target="subdir/"),
)

_HARDLINKS: tuple[_File, ...] = (
    _File("file1.txt", b"Hello 1!"),
    _File("subdir/file2.txt", b"Hello 2!"),
    _File("subdir/hardlink_to_file1.txt", hardlink_to="file1.txt"),
    _File("hardlink_to_file2.txt", hardlink_to="subdir/file2.txt"),
)

# WinRAR ``-ver`` revisions of a single path (oldest → newest / live).
_FILE_VERSION_REVISIONS: tuple[bytes, ...] = (
    b"version-one",
    b"version-two!!",
    b"version-three!!!",
)

_FILE_VERSION_SOLID_V1 = b"AAA-v1"
_FILE_VERSION_SOLID_OTHER = b"BBB-payload"
_FILE_VERSION_SOLID_V2 = b"AAA-v2-longer"


def _run(cmd: Sequence[str], *, cwd: Path) -> None:
    env = os.environ.copy()
    env["TZ"] = "UTC"
    subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, env=env)


def _write_tree(root: Path, files: Iterable[_File]) -> list[str]:
    """Materialize members under ``root``; return rar add names in order."""
    names: list[str] = []
    for item in files:
        rel = item.name.rstrip("/")
        path = root / rel
        if item.data is None and item.link_target is None and item.hardlink_to is None:
            path.mkdir(parents=True, exist_ok=True)
            names.append(rel)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        if item.link_target is not None:
            if path.exists() or path.is_symlink():
                path.unlink()
            path.symlink_to(item.link_target)
        elif item.hardlink_to is not None:
            if path.exists() or path.is_symlink():
                path.unlink()
            os.link(root / item.hardlink_to, path)
        else:
            assert item.data is not None
            path.write_bytes(item.data)
        names.append(rel)
    return names


def _supports_ma4(rar_bin: Path) -> bool:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        (tmp / "x.txt").write_bytes(b"x")
        probe = tmp / "probe.rar"
        try:
            _run(
                [str(rar_bin), "a", "-idq", "-ma4", "-m0", str(probe), "x.txt"], cwd=tmp
            )
        except subprocess.CalledProcessError:
            return False
        return probe.is_file()


def _cache_dir() -> Path:
    raw = os.environ.get("XDG_CACHE_HOME")
    base = Path(raw) if raw else Path.home() / ".cache"
    return base / "archivey" / "rar-gen"


def _fetch_rar624() -> Path:
    """Download pinned RAR 6.24 into the user cache; return path to ``rar``."""
    dest_dir = _cache_dir() / "rarlinux-x64-624"
    rar_bin = dest_dir / "rar"
    if rar_bin.is_file() and os.access(rar_bin, os.X_OK) and _supports_ma4(rar_bin):
        return rar_bin

    dest_dir.mkdir(parents=True, exist_ok=True)
    tarball = dest_dir / "rarlinux-x64-624.tar.gz"
    print(f"Downloading {_RAR624_URL} -> {tarball}", file=sys.stderr)
    urllib.request.urlretrieve(_RAR624_URL, tarball)  # noqa: S310 - pinned vendor URL
    if _RAR624_SHA256:
        digest = hashlib.sha256(tarball.read_bytes()).hexdigest()
        if digest != _RAR624_SHA256:
            raise RuntimeError(
                f"SHA-256 mismatch for rar 6.24 tarball: {digest} != {_RAR624_SHA256}"
            )
    with tarfile.open(tarball, "r:gz") as tf:
        # Extract only the rar binary (and keep it flat as dest_dir/rar).
        member = tf.getmember(_RAR624_MEMBER)
        member.name = "rar"
        tf.extract(member, path=dest_dir, filter="data")
    rar_bin.chmod(rar_bin.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    if not _supports_ma4(rar_bin):
        raise RuntimeError(f"downloaded {rar_bin} still lacks -ma4")
    return rar_bin


def _resolve_rar(*, need_ma4: bool) -> Path:
    override = os.environ.get("ARCHIVEY_RAR_BIN")
    if override:
        path = Path(override)
        if not path.is_file():
            raise SystemExit(f"ARCHIVEY_RAR_BIN={override!r} is not a file")
        if need_ma4 and not _supports_ma4(path):
            raise SystemExit(f"ARCHIVEY_RAR_BIN={override!r} does not support -ma4")
        return path

    which = shutil.which("rar")
    if which is None:
        print(
            "No system rar; fetching RAR 6.24"
            + (" for -ma4 support" if need_ma4 else ""),
            file=sys.stderr,
        )
        return _fetch_rar624()

    system = Path(which)
    if need_ma4 and not _supports_ma4(system):
        print(
            f"System rar ({system}) lacks -ma4; fetching RAR 6.24 for RAR4 fixtures",
            file=sys.stderr,
        )
        return _fetch_rar624()
    return system


def _rar_a(
    rar_bin: Path,
    archive: Path,
    names: Sequence[str],
    *,
    cwd: Path,
    extra: Sequence[str] = (),
) -> None:
    if archive.exists():
        archive.unlink()
    # Drop sibling volumes if regenerating a multi-volume stem.
    stem = archive.name
    if stem.endswith(".rar"):
        base = stem[: -len(".rar")]
        for sibling in archive.parent.glob(f"{base}.part*.rar"):
            sibling.unlink()
        for sibling in archive.parent.glob(f"{base}.r[0-9][0-9]"):
            sibling.unlink()
    cmd = [str(rar_bin), "a", "-idq", "-oh", "-ol", *extra, str(archive), *names]
    _run(cmd, cwd=cwd)


def _rar_a_update(
    rar_bin: Path,
    archive: Path,
    names: Sequence[str],
    *,
    cwd: Path,
    extra: Sequence[str] = (),
) -> None:
    """Append/update members into an existing archive (keeps ``-ver`` history)."""
    cmd = [str(rar_bin), "a", "-idq", "-oh", "-ol", *extra, str(archive), *names]
    _run(cmd, cwd=cwd)


def _build_file_version(
    rar_bin: Path,
    out: Path,
    revisions: Sequence[bytes],
    *,
    extra: Sequence[str] = (),
    path_name: str = "file.txt",
) -> None:
    """Write ``path_name`` through ``revisions`` with ``-ver`` (last = live)."""
    if out.exists():
        out.unlink()
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        target = root / path_name
        target.parent.mkdir(parents=True, exist_ok=True)
        extras = [*extra, "-ver"]
        for i, payload in enumerate(revisions):
            target.write_bytes(payload)
            if i == 0:
                _rar_a(rar_bin, out, [path_name], cwd=root, extra=extras)
            else:
                _rar_a_update(rar_bin, out, [path_name], cwd=root, extra=extras)
    print(f"wrote {out.relative_to(REPO_ROOT)}")


def _build_file_version_solid(rar_bin: Path, out: Path) -> None:
    """Solid RAR5 with ``a.txt`` history + a second payload for demux order checks."""
    if out.exists():
        out.unlink()
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "a.txt").write_bytes(_FILE_VERSION_SOLID_V1)
        (root / "b.txt").write_bytes(_FILE_VERSION_SOLID_OTHER)
        extras = ("-s", "-m3", "-ver")
        _rar_a(rar_bin, out, ["a.txt", "b.txt"], cwd=root, extra=extras)
        (root / "a.txt").write_bytes(_FILE_VERSION_SOLID_V2)
        _rar_a_update(rar_bin, out, ["a.txt"], cwd=root, extra=extras)
    print(f"wrote {out.relative_to(REPO_ROOT)}")


def generate_all(*, rar5_bin: Path, rar4_bin: Path, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    def build(
        rar_bin: Path,
        out_name: str,
        files: Sequence[_File],
        *,
        extra: Sequence[str] = (),
        comment: str | None = None,
    ) -> None:
        out = out_dir / out_name
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            names = _write_tree(root, files)
            extras = list(extra)
            if comment is not None:
                cpath = root / ".archive_comment.txt"
                cpath.write_text(comment, encoding="utf-8")
                extras.append(f"-z{cpath}")
            _rar_a(rar_bin, out, names, cwd=root, extra=extras)
        print(f"wrote {out.relative_to(REPO_ROOT)}")

    # --- RAR5 ---
    build(rar5_bin, "basic_nonsolid__.rar", _BASIC, extra=("-m0",))
    build(rar5_bin, "basic_solid__.rar", _BASIC, extra=("-s", "-m3"))
    build(
        rar5_bin,
        "comment__.rar",
        _COMMENT,
        extra=("-m0",),
        comment="This is a\nmulti-line comment",
    )
    build(
        rar5_bin,
        "encryption__.rar",
        _ENCRYPTION,
        extra=("-m3", "-ppassword"),
    )
    build(
        rar5_bin,
        "encrypted_header__.rar",
        _BASIC,
        extra=("-m3", "-hpheader_password"),
    )
    build(
        rar5_bin,
        "symlinks_solid__.rar",
        _SYMLINKS,
        extra=("-s", "-m3"),
    )
    build(
        rar5_bin,
        "hardlinks_solid__.rar",
        _HARDLINKS,
        extra=("-s", "-m3"),
    )
    build(
        rar5_bin,
        "stored_m0.rar",
        (_File("store.txt", b"stored payload"),),
        extra=("-m0",),
    )
    build(
        rar5_bin,
        "blake2sp.rar",
        (_File("store.txt", b"stored payload"),),
        extra=("-m0", "-htb"),
    )
    build(
        rar5_bin,
        "encryption_blake2sp.rar",
        (_File("store.txt", b"stored payload"),),
        extra=("-m0", "-htb", "-ppassword"),
    )
    _build_file_version(
        rar5_bin,
        out_dir / "file_version__.rar",
        _FILE_VERSION_REVISIONS,
        extra=("-m0",),
    )
    _build_file_version_solid(rar5_bin, out_dir / "file_version_solid__.rar")

    # Multi-volume: 1600-byte payload, 900-byte volumes → two parts.
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "payload.bin").write_bytes(b"ABCDEFGH" * 200)
        out = out_dir / "tinyvol.rar"
        _rar_a(
            rar5_bin,
            out,
            ["payload.bin"],
            cwd=root,
            extra=("-m0", "-v900b"),
        )
        part1 = out_dir / "tinyvol.part1.rar"
        part2 = out_dir / "tinyvol.part2.rar"
        if not part1.is_file() or not part2.is_file():
            raise RuntimeError(f"expected {part1.name} and {part2.name}")
        if out.is_file():
            out.unlink()
        print(f"wrote {part1.relative_to(REPO_ROOT)}")
        print(f"wrote {part2.relative_to(REPO_ROOT)}")

    # --- RAR4 (needs -ma4) ---
    # Classic extension volumes (name.rar + name.r00…): RAR4-only via -vn.
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "payload.bin").write_bytes(b"ABCDEFGH" * 200)
        out = out_dir / "tinyvol_rnn.rar"
        _rar_a(
            rar4_bin,
            out,
            ["payload.bin"],
            cwd=root,
            extra=("-ma4", "-m0", "-vn", "-v900b"),
        )
        vol0 = out_dir / "tinyvol_rnn.rar"
        vol1 = out_dir / "tinyvol_rnn.r00"
        if not vol0.is_file() or not vol1.is_file():
            raise RuntimeError(f"expected {vol0.name} and {vol1.name}")
        print(f"wrote {vol0.relative_to(REPO_ROOT)}")
        print(f"wrote {vol1.relative_to(REPO_ROOT)}")

    build(rar4_bin, "basic_nonsolid__rar4.rar", _BASIC, extra=("-ma4", "-m0"))
    build(rar4_bin, "basic_solid__rar4.rar", _BASIC, extra=("-ma4", "-s", "-m3"))
    build(
        rar4_bin,
        "encryption__rar4.rar",
        _ENCRYPTION,
        extra=("-ma4", "-m3", "-ppassword"),
    )
    build(
        rar4_bin,
        "encrypted_header__rar4.rar",
        _BASIC,
        extra=("-ma4", "-m3", "-hpheader_password"),
    )
    build(
        rar4_bin,
        "symlinks_solid__rar4.rar",
        _SYMLINKS,
        extra=("-ma4", "-s", "-m3"),
    )
    _build_file_version(
        rar4_bin,
        out_dir / "file_version__rar4.rar",
        _FILE_VERSION_REVISIONS,
        extra=("-ma4", "-m0"),
    )

    kept = ", ".join(sorted(_LEGACY_KEEP))
    print(f"left legacy fixtures untouched: {kept}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=OUT_DIR,
        help=f"output directory (default: {OUT_DIR})",
    )
    args = parser.parse_args(argv)
    out_dir = args.out_dir.resolve()

    rar5 = _resolve_rar(need_ma4=False)
    if _supports_ma4(rar5):
        rar4 = rar5
    else:
        rar4 = _resolve_rar(need_ma4=True)

    print(f"rar5 binary: {rar5}", file=sys.stderr)
    print(f"rar4 binary: {rar4}", file=sys.stderr)
    generate_all(rar5_bin=rar5, rar4_bin=rar4, out_dir=out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
