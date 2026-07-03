"""Safe-extraction tests: universal filters, policy transforms, BombTracker, and the
ExtractionCoordinator across ZIP / TAR / directory backends.

Covers the ``safe-extraction`` capability and the ``testing-contract`` adversarial
scenarios (path traversal, symlink escape, zip bomb, entry-count bomb).
"""

from __future__ import annotations

import errno
import io
import os
import tarfile
import zipfile
from pathlib import Path

import pytest

from archivey import (
    ExtractionPolicy,
    ExtractionStatus,
    OnError,
    OverwritePolicy,
    extract,
    open_archive,
)
from archivey.exceptions import (
    ExtractionError,
    PathTraversalError,
    SpecialFileError,
    SymlinkEscapeError,
)
from archivey.internal.extraction import BombTracker, _AlwaysStopExtractionError
from archivey.internal.filters import (
    POLICY_TRANSFORMS,
    check_universal,
    transform_standard,
    transform_strict,
)
from archivey.types import ArchiveMember, MemberType

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _member(
    name: str,
    *,
    raw: bytes | None = None,
    type: MemberType = MemberType.FILE,
    mode: int | None = None,
    link_target: str | None = None,
    uid: int | None = None,
    gid: int | None = None,
) -> ArchiveMember:
    return ArchiveMember(
        type=type,
        name=name,
        raw_name=raw if raw is not None else name.encode(),
        mode=mode,
        link_target=link_target,
        uid=uid,
        gid=gid,
    )


def _tar_bytes(specs: list[tuple], *, mode: str = "w") -> bytes:
    """Build a tar from (kind, name, payload) specs.

    kind: "file" (payload=bytes), "sym"/"hard" (payload=linkname), "dir" (payload=None).
    """
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode=mode) as t:
        for kind, name, payload in specs:
            if kind == "file":
                info = tarfile.TarInfo(name)
                info.size = len(payload)
                info.mode = 0o644
                t.addfile(info, io.BytesIO(payload))
            elif kind == "dir":
                info = tarfile.TarInfo(name)
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
                t.addfile(info)
            elif kind == "sym":
                info = tarfile.TarInfo(name)
                info.type = tarfile.SYMTYPE
                info.linkname = payload
                t.addfile(info)
            elif kind == "hard":
                info = tarfile.TarInfo(name)
                info.type = tarfile.LNKTYPE
                info.linkname = payload
                t.addfile(info)
    return buf.getvalue()


def _write_zip(path: Path, entries: dict[str, bytes], mode: int | None = None) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in entries.items():
            if mode is not None:
                info = zipfile.ZipInfo(name)
                info.create_system = 3  # Unix
                info.external_attr = mode << 16
                z.writestr(info, data)
            else:
                z.writestr(name, data)


# ---------------------------------------------------------------------------
# check_universal — universal path-safety (task 1.4 / testing-contract)
# ---------------------------------------------------------------------------


# Names are faithful now (normalization keeps a leading "/" and every ".."), so the
# check runs on member.name directly.
@pytest.mark.parametrize(
    "name",
    [
        "../evil",  # escaping traversal
        "../../etc/passwd",
        "foo/../bar",  # internal traversal is also rejected under the default RAISE
        "/etc/x",  # absolute
    ],
)
def test_check_universal_rejects_traversal(tmp_path: Path, name: str) -> None:
    with pytest.raises(PathTraversalError):
        check_universal(_member(name), tmp_path)


def test_check_universal_rejects_null_byte(tmp_path: Path) -> None:
    with pytest.raises(PathTraversalError):
        check_universal(_member("a\x00b"), tmp_path)


def test_check_universal_rejects_special_file(tmp_path: Path) -> None:
    with pytest.raises(SpecialFileError):
        check_universal(_member("dev", type=MemberType.OTHER), tmp_path)


def test_check_universal_rejects_symlink_escape(tmp_path: Path) -> None:
    m = _member("link", type=MemberType.SYMLINK, link_target="../../etc/passwd")
    with pytest.raises(SymlinkEscapeError):
        check_universal(m, tmp_path)


def test_check_universal_allows_internal_symlink(tmp_path: Path) -> None:
    m = _member("sub/link", type=MemberType.SYMLINK, link_target="../file.txt")
    check_universal(m, tmp_path)  # resolves to <dest>/file.txt, inside root


def test_check_universal_enforced_under_trusted(tmp_path: Path) -> None:
    # Universal checks are non-bypassable, even under TRUSTED.
    with pytest.raises(PathTraversalError):
        check_universal(_member("../evil"), tmp_path)
    # ...and TRUSTED's transform itself is identity (path safety is separate).
    m = _member("ok", mode=0o777)
    assert POLICY_TRANSFORMS[ExtractionPolicy.TRUSTED](m).mode == 0o777


# ---------------------------------------------------------------------------
# Policy transforms (task 1.4)
# ---------------------------------------------------------------------------


def test_strict_strips_execute_and_normalizes() -> None:
    out = transform_strict(_member("f", mode=0o755))
    assert out.mode == 0o644  # execute stripped, normalized
    assert out.uid is None and out.gid is None


def test_strict_dir_normalized_to_755() -> None:
    out = transform_strict(_member("d/", type=MemberType.DIRECTORY, mode=0o777))
    assert out.mode == 0o755


def test_strict_strips_setuid() -> None:
    out = transform_strict(_member("f", mode=0o4755))
    assert out.mode & 0o7000 == 0
    assert out.mode == 0o644


def test_standard_preserves_execute() -> None:
    out = transform_standard(_member("f", mode=0o755))
    assert out.mode == 0o755  # execute kept
    out2 = transform_standard(_member("f", mode=0o4755))
    assert out2.mode == 0o755  # only setuid stripped


def test_strict_mode_none_defaults() -> None:
    assert transform_strict(_member("f")).mode == 0o644
    assert transform_strict(_member("d/", type=MemberType.DIRECTORY)).mode == 0o755


# ---------------------------------------------------------------------------
# BombTracker (task 2.3)
# ---------------------------------------------------------------------------


def test_cumulative_byte_limit() -> None:
    t = BombTracker(max_bytes=100, max_ratio=1e9)
    t.start_member(_member("f"))
    t.count(60)
    with pytest.raises(_AlwaysStopExtractionError):
        t.count(50)  # crosses 100


def test_per_member_ratio() -> None:
    t = BombTracker(max_bytes=10**12, max_ratio=2.0, ratio_activation_threshold=10)
    t.start_member(_member("f").replace(compressed_size=1))
    with pytest.raises(ExtractionError) as ei:
        t.count(20)  # member_bytes 20 > floor 10, ratio 20:1 > 2
    assert not isinstance(ei.value, _AlwaysStopExtractionError)  # skippable


def test_ratio_activation_threshold_false_positive_guard() -> None:
    t = BombTracker(max_bytes=10**12, max_ratio=2.0, ratio_activation_threshold=100)
    t.start_member(_member("f").replace(compressed_size=1))
    t.count(50)  # below floor 100 -> no trip despite 50:1


def test_archive_wide_ratio() -> None:
    t = BombTracker(
        max_bytes=10**12,
        max_ratio=2.0,
        ratio_activation_threshold=10,
        compressed_source_size=10,
    )
    t.start_member(_member("f"))  # no per-member compressed_size
    with pytest.raises(_AlwaysStopExtractionError):
        t.count(30)  # total 30 > floor 10, 30/10 = 3 > 2


def test_ratio_skipped_when_denominators_unknown() -> None:
    t = BombTracker(max_bytes=10**12, max_ratio=2.0, ratio_activation_threshold=1)
    t.start_member(_member("f"))  # compressed_size None, no source size
    t.count(10_000)  # no ratio applies; cumulative under limit


def test_max_entries_guard() -> None:
    t = BombTracker(max_bytes=10**12, max_ratio=1e9, max_entries=2)
    t.start_member(_member("a"))
    t.start_member(_member("b"))
    with pytest.raises(_AlwaysStopExtractionError):
        t.start_member(_member("c"))


def test_entry_count_independent_of_bytes() -> None:
    t = BombTracker(max_bytes=10**12, max_ratio=1e9, max_entries=1)
    t.start_member(_member("a"))
    with pytest.raises(_AlwaysStopExtractionError):
        t.start_member(_member("b"))  # trips on count, not bytes


# ---------------------------------------------------------------------------
# Coordinator — ZIP vertical slice (tasks 3.x, 5.x)
# ---------------------------------------------------------------------------


def test_extract_zip_basic(tmp_path: Path) -> None:
    src = tmp_path / "a.zip"
    _write_zip(src, {"hello.txt": b"hi", "dir/nested.txt": b"deep"})
    dest = tmp_path / "out"
    results = extract(src, dest)
    assert (dest / "hello.txt").read_bytes() == b"hi"
    assert (dest / "dir" / "nested.txt").read_bytes() == b"deep"
    assert {r.status for r in results} == {ExtractionStatus.EXTRACTED}


# Windows has no Unix permission bits — os.chmod there can only toggle the read-only
# flag, so a regular file always reads back ~0o666 with no execute/group/other bits. The
# permission-normalization behavior is a POSIX concept, so assert it only on POSIX.
_posix_perms = pytest.mark.skipif(
    os.name != "posix", reason="Unix permission bits (chmod) are a no-op on Windows"
)


@_posix_perms
def test_extract_zip_strict_normalizes_mode(tmp_path: Path) -> None:
    src = tmp_path / "m.zip"
    _write_zip(src, {"x.sh": b"#!/bin/sh"}, mode=0o777)
    dest = tmp_path / "out"
    extract(src, dest, policy=ExtractionPolicy.STRICT)
    assert (dest / "x.sh").stat().st_mode & 0o777 == 0o644


@_posix_perms
def test_extract_zip_standard_keeps_execute(tmp_path: Path) -> None:
    src = tmp_path / "m.zip"
    _write_zip(src, {"x.sh": b"#!/bin/sh"}, mode=0o755)
    dest = tmp_path / "out"
    extract(src, dest, policy=ExtractionPolicy.STANDARD)
    assert (dest / "x.sh").stat().st_mode & 0o111  # execute preserved


def test_subset_selection(tmp_path: Path) -> None:
    src = tmp_path / "a.zip"
    _write_zip(src, {"a.txt": b"a", "b.txt": b"b", "c.txt": b"c"})
    dest = tmp_path / "out"
    with open_archive(src) as r:
        results = r.extract_all(dest, members=["a.txt", "c.txt"])
    assert (dest / "a.txt").exists() and (dest / "c.txt").exists()
    assert not (dest / "b.txt").exists()
    assert len(results) == 2


def test_user_filter_rename(tmp_path: Path) -> None:
    src = tmp_path / "a.zip"
    _write_zip(src, {"a.txt": b"a"})
    dest = tmp_path / "out"

    def rename(m: ArchiveMember) -> ArchiveMember:
        return m.replace(name="renamed.txt")

    with open_archive(src) as r:
        r.extract_all(dest, filter=rename)
    assert (dest / "renamed.txt").read_bytes() == b"a"


def test_user_filter_skip(tmp_path: Path) -> None:
    src = tmp_path / "a.zip"
    _write_zip(src, {"a.txt": b"a", "b.txt": b"b"})
    dest = tmp_path / "out"

    def skip_b(m: ArchiveMember) -> ArchiveMember | None:
        return None if m.name == "b.txt" else m

    with open_archive(src) as r:
        r.extract_all(dest, filter=skip_b)
    assert (dest / "a.txt").exists()
    assert not (dest / "b.txt").exists()


# ---------------------------------------------------------------------------
# Overwrite policy (task 3.x)
# ---------------------------------------------------------------------------


def test_overwrite_error(tmp_path: Path) -> None:
    src = tmp_path / "a.zip"
    _write_zip(src, {"a.txt": b"new"})
    dest = tmp_path / "out"
    dest.mkdir()
    (dest / "a.txt").write_bytes(b"old")
    with pytest.raises(ExtractionError):
        extract(src, dest, overwrite=OverwritePolicy.ERROR)
    assert (dest / "a.txt").read_bytes() == b"old"  # untouched


def test_overwrite_skip(tmp_path: Path) -> None:
    src = tmp_path / "a.zip"
    _write_zip(src, {"a.txt": b"new"})
    dest = tmp_path / "out"
    dest.mkdir()
    (dest / "a.txt").write_bytes(b"old")
    results = extract(src, dest, overwrite=OverwritePolicy.SKIP)
    assert (dest / "a.txt").read_bytes() == b"old"
    assert results[0].status is ExtractionStatus.SKIPPED


def test_overwrite_replace(tmp_path: Path) -> None:
    src = tmp_path / "a.zip"
    _write_zip(src, {"a.txt": b"new"})
    dest = tmp_path / "out"
    dest.mkdir()
    (dest / "a.txt").write_bytes(b"old")
    extract(src, dest, overwrite=OverwritePolicy.REPLACE)
    assert (dest / "a.txt").read_bytes() == b"new"


def test_replace_symlink_no_write_through(tmp_path: Path) -> None:
    # A planted symlink at the destination path must be unlinked, not written through.
    src = tmp_path / "a.zip"
    _write_zip(src, {"a.txt": b"new"})
    dest = tmp_path / "out"
    dest.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"secret")
    os.symlink(outside, dest / "a.txt")
    extract(src, dest, overwrite=OverwritePolicy.REPLACE)
    assert (dest / "a.txt").read_bytes() == b"new"
    assert not (dest / "a.txt").is_symlink()
    assert outside.read_bytes() == b"secret"  # target never written through


def test_dangling_symlink_counts_as_existing(tmp_path: Path) -> None:
    src = tmp_path / "a.zip"
    _write_zip(src, {"a.txt": b"new"})
    dest = tmp_path / "out"
    dest.mkdir()
    os.symlink(tmp_path / "does-not-exist", dest / "a.txt")  # dangling
    with pytest.raises(ExtractionError):
        extract(src, dest, overwrite=OverwritePolicy.ERROR)


# ---------------------------------------------------------------------------
# OnError policy (task 3.5)
# ---------------------------------------------------------------------------


def test_on_error_continue_records_rejected(tmp_path: Path) -> None:
    # A member with a hostile stored name is rejected; CONTINUE keeps going.
    src = tmp_path / "a.zip"
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("../evil.txt", b"bad")
        z.writestr("good.txt", b"good")
    dest = tmp_path / "out"
    results = extract(src, dest, on_error=OnError.CONTINUE)
    statuses = {r.member.name: r.status for r in results}
    assert ExtractionStatus.REJECTED in statuses.values()
    assert (dest / "good.txt").read_bytes() == b"good"


def test_on_error_stop_raises(tmp_path: Path) -> None:
    src = tmp_path / "a.zip"
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("../evil.txt", b"bad")
    dest = tmp_path / "out"
    with pytest.raises(PathTraversalError):
        extract(src, dest, on_error=OnError.STOP)


# ---------------------------------------------------------------------------
# Bomb limits end-to-end (task 6.2)
# ---------------------------------------------------------------------------


def test_cumulative_bomb_halts_even_under_continue(tmp_path: Path) -> None:
    src = tmp_path / "big.zip"
    _write_zip(src, {"a.txt": b"x" * 5000, "b.txt": b"y" * 5000})
    dest = tmp_path / "out"
    with pytest.raises(ExtractionError):
        extract(src, dest, max_extracted_bytes=6000, on_error=OnError.CONTINUE)


def test_zip_bomb_per_member_ratio(tmp_path: Path) -> None:
    # Highly compressible payload above the activation threshold trips the ratio.
    src = tmp_path / "bomb.zip"
    payload = b"\x00" * (8 * 1024 * 1024)  # 8 MiB of zeros -> tiny compressed
    _write_zip(src, {"bomb.bin": payload})
    dest = tmp_path / "out"
    with pytest.raises(ExtractionError):
        extract(src, dest, max_ratio=10.0, ratio_activation_threshold=1024)


def test_entry_count_bomb(tmp_path: Path) -> None:
    src = tmp_path / "many.zip"
    _write_zip(src, {f"f{i}.txt": b"" for i in range(50)})
    dest = tmp_path / "out"
    with pytest.raises(ExtractionError):
        extract(src, dest, max_entries=10, on_error=OnError.CONTINUE)


# ---------------------------------------------------------------------------
# Progress callback
# ---------------------------------------------------------------------------


def test_on_progress_called_per_member(tmp_path: Path) -> None:
    src = tmp_path / "a.zip"
    _write_zip(src, {"a.txt": b"a", "b.txt": b"b"})
    dest = tmp_path / "out"
    seen: list[str] = []
    extract(src, dest, on_progress=lambda p: seen.append(p.member.name))
    assert set(seen) == {"a.txt", "b.txt"}


# ---------------------------------------------------------------------------
# TAR symlinks (tasks 3.4, 4.5)
# ---------------------------------------------------------------------------


def test_tar_symlink_created(tmp_path: Path) -> None:
    src = tmp_path / "a.tar"
    src.write_bytes(_tar_bytes([("file", "file.txt", b"data"), ("sym", "link", "file.txt")]))
    dest = tmp_path / "out"
    extract(src, dest)
    link = dest / "link"
    assert link.is_symlink()
    assert os.readlink(link) == "file.txt"
    assert link.read_bytes() == b"data"


def test_tar_symlink_dangling_to_filtered_target(tmp_path: Path) -> None:
    # A symlink is target-independent: it is created even when its target is excluded,
    # and may dangle. No copy, no error.
    src = tmp_path / "a.tar"
    src.write_bytes(_tar_bytes([("file", "file.txt", b"data"), ("sym", "link", "file.txt")]))
    dest = tmp_path / "out"
    with open_archive(src) as r:
        r.extract_all(dest, members=["link"])  # exclude file.txt
    link = dest / "link"
    assert link.is_symlink()
    assert not (dest / "file.txt").exists()
    assert not link.exists()  # dangles (target excluded)


def test_tar_symlink_escape_rejected(tmp_path: Path) -> None:
    src = tmp_path / "a.tar"
    src.write_bytes(_tar_bytes([("sym", "evil", "../../etc/passwd")]))
    dest = tmp_path / "out"
    with pytest.raises(SymlinkEscapeError):
        extract(src, dest)
    assert not (dest / "evil").exists()


def test_tar_symlink_escape_continue_records_rejected(tmp_path: Path) -> None:
    src = tmp_path / "a.tar"
    src.write_bytes(
        _tar_bytes([("sym", "evil", "../../etc/passwd"), ("file", "ok.txt", b"ok")])
    )
    dest = tmp_path / "out"
    results = extract(src, dest, on_error=OnError.CONTINUE)
    statuses = {r.member.name: r.status for r in results}
    assert statuses["evil"] is ExtractionStatus.REJECTED
    assert (dest / "ok.txt").read_bytes() == b"ok"


# ---------------------------------------------------------------------------
# TAR hardlinks (tasks 4.1, 4.3)
# ---------------------------------------------------------------------------


def test_tar_hardlink_shares_inode(tmp_path: Path) -> None:
    src = tmp_path / "a.tar"
    src.write_bytes(
        _tar_bytes([("file", "file.txt", b"data"), ("hard", "hard.txt", "file.txt")])
    )
    dest = tmp_path / "out"
    extract(src, dest)
    assert (dest / "file.txt").read_bytes() == b"data"
    assert (dest / "hard.txt").read_bytes() == b"data"
    assert os.path.samefile(dest / "file.txt", dest / "hard.txt")


def test_tar_hardlink_orphan_recovered_seekable(tmp_path: Path) -> None:
    # A filter excludes the source but keeps the link; a seekable source recovers the
    # content in a second pass, writing it to the link path (source name never created).
    src = tmp_path / "a.tar"
    src.write_bytes(
        _tar_bytes([("file", "file.txt", b"data"), ("hard", "hard.txt", "file.txt")])
    )
    dest = tmp_path / "out"

    def drop_source(m: ArchiveMember) -> ArchiveMember | None:
        return None if m.name == "file.txt" else m

    with open_archive(src) as r:
        results = r.extract_all(dest, filter=drop_source)
    assert (dest / "hard.txt").read_bytes() == b"data"
    assert not (dest / "file.txt").exists()
    assert [r.status for r in results] == [ExtractionStatus.EXTRACTED]


def test_tar_hardlink_orphan_forward_only_onerror(tmp_path: Path) -> None:
    from tests.streams_util import NonSeekableBytesIO

    raw = _tar_bytes([("file", "file.txt", b"data"), ("hard", "hard.txt", "file.txt")])

    def drop_source(m: ArchiveMember) -> ArchiveMember | None:
        return None if m.name == "file.txt" else m

    # STOP: raises on the unrecoverable orphan.
    dest = tmp_path / "out_stop"
    with open_archive(NonSeekableBytesIO(raw), streaming=True) as r:
        with pytest.raises(ExtractionError):
            r.extract_all(dest, filter=drop_source)

    # CONTINUE: records FAILED, does not raise.
    dest2 = tmp_path / "out_continue"
    with open_archive(NonSeekableBytesIO(raw), streaming=True) as r:
        results = r.extract_all(dest2, filter=drop_source, on_error=OnError.CONTINUE)
    statuses = {r.member.name: r.status for r in results}
    assert statuses["hard.txt"] is ExtractionStatus.FAILED
    assert not (dest2 / "hard.txt").exists()


# ---------------------------------------------------------------------------
# Seekable + non-seekable compressed TAR (tasks 5.3, 6.3)
# ---------------------------------------------------------------------------


def test_seekable_targz_extract(tmp_path: Path) -> None:
    src = tmp_path / "a.tar.gz"
    src.write_bytes(_tar_bytes([("file", "a.txt", b"hello"), ("dir", "d", None)], mode="w:gz"))
    dest = tmp_path / "out"
    extract(src, dest)
    assert (dest / "a.txt").read_bytes() == b"hello"
    assert (dest / "d").is_dir()


def test_seekable_targz_archive_wide_ratio(tmp_path: Path) -> None:
    # A small .tar.gz whose decompressed output far exceeds max_ratio * file_size trips
    # the archive-wide ratio (member compressed_size is unknown for TAR).
    src = tmp_path / "bomb.tar.gz"
    payload = b"\x00" * (4 * 1024 * 1024)
    src.write_bytes(_tar_bytes([("file", "z.bin", payload)], mode="w:gz"))
    dest = tmp_path / "out"
    with pytest.raises(ExtractionError):
        extract(src, dest, max_ratio=5.0, ratio_activation_threshold=1024)


def test_non_seekable_targz_extract(tmp_path: Path) -> None:
    from tests.streams_util import NonSeekableBytesIO

    raw = _tar_bytes([("file", "a.txt", b"hello"), ("file", "b.txt", b"world")], mode="w:gz")
    dest = tmp_path / "out"
    with open_archive(NonSeekableBytesIO(raw), streaming=True) as r:
        r.extract_all(dest)
    assert (dest / "a.txt").read_bytes() == b"hello"
    assert (dest / "b.txt").read_bytes() == b"world"


def test_cross_device_hardlink_reuses_sibling(tmp_path: Path, monkeypatch) -> None:
    # B->A lands "cross-device" (os.link against A fails EXDEV -> copy). A later C->A on
    # the same device as B is created with os.link(B, C), reusing the sibling copy.
    src = tmp_path / "a.tar"
    src.write_bytes(
        _tar_bytes(
            [
                ("file", "A.txt", b"payload"),
                ("hard", "B.txt", "A.txt"),
                ("hard", "C.txt", "A.txt"),
            ]
        )
    )
    dest = tmp_path / "out"
    real_link = os.link

    def fake_link(source, target, *a, **k):
        # Every attempt against A's own copy is "cross-device"; links to B's copy work.
        if os.fspath(source).endswith("A.txt"):
            raise OSError(errno.EXDEV, "cross-device")
        return real_link(source, target, *a, **k)

    monkeypatch.setattr(os, "link", fake_link)
    extract(src, dest)

    assert (dest / "B.txt").read_bytes() == b"payload"
    assert (dest / "C.txt").read_bytes() == b"payload"
    assert os.path.samefile(dest / "B.txt", dest / "C.txt")  # C linked to B's copy
    assert not os.path.samefile(dest / "A.txt", dest / "B.txt")  # B is a separate copy
