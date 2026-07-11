"""The declarative archive corpus: shapes, expectations, builders, and the cache.

Each :class:`CorpusEntry` describes an archive's contents *independently of any
format*: the members with their expected properties, plus which formats the entry is
built in. The conformance sweep (``test_corpus_sweep.py``) parametrizes over
(entry × format) and asserts the open/list/extract contract for every implemented
backend — the cross-format regression net (see ``testing-contract``: corpus
conformance sweep).

The shapes are ported from the DEV declarative corpus (``archivey-dev``
``tests/archivey/sample_archives.py`` @ 730275b7a755f8b5b8d08d3d4d9b267b5bdadb0d),
re-expressed in the v2 idiom; v1-API creation plumbing was deliberately not carried
over. 7z/RAR entries are present but inactive until the Phase 6 native readers land
(the sweep skips them via the registry's format-availability guard).

Generation is on-demand with a content-keyed cache under ``ARCHIVEY_TEST_CACHE``
(atomic ``os.replace`` writes, safe for parallel runs; no binaries are committed).
Bump :data:`GENERATOR_VERSION` when builder behavior changes so stale archives are
never reused.
"""

from __future__ import annotations

import hashlib
import io
import os
import random
import shutil
import stat as stat_mod
import subprocess
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass, replace
from pathlib import Path

from archivey.types import ArchiveFormat, ContainerFormat, MemberType, StreamFormat
from tests.conftest import ARCHIVEY_TEST_CACHE

# Bump when any builder's output changes, so cached archives regenerate.
# v2: the ZIP builder's Windows output changed (backslash names are now preserved).
GENERATOR_VERSION = 2


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Member:
    """One expected member: what the archive holds and what readers must report."""

    name: str
    type: MemberType = MemberType.FILE
    contents: bytes = b""
    link_target: str | None = None
    mode: int | None = None
    uid: int | None = None
    gid: int | None = None
    mtime: int = 1_600_000_001  # odd seconds: catches 2s-rounded DOS-time mishandling
    password: str | None = None
    zip_method: int | None = None  # per-member ZIP compress_type (compression entry)
    comment: str | None = None
    # Expected to be REJECTED by safe extraction (adversarial name/link). Listing must
    # still be faithful — the danger is visible on the name/target, never hidden.
    unsafe: bool = False

    # Expected resolved contents when following a link; None = no read check (e.g. a
    # link to a directory). For links whose *read* must fail, set expect_read_error.
    link_contents: bytes | None = None
    # Reading this member must raise an ArchiveyError (symlink cycle, dangling link).
    expect_read_error: bool = False


def F(name: str, contents: bytes, **kw: object) -> Member:
    return Member(name=name, type=MemberType.FILE, contents=contents, **kw)  # type: ignore[arg-type]


def D(name: str, **kw: object) -> Member:
    if not name.endswith("/"):
        name += "/"
    return Member(name=name, type=MemberType.DIRECTORY, **kw)  # type: ignore[arg-type]


def S(
    name: str, target: str, *, link_contents: bytes | None = None, **kw: object
) -> Member:
    return Member(
        name=name,
        type=MemberType.SYMLINK,
        link_target=target,
        link_contents=link_contents,
        **kw,  # type: ignore[arg-type]
    )


def H(
    name: str, target: str, *, link_contents: bytes | None = None, **kw: object
) -> Member:
    return Member(
        name=name,
        type=MemberType.HARDLINK,
        link_target=target,
        link_contents=link_contents,
        **kw,  # type: ignore[arg-type]
    )


@dataclass(frozen=True)
class CorpusEntry:
    """One corpus shape and the formats it is built in."""

    id: str
    members: tuple[Member, ...]
    formats: tuple[str, ...]
    archive_comment: str | None = None
    # Extra tools/packages the *builder* needs beyond the format's own reader deps.
    requires_binaries: tuple[str, ...] = ()
    notes: str = ""

    def with_formats(self, *formats: str) -> CorpusEntry:
        return replace(self, formats=formats)

    @property
    def passwords(self) -> tuple[str, ...]:
        seen: list[str] = []
        for m in self.members:
            if m.password is not None and m.password not in seen:
                seen.append(m.password)
        return tuple(seen)


# ---------------------------------------------------------------------------
# Format keys
# ---------------------------------------------------------------------------

# Corpus format key -> the ArchiveFormat the sweep gates availability on.
FORMAT_KEYS: dict[str, ArchiveFormat] = {
    "zip": ArchiveFormat.ZIP,
    "tar": ArchiveFormat.TAR,
    "tar.gz": ArchiveFormat.TAR_GZ,
    "tar.bz2": ArchiveFormat.TAR_BZ2,
    "tar.xz": ArchiveFormat.TAR_XZ,
    "tar.zst": ArchiveFormat.TAR_ZST,
    "tar.lz4": ArchiveFormat.TAR_LZ4,
    "tar.lz": ArchiveFormat(ContainerFormat.TAR, StreamFormat.LZIP),
    "tar.zz": ArchiveFormat(ContainerFormat.TAR, StreamFormat.ZLIB),
    "tar.br": ArchiveFormat(ContainerFormat.TAR, StreamFormat.BROTLI),
    "dir": ArchiveFormat.DIRECTORY,
    "iso": ArchiveFormat.ISO,
    "gz": ArchiveFormat.GZ,
    "gz-meta": ArchiveFormat.GZ,
    "bz2": ArchiveFormat.BZ2,
    "xz": ArchiveFormat.XZ,
    "zst": ArchiveFormat.ZST,
    "lz4": ArchiveFormat.LZ4,
    "lz": ArchiveFormat.LZIP,
    "zz": ArchiveFormat.ZLIB,
    "br": ArchiveFormat.BROTLI,
    # Inactive until the Phase 6 native readers register these formats; the sweep's
    # registry-driven availability guard skips them automatically until then.
    "7z": ArchiveFormat.SEVEN_Z,
    "rar": ArchiveFormat.RAR,
}

_ALL_TAR = (
    "tar",
    "tar.gz",
    "tar.bz2",
    "tar.xz",
    "tar.zst",
    "tar.lz4",
    "tar.lz",
    "tar.zz",
    "tar.br",
)
_SINGLE_FILE = ("gz", "bz2", "xz", "zst", "lz4", "lz", "zz", "br")


def _rand(size: int, seed: int) -> bytes:
    """Deterministic pseudo-random data (stable across runs -> stable cache keys)."""
    return random.Random(seed).randbytes(size)


# ---------------------------------------------------------------------------
# The corpus (shapes ported from DEV; see module docstring for provenance)
# ---------------------------------------------------------------------------

_BASIC = (
    F("file1.txt", b"Hello, world!"),
    D("subdir/"),
    F("empty_file.txt", b""),
    D("empty_subdir/"),
    F("subdir/file2.txt", b"Hello, universe!"),
    F("implicit_subdir/file3.txt", b"Hello there!"),
)

_ENCODING = (
    F("Español.txt", b"Hola, mundo!"),
    F("Català.txt", "Hola, món!".encode()),
    F("Português.txt", "Olá, mundo!".encode()),
    F("emoji_\U0001f600.txt", b"I'm happy"),
)

_SYMLINKS = (
    F("file1.txt", b"Hello, world!"),
    S("symlink_to_file1.txt", "file1.txt", link_contents=b"Hello, world!"),
    D("subdir/"),
    S("subdir/link_to_file1.txt", "../file1.txt", link_contents=b"Hello, world!"),
    S("subdir_link", "subdir"),
)

_SYMLINK_LOOP = (
    # The cycle: following any of these must raise (cycle detection), never hang.
    S("file1.txt", "file2.txt", expect_read_error=True),
    S("file2.txt", "file3.txt", expect_read_error=True),
    S("file3.txt", "file1.txt", expect_read_error=True),
    S("file4.txt", "file5.txt", link_contents=b"this is file 5"),
    F("file5.txt", b"this is file 5"),
)

_HARDLINKS = (
    F("file1.txt", b"Hello 1!"),
    F("subdir/file2.txt", b"Hello 2!"),
    H("subdir/hardlink_to_file1.txt", "file1.txt", link_contents=b"Hello 1!"),
    H("hardlink_to_file2.txt", "subdir/file2.txt", link_contents=b"Hello 2!"),
)

# TAR hardlink semantics: a link resolves to the latest *earlier* same-named member.
_HARDLINKS_DUP = (
    F("file1.txt", b"Old contents"),
    H("hardlink_to_file1_old.txt", "file1.txt", link_contents=b"Old contents"),
    F("file1.txt", b"New contents!"),
    H("hardlink_to_file1_new.txt", "file1.txt", link_contents=b"New contents!"),
    F("file1.txt", b"Newer contents!!"),
)

# Forward/chained links. v2 semantics (archive-reading, link resolution): a hardlink
# with no earlier same-named target falls back to a later one on a re-readable source,
# so the forward hardlink `b` resolves through `d` to `a_file` rather than dangling.
_HARDLINKS_FORWARD = (
    F("a_file.txt", b"Hello!"),
    H("b_forward_hardlink.txt", "d_hardlink.txt", link_contents=b"Hello!"),
    S("c_forward_symlink.txt", "d_hardlink.txt", link_contents=b"Hello!"),
    H("d_hardlink.txt", "a_file.txt", link_contents=b"Hello!"),
    H("e_double_hardlink.txt", "d_hardlink.txt", link_contents=b"Hello!"),
)

_PERMISSIONS = (
    F("standard.txt", b"Standard permissions.", mode=0o644),
    F("readonly.txt", b"Read-only permissions.", mode=0o444),
    F("executable.sh", b"#!/bin/sh\necho hi\n", mode=0o755),
    F("world_readable.txt", b"World readable permissions.", mode=0o666),
)

_ZIP_METHODS = (
    F("store.txt", b"I am stored\n" * 1000, zip_method=zipfile.ZIP_STORED),
    F("deflate.txt", b"I am deflated\n" * 1000, zip_method=zipfile.ZIP_DEFLATED),
    F("bzip2.txt", b"I am bzip'd\n" * 1000, zip_method=zipfile.ZIP_BZIP2),
    F("lzma.txt", b"I am lzma'd\n" * 1000, zip_method=zipfile.ZIP_LZMA),
)

_DUPLICATES = (
    F("file1.txt", b"Old contents"),
    F("file2.txt", b"Duplicate contents"),
    F("file1.txt", b"New contents!"),
    F("file2_dupe.txt", b"Duplicate contents"),
)

_LARGE = tuple(
    F(f"large{i}.txt", f"Large file #{i}\n".encode() + _rand(64_000, i))
    for i in (1, 2, 3)
)

# Adversarial names/links: listing stays faithful; safe extraction must reject each
# member marked unsafe (and only those). The backslash name is rejected because the
# universal check deliberately treats ``..`` between *either* separator as traversal.
_ADVERSARIAL_COMMON = (
    F("good.txt", b"good", uid=1001, gid=1002),
    F("/absfile.txt", b"abs", unsafe=True),
    F("../outside.txt", b"outside", unsafe=True),
    F("exec.sh", b"#!/bin/sh\n", mode=0o755),
    S("subdir/good_link.txt", "../good.txt", link_contents=b"good"),
    S("link_abs", "/etc/passwd", unsafe=True),
    S("link_outside", "../escape.txt", unsafe=True),
    F("backslash/..\\good.txt", b"not the same as good.txt", unsafe=True),
)
_ADVERSARIAL_TAR = _ADVERSARIAL_COMMON + (
    H("hardlink_outside", "../outside.txt", unsafe=True),
)

_ENC_SINGLE = (
    F("secret.txt", b"This is secret", password="password"),
    F("also_secret.txt", b"This is also secret", password="password"),
)
_ENC_MIXED = _ENC_SINGLE + (F("not_secret.txt", b"This is not secret"),)
_ENC_MULTI = (
    F("plain.txt", b"This is plain"),
    F("secret.txt", b"This is secret", password="password"),
    F("also_secret.txt", b"This is also secret", password="password"),
    F("very_secret.txt", b"This is very secret", password="very_secret_password"),
)

_SINGLE_CONTENT = b"This is a single test file for compression.\n"

CORPUS: tuple[CorpusEntry, ...] = (
    CorpusEntry("basic", _BASIC, ("zip", *_ALL_TAR, "dir", "iso", "7z", "rar")),
    CorpusEntry(
        "comments",
        (
            F("abc.txt", b"ABC", comment="Contains some letters"),
            F("subdir/123.txt", b"1234567890", comment="Contains some numbers"),
        ),
        ("zip", "rar"),
        archive_comment="This is a\nmulti-line comment",
    ),
    CorpusEntry("encoding", _ENCODING, ("zip", "tar", "tar.gz", "dir", "7z", "rar")),
    CorpusEntry("symlinks", _SYMLINKS, ("zip", "tar", "tar.gz", "dir", "7z", "rar")),
    CorpusEntry("symlink-loop", _SYMLINK_LOOP, ("zip", "tar")),
    CorpusEntry("hardlinks", _HARDLINKS, ("tar", "tar.gz", "rar")),
    CorpusEntry("hardlinks-duplicate", _HARDLINKS_DUP, ("tar",)),
    CorpusEntry("hardlinks-forward", _HARDLINKS_FORWARD, ("tar", "tar.gz")),
    CorpusEntry("permissions", _PERMISSIONS, ("zip", "tar", "dir", "7z")),
    CorpusEntry("zip-compression-methods", _ZIP_METHODS, ("zip",)),
    CorpusEntry("duplicates", _DUPLICATES, ("zip", "tar")),
    CorpusEntry("large", _LARGE, ("zip", "tar.gz", "tar.zst", "7z", "rar")),
    CorpusEntry("adversarial", _ADVERSARIAL_COMMON, ("zip",)),
    CorpusEntry("adversarial-tar", _ADVERSARIAL_TAR, ("tar", "tar.gz")),
    # Encrypted ZIPs are built with the 7z CLI (stdlib zipfile cannot write encryption).
    CorpusEntry(
        "encrypted", _ENC_SINGLE, ("zip", "7z", "rar"), requires_binaries=("7z",)
    ),
    CorpusEntry(
        "encrypted-mixed", _ENC_MIXED, ("zip", "7z", "rar"), requires_binaries=("7z",)
    ),
    CorpusEntry("encrypted-multi", _ENC_MULTI, ("zip",), requires_binaries=("7z",)),
    # Single-file compressors: exactly one member whose name is inferred from the
    # archive filename (see format-single-file-compressors); gz-meta stores FNAME+mtime.
    CorpusEntry("single-file", (F("payload.txt", _SINGLE_CONTENT),), _SINGLE_FILE),
    CorpusEntry(
        "single-file-meta", (F("stored_name.txt", _SINGLE_CONTENT),), ("gz-meta",)
    ),
)


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _tar_bytes(entry: CorpusEntry) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=tarfile.PAX_FORMAT) as tf:
        for m in entry.members:
            info = tarfile.TarInfo(
                m.name.rstrip("/") if m.type is MemberType.DIRECTORY else m.name
            )
            info.mtime = m.mtime
            info.mode = (
                m.mode
                if m.mode is not None
                else (0o755 if m.type is MemberType.DIRECTORY else 0o644)
            )
            if m.uid is not None:
                info.uid = m.uid
            if m.gid is not None:
                info.gid = m.gid
            if m.type is MemberType.DIRECTORY:
                info.type = tarfile.DIRTYPE
                tf.addfile(info)
            elif m.type is MemberType.SYMLINK:
                info.type = tarfile.SYMTYPE
                info.linkname = m.link_target or ""
                tf.addfile(info)
            elif m.type is MemberType.HARDLINK:
                info.type = tarfile.LNKTYPE
                info.linkname = m.link_target or ""
                tf.addfile(info)
            else:
                info.size = len(m.contents)
                tf.addfile(info, io.BytesIO(m.contents))
    return buf.getvalue()


def _compress(data: bytes, key: str) -> bytes:
    """Compress ``data`` with the outer codec named by the format key suffix."""
    import bz2 as bz2_mod
    import gzip as gzip_mod
    import lzma as lzma_mod
    import zlib as zlib_mod

    codec = key.split(".", 1)[1] if "." in key else key
    if codec in ("gz", "gz-meta"):
        return gzip_mod.compress(data, mtime=0)
    if codec == "bz2":
        return bz2_mod.compress(data)
    if codec == "xz":
        return lzma_mod.compress(data)
    if codec == "zz":
        return zlib_mod.compress(data)
    if codec == "zst":
        from tests.conftest import zstd_backend

        return zstd_backend().compress(data)
    if codec == "lz4":
        import lz4.frame

        return lz4.frame.compress(data)
    if codec == "br":
        import brotli

        return brotli.compress(data)
    if codec == "lz":
        from tests.streams_util import make_lzip_member

        return make_lzip_member(data)
    raise ValueError(f"no compressor for {key!r}")


def _zip_build(entry: CorpusEntry, path: Path) -> None:
    if entry.passwords:
        _zip_build_encrypted(entry, path)
        return
    with zipfile.ZipFile(path, "w") as zf:
        if entry.archive_comment:
            zf.comment = entry.archive_comment.encode()
        for m in entry.members:
            zi = zipfile.ZipInfo(date_time=(2020, 9, 13, 12, 26, 41))
            # Post-assign: ZipInfo.__init__ replaces os.sep on Windows (see zip_backslash/generate.py).
            zi.filename = m.name
            zi.create_system = (
                3  # force Unix so modes/symlinks are deterministic on all OSes
            )
            if m.type is MemberType.DIRECTORY:
                zi.external_attr = (0o40755 << 16) | 0x10
                zf.writestr(zi, b"")
            elif m.type is MemberType.SYMLINK:
                zi.external_attr = (stat_mod.S_IFLNK | 0o777) << 16
                zf.writestr(zi, (m.link_target or "").encode())
            else:
                mode = m.mode if m.mode is not None else 0o644
                zi.external_attr = (stat_mod.S_IFREG | mode) << 16
                zi.compress_type = (
                    m.zip_method if m.zip_method is not None else zipfile.ZIP_DEFLATED
                )
                if m.comment:
                    zi.comment = m.comment.encode()
                zf.writestr(zi, m.contents)


def _zip_build_encrypted(entry: CorpusEntry, path: Path) -> None:
    """Encrypted ZIP via the 7z CLI (one invocation per password group; plain last)."""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        groups: dict[str | None, list[Member]] = {}
        for m in entry.members:
            groups.setdefault(m.password, []).append(m)
        for password, members in groups.items():
            names = []
            for m in members:
                p = tmp / m.name
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(m.contents)
                names.append(m.name)
            cmd = ["7z", "a", "-tzip", str(path), *names, "-y"]
            if password is not None:
                cmd.append(f"-p{password}")
            subprocess.run(cmd, cwd=tmp, check=True, capture_output=True)
            for m in members:
                (tmp / m.name).unlink()


def _dir_build(entry: CorpusEntry, path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    for m in entry.members:
        target = path / m.name.rstrip("/")
        if m.type is MemberType.DIRECTORY:
            target.mkdir(parents=True, exist_ok=True)
        elif m.type is MemberType.SYMLINK:
            target.parent.mkdir(parents=True, exist_ok=True)
            os.symlink(m.link_target or "", target)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(m.contents)
            if m.mode is not None:
                os.chmod(target, m.mode)


def _iso_build(entry: CorpusEntry, path: Path) -> None:
    import pycdlib

    iso = pycdlib.PyCdlib()
    iso.new(interchange_level=3, rock_ridge="1.09", joliet=3)
    made_dirs: set[str] = set()

    def _ensure_dirs(rel: str) -> None:
        parts = rel.split("/")[:-1]
        for i in range(1, len(parts) + 1):
            joined = "/".join(parts[:i])
            if joined and joined not in made_dirs:
                made_dirs.add(joined)
                iso_path = "/" + "/".join(p.upper()[:8] for p in joined.split("/"))
                iso.add_directory(
                    iso_path, rr_name=parts[i - 1], joliet_path="/" + joined
                )

    counter = 0
    for m in entry.members:
        rel = m.name.rstrip("/")
        _ensure_dirs(m.name)
        if m.type is MemberType.DIRECTORY:
            if rel not in made_dirs:
                made_dirs.add(rel)
                iso_path = "/" + "/".join(p.upper()[:8] for p in rel.split("/"))
                iso.add_directory(
                    iso_path, rr_name=rel.split("/")[-1], joliet_path="/" + rel
                )
        else:
            counter += 1
            iso_dir = "/".join(p.upper()[:8] for p in rel.split("/")[:-1])
            iso_path = ("/" + iso_dir + "/" if iso_dir else "/") + f"F{counter}.TXT;1"
            iso.add_fp(
                io.BytesIO(m.contents),
                len(m.contents),
                iso_path,
                rr_name=rel.split("/")[-1],
                joliet_path="/" + rel,
            )
    out = io.BytesIO()
    iso.write_fp(out)
    iso.close()
    path.write_bytes(out.getvalue())


def _7z_build(entry: CorpusEntry, path: Path) -> None:  # pragma: no cover - Phase 6
    import py7zr

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        _dir_build(entry, tmp)
        with py7zr.SevenZipFile(path, "w") as zf:
            zf.writeall(tmp, arcname="")


def _rar_build(entry: CorpusEntry, path: Path) -> None:  # pragma: no cover - Phase 6
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        _dir_build(entry, tmp)
        subprocess.run(
            ["rar", "a", "-r", str(path), "."], cwd=tmp, check=True, capture_output=True
        )


def _single_file_build(entry: CorpusEntry, path: Path, key: str) -> None:
    import gzip as gzip_mod

    (member,) = entry.members
    if key == "gz-meta":
        buf = io.BytesIO()
        with gzip_mod.GzipFile(
            filename=member.name, mode="wb", fileobj=buf, mtime=member.mtime
        ) as gz:
            gz.write(member.contents)
        path.write_bytes(buf.getvalue())
        return
    path.write_bytes(_compress(member.contents, key))


def build_archive(entry: CorpusEntry, key: str, path: Path) -> None:
    """Build ``entry`` as format ``key`` at ``path`` (a file, or a directory for dir)."""
    if key == "zip":
        _zip_build(entry, path)
    elif key == "dir":
        _dir_build(entry, path)
    elif key == "iso":
        _iso_build(entry, path)
    elif key == "7z":
        _7z_build(entry, path)
    elif key == "rar":
        _rar_build(entry, path)
    elif key == "tar":
        path.write_bytes(_tar_bytes(entry))
    elif key.startswith("tar."):
        path.write_bytes(_compress(_tar_bytes(entry), key))
    elif key in ("gz", "gz-meta", *_SINGLE_FILE):
        _single_file_build(entry, path, key)
    else:
        raise ValueError(f"unknown corpus format key: {key!r}")


# Extra *builder-side* package requirements per format key (reader-side availability is
# gated separately via the registry). Import names, checked with importlib.
BUILDER_PACKAGES: dict[str, tuple[str, ...]] = {
    "tar.zst": (
        "_zstd_backend",
    ),  # sentinel: either compression.zstd or backports.zstd
    "zst": ("_zstd_backend",),
    "tar.lz4": ("lz4",),
    "lz4": ("lz4",),
    "tar.br": ("brotli",),
    "br": ("brotli",),
    "iso": ("pycdlib",),
    "7z": ("py7zr",),
}
BUILDER_BINARIES: dict[str, tuple[str, ...]] = {
    "rar": ("rar",),
}


# ---------------------------------------------------------------------------
# Generation cache (content-keyed, atomic, parallel-safe)
# ---------------------------------------------------------------------------


def _cache_key(entry: CorpusEntry, key: str) -> str:
    blob = f"v{GENERATOR_VERSION}|{key}|{entry!r}".encode()
    return hashlib.sha256(blob).hexdigest()[:24]


def _filename(entry: CorpusEntry, key: str) -> str:
    if key == "gz-meta":
        return f"{entry.id}.gz"
    return f"{entry.id}.{key}"


def corpus_archive_path(entry: CorpusEntry, key: str, tmp_path: Path) -> Path:
    """The built archive for (entry, key): from the cache, or generated now.

    ``dir`` entries are built fresh under ``tmp_path`` (a directory tree is cheap and
    caching one invites accidental mutation); file archives land in the cache keyed by
    the entry definition + generator version, written atomically so parallel test
    runs never see a half-written archive.
    """
    if key == "dir":
        target = tmp_path / entry.id
        build_archive(entry, key, target)
        return target

    cache_dir = Path(ARCHIVEY_TEST_CACHE) / _cache_key(entry, key)
    final = cache_dir / _filename(entry, key)
    if final.exists():
        return final
    cache_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=cache_dir, prefix=".building-")
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        tmp.unlink()  # builders create the file themselves
        build_archive(entry, key, tmp)
        os.replace(tmp, final)
    except BaseException:
        shutil.rmtree(cache_dir, ignore_errors=True)
        raise
    return final
