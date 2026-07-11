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
from types import SimpleNamespace

import pytest

from archivey import (
    ExtractionLimits,
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
from archivey.internal.extraction import (
    BombTracker,
    ExtractionCoordinator,
    _AlwaysStopExtractionError,
)
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


def test_check_universal_rejects_root_named_file(tmp_path: Path) -> None:
    with pytest.raises(PathTraversalError, match="extraction root"):
        check_universal(_member("."), tmp_path)
    with pytest.raises(PathTraversalError, match="extraction root"):
        check_universal(_member(""), tmp_path)


def test_check_universal_allows_root_directory(tmp_path: Path) -> None:
    check_universal(_member(".", type=MemberType.DIRECTORY), tmp_path)


def test_check_universal_rejects_special_file(tmp_path: Path) -> None:
    with pytest.raises(SpecialFileError):
        check_universal(_member("dev", type=MemberType.OTHER), tmp_path)


def test_check_universal_rejects_symlink_escape(tmp_path: Path) -> None:
    m = _member("link", type=MemberType.SYMLINK, link_target="../../etc/passwd")
    with pytest.raises(SymlinkEscapeError):
        check_universal(m, tmp_path)


def test_check_universal_rejects_null_byte_in_symlink_target(tmp_path: Path) -> None:
    m = _member("link", type=MemberType.SYMLINK, link_target="target\x00hidden")
    with pytest.raises(SymlinkEscapeError, match="Null byte in link target"):
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


@pytest.mark.skipif(
    os.name != "posix", reason="surrogateescape filename bytes are a POSIX concept"
)
@pytest.mark.parametrize(
    "name_bytes",
    [
        b"caf\xe9.txt",  # Latin-1 'é' — undecodable as UTF-8, kept via surrogateescape
        b"\xff\xfe.bin",  # arbitrary high bytes
        b"dir\xe9/file\xff.txt",  # hostile bytes in a subdir component too
    ],
    ids=["latin1", "highbytes", "subdir"],
)
def test_surrogateescape_name_extracts_safely_or_is_cleanly_refused(
    tmp_path: Path, name_bytes: bytes
) -> None:
    # The accept side of the encoding contract (companion to the check_universal
    # totality/materializability property tests). A member name carrying non-UTF-8
    # filename bytes is decoded with surrogateescape on read and PASSES the universal
    # filter (the bytes are fsencodable, so representable *as bytes*). What happens at
    # the write() syscall is then the *target filesystem's* call, and we only promise
    # safety, not faithful round-trip:
    #   * On a bytes-transparent FS (ext4/most Linux) the file lands under the exact
    #     original filename bytes — no crash, no mojibake.
    #   * On a UTF-8-enforcing FS (APFS/macOS raises OSError EILSEQ "Illegal byte
    #     sequence") the OS refuses the name; extraction surfaces that as a normal
    #     write failure — no traversal, nothing created outside dest, no process abort.
    # Sanitizing such names into an always-writable "safe" form is a deliberate,
    # policy-gated feature (threat-model O3/O7), intentionally NOT done here.
    stored = name_bytes.decode("utf-8", errors="surrogateescape")
    buf = io.BytesIO()
    with tarfile.open(
        fileobj=buf, mode="w", encoding="utf-8", errors="surrogateescape"
    ) as tf:
        info = tarfile.TarInfo(stored)
        info.size = 3
        tf.addfile(info, io.BytesIO(b"abc"))

    dest = tmp_path / "out"
    try:
        extract(io.BytesIO(buf.getvalue()), dest, policy=ExtractionPolicy.TRUSTED)
    except ExtractionError:
        # The filesystem rejected the byte sequence at write time (e.g. APFS/macOS
        # raises OSError EILSEQ); the coordinator translates that to a typed
        # ExtractionError. That is the honest "the OS won't represent this name"
        # outcome — safe. The only guarantee is that nothing escaped: whatever
        # exists is under dest.
        for p in dest.rglob("*"):
            assert (
                dest.resolve() in p.resolve().parents or p.resolve() == dest.resolve()
            )
        return

    # The FS accepted the bytes: the file exists under the exact original filename bytes.
    on_disk = {os.fsencode(p.name) for p in dest.rglob("*") if p.is_file()}
    assert os.path.basename(name_bytes) in on_disk


def test_unrepresentable_name_oserror_is_translated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A destination filesystem that refuses a filter-accepted name at write time
    # (OSError EILSEQ, e.g. non-UTF-8 bytes on APFS/macOS) must surface as a typed
    # ExtractionError naming the member, not a bare OSError. Simulated here (ext4
    # accepts any bytes) by making the atomic file write raise EILSEQ.
    import archivey.internal.extraction as extraction_mod

    def fake_replace(src: object, dst: object, /) -> None:
        raise OSError(errno.EILSEQ, "Illegal byte sequence")

    monkeypatch.setattr(extraction_mod.os, "replace", fake_replace)

    archive = _tar_bytes([("file", "member.txt", b"hi")])
    dest = tmp_path / "out"
    with pytest.raises(ExtractionError, match="member.txt"):
        extract(io.BytesIO(archive), dest, policy=ExtractionPolicy.TRUSTED)


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
    # Static denominator: the reader reports a cheaply-known outer compressed size.
    t = BombTracker(
        max_bytes=10**12,
        max_ratio=2.0,
        ratio_activation_threshold=10,
        source=SimpleNamespace(
            compressed_source_size=10, compressed_bytes_consumed=None
        ),
    )
    t.start_member(_member("f"))  # no per-member compressed_size
    with pytest.raises(_AlwaysStopExtractionError):
        t.count(30)  # total 30 > floor 10, 30/10 = 3 > 2


def test_archive_wide_ratio_live_denominator() -> None:
    # No static size (e.g. a pipe): the tracker samples the reader's live count of
    # compressed bytes consumed instead.
    t = BombTracker(
        max_bytes=10**12,
        max_ratio=2.0,
        ratio_activation_threshold=10,
        source=SimpleNamespace(
            compressed_source_size=None, compressed_bytes_consumed=10
        ),
    )
    t.start_member(_member("f"))
    with pytest.raises(_AlwaysStopExtractionError):
        t.count(30)  # total 30 > floor 10, live 30/10 = 3 > 2


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


def test_file_writer_rejects_missing_stream() -> None:
    # A FILE reaching the writer with stream=None is a backend bug (a zero-byte FILE still
    # yields a real, empty stream). The writer must raise rather than silently create an
    # empty file and mask the bug (task 5a.5).
    with pytest.raises(ExtractionError, match="no data stream"):
        ExtractionCoordinator._copy_to_fileobj(None, io.BytesIO(), None)


# ---------------------------------------------------------------------------
# Coordinator — ZIP vertical slice (tasks 3.x, 5.x)
# ---------------------------------------------------------------------------


def test_extract_zip_basic(tmp_path: Path) -> None:
    src = tmp_path / "a.zip"
    _write_zip(src, {"hello.txt": b"hi", "dir/nested.txt": b"deep"})
    dest = tmp_path / "out"
    results = extract(src, dest).results
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
        results = r.extract_all(dest, members=["a.txt", "c.txt"]).results
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
    results = extract(src, dest, overwrite=OverwritePolicy.SKIP).results
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


@pytest.mark.parametrize(
    "overwrite", [OverwritePolicy.REPLACE, OverwritePolicy.ERROR, OverwritePolicy.SKIP]
)
def test_error_when_dest_is_a_file_never_deletes_it(
    tmp_path: Path, overwrite: OverwritePolicy
) -> None:
    """A file at the dest path is a hard error under every policy — never deleted.

    Extracting must not remove a path the caller pointed at by mistake (e.g. a CLI given
    a file where a directory was meant), and must not surface a raw ``FileExistsError``.
    """
    src = tmp_path / "a.zip"
    _write_zip(src, {"a.txt": b"new"})
    dest = tmp_path / "out"
    dest.write_bytes(b"important data")
    with pytest.raises(ExtractionError, match="not a directory"):
        extract(src, dest, overwrite=overwrite)
    assert dest.is_file()
    assert dest.read_bytes() == b"important data"  # untouched


@pytest.mark.parametrize(
    "overwrite", [OverwritePolicy.REPLACE, OverwritePolicy.ERROR, OverwritePolicy.SKIP]
)
def test_error_when_dest_is_a_dangling_symlink_never_deletes_it(
    tmp_path: Path, overwrite: OverwritePolicy
) -> None:
    """A dangling symlink at the dest is a hard error — the link is preserved, not removed.

    ``lexists`` (not ``exists``) sees the broken link; otherwise ``exists`` reports False
    and ``mkdir`` raises a raw ``FileExistsError`` on the occupied path.
    """
    src = tmp_path / "a.zip"
    _write_zip(src, {"a.txt": b"new"})
    dest = tmp_path / "out"
    target = tmp_path / "nonexistent-target"
    dest.symlink_to(target)  # dangling: target does not exist
    assert dest.is_symlink() and not dest.exists()

    with pytest.raises(ExtractionError, match="not a directory"):
        extract(src, dest, overwrite=overwrite)

    assert dest.is_symlink()  # preserved, not unlinked
    assert not target.exists()  # never created behind the link


def test_dest_symlink_to_dir_is_followed_into_target(tmp_path: Path) -> None:
    """A dest symlink pointing at a real directory is followed (tar -C / unzip -d).

    Members land in the pointed-to directory and the caller's symlink is left in place;
    the dest root is trusted, unlike archive-internal symlinks.
    """
    src = tmp_path / "a.zip"
    _write_zip(src, {"a.txt": b"new"})
    target = tmp_path / "real_target"
    target.mkdir()
    dest = tmp_path / "out"
    dest.symlink_to(target)

    extract(src, dest, overwrite=OverwritePolicy.REPLACE)

    assert dest.is_symlink()  # symlink preserved, not replaced with a real dir
    assert (target / "a.txt").read_bytes() == b"new"  # extracted into the target


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


def test_replace_failure_preserves_existing_file(tmp_path: Path) -> None:
    # A mid-extraction failure under REPLACE must not clobber the existing file: FILE writes
    # land via a temp + os.replace, so the old data survives and no temp is left behind.
    src = tmp_path / "big.zip"
    _write_zip(src, {"a.txt": b"x" * 100_000})
    dest = tmp_path / "out"
    dest.mkdir()
    (dest / "a.txt").write_bytes(b"OLD DATA")
    with pytest.raises(ExtractionError):
        extract(
            src,
            dest,
            overwrite=OverwritePolicy.REPLACE,
            limits=ExtractionLimits(max_extracted_bytes=1000),
        )
    assert (dest / "a.txt").read_bytes() == b"OLD DATA"  # untouched
    assert not list(dest.glob(".archivey-tmp-*"))  # temp cleaned up


def test_no_temp_files_after_success(tmp_path: Path) -> None:
    src = tmp_path / "a.zip"
    _write_zip(src, {"a.txt": b"hi", "dir/b.txt": b"bye"})
    dest = tmp_path / "out"
    extract(src, dest)
    assert not list(dest.rglob(".archivey-tmp-*"))


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
    results = extract(src, dest, on_error=OnError.CONTINUE).results
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
        extract(
            src,
            dest,
            limits=ExtractionLimits(max_extracted_bytes=6000),
            on_error=OnError.CONTINUE,
        )


def test_zip_bomb_per_member_ratio(tmp_path: Path) -> None:
    # Highly compressible payload above the activation threshold trips the ratio.
    src = tmp_path / "bomb.zip"
    payload = b"\x00" * (8 * 1024 * 1024)  # 8 MiB of zeros -> tiny compressed
    _write_zip(src, {"bomb.bin": payload})
    dest = tmp_path / "out"
    with pytest.raises(ExtractionError):
        extract(
            src,
            dest,
            limits=ExtractionLimits(max_ratio=10.0, ratio_activation_threshold=1024),
        )


def test_entry_count_bomb(tmp_path: Path) -> None:
    src = tmp_path / "many.zip"
    _write_zip(src, {f"f{i}.txt": b"" for i in range(50)})
    dest = tmp_path / "out"
    with pytest.raises(ExtractionError):
        extract(
            src,
            dest,
            limits=ExtractionLimits(max_entries=10),
            on_error=OnError.CONTINUE,
        )


def test_selector_skips_do_not_count_toward_max_entries(tmp_path: Path) -> None:
    # Only members actually written count toward max_entries (resolved 2026-07 decision):
    # a member excluded by the selector creates nothing on disk, so a tiny cap survives a
    # huge archive when only one member is selected.
    src = tmp_path / "many.zip"
    _write_zip(src, {f"f{i}.txt": b"x" for i in range(20)})
    dest = tmp_path / "out"
    with open_archive(src) as r:
        results = r.extract_all(
            dest, members=["f0.txt"], limits=ExtractionLimits(max_entries=1)
        ).results
    assert (dest / "f0.txt").read_bytes() == b"x"
    assert len(results) == 1


def test_filter_skips_do_not_count_toward_max_entries(tmp_path: Path) -> None:
    # A member the user filter drops (returns None) likewise creates nothing on disk and
    # must not consume the entry-count budget.
    src = tmp_path / "many.zip"
    _write_zip(src, {f"f{i}.txt": b"x" for i in range(20)})
    dest = tmp_path / "out"

    def keep_one(m: ArchiveMember) -> ArchiveMember | None:
        return m if m.name == "f0.txt" else None

    with open_archive(src) as r:
        results = r.extract_all(
            dest, filter=keep_one, limits=ExtractionLimits(max_entries=1)
        ).results
    assert (dest / "f0.txt").read_bytes() == b"x"
    assert [res.status for res in results] == [ExtractionStatus.EXTRACTED]


def test_rejected_members_do_not_count_toward_max_entries(tmp_path: Path) -> None:
    # A member rejected by the universal safety check writes nothing, so it must not
    # consume the entry-count budget either: two good files + one hostile member with
    # max_entries=2 completes under CONTINUE (before the fix the rejected member counted
    # and tripped the guard on the second good file).
    src = tmp_path / "a.zip"
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("good1.txt", b"a")
        z.writestr("../evil.txt", b"bad")
        z.writestr("good2.txt", b"b")
    dest = tmp_path / "out"
    with open_archive(src) as r:
        results = r.extract_all(
            dest, limits=ExtractionLimits(max_entries=2), on_error=OnError.CONTINUE
        ).results
    statuses = {res.member.name: res.status for res in results}
    assert statuses["good1.txt"] is ExtractionStatus.EXTRACTED
    assert statuses["good2.txt"] is ExtractionStatus.EXTRACTED
    assert ExtractionStatus.REJECTED in statuses.values()


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


def test_progress_totals_respect_selector(tmp_path: Path) -> None:
    # Totals cover what the call will actually attempt: with a selector, members_total
    # and total_bytes_estimated count only the selected members, and members_done can
    # reach members_total.
    src = tmp_path / "a.zip"
    _write_zip(src, {"a.txt": b"aaaa", "b.txt": b"b" * 100, "c.txt": b"cc"})
    dest = tmp_path / "out"
    progress = []
    with open_archive(src) as r:
        r.extract_all(dest, members=["a.txt", "c.txt"], on_progress=progress.append)
    last = progress[-1]
    assert last.members_total == 2
    assert last.members_done == 2
    assert last.total_bytes_estimated == 6  # a.txt (4) + c.txt (2); b.txt excluded


def test_progress_counts_filter_skipped_members_as_done(tmp_path: Path) -> None:
    # A user-filter skip is still a processed member: members_done must reach
    # members_total at the end (the filter cannot be pre-applied to the totals).
    src = tmp_path / "a.zip"
    _write_zip(src, {"a.txt": b"a", "b.txt": b"b", "c.txt": b"c"})
    dest = tmp_path / "out"
    progress = []
    with open_archive(src) as r:
        r.extract_all(
            dest,
            filter=lambda m: None if m.name == "b.txt" else m,
            on_progress=progress.append,
        )
    last = progress[-1]
    assert last.members_total == 3
    assert last.members_done == 3
    assert not (dest / "b.txt").exists()


def test_extract_one_shot_from_non_seekable_pipe(tmp_path: Path) -> None:
    # The one-shot extract() auto-selects streaming mode for a non-seekable source:
    # extraction is a single forward pass, so it needs no random access.
    from tests.streams_util import NonSeekableBytesIO

    raw = _tar_bytes(
        [("file", "a.txt", b"hello"), ("file", "b.txt", b"world")], mode="w:gz"
    )
    dest = tmp_path / "out"
    results = extract(NonSeekableBytesIO(raw), dest).results
    assert (dest / "a.txt").read_bytes() == b"hello"
    assert (dest / "b.txt").read_bytes() == b"world"
    assert {r.status for r in results} == {ExtractionStatus.EXTRACTED}


# ---------------------------------------------------------------------------
# TAR symlinks (tasks 3.4, 4.5)
# ---------------------------------------------------------------------------


def test_tar_symlink_created(tmp_path: Path) -> None:
    src = tmp_path / "a.tar"
    src.write_bytes(
        _tar_bytes([("file", "file.txt", b"data"), ("sym", "link", "file.txt")])
    )
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
    src.write_bytes(
        _tar_bytes([("file", "file.txt", b"data"), ("sym", "link", "file.txt")])
    )
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
    results = extract(src, dest, on_error=OnError.CONTINUE).results
    statuses = {r.member.name: r.status for r in results}
    assert statuses["evil"] is ExtractionStatus.REJECTED
    assert (dest / "ok.txt").read_bytes() == b"ok"


# ---------------------------------------------------------------------------
# Chained-symlink attack (task 5a.4, ported from the DEV oracle suite)
#
# The classic escape: member 1 plants a directory symlink `sub` that points OUTSIDE the
# destination, then member 2 is written "through" it as `sub/<payload>`, so a naive
# extractor lands the payload outside dest. archivey blocks this on two layers, and both
# the SYMLINK-payload and FILE-payload variants of member 2 must be neutralized:
#   * the escaping parent symlink is rejected up front by the universal check
#     (SymlinkEscapeError), so it is never planted, and
#   * the universal check re-resolves each member's PARENT directory on the real
#     filesystem, so a payload written through an already-planted hostile parent symlink
#     is rejected (PathTraversalError) before any bytes are written outside dest.
# ---------------------------------------------------------------------------


def test_chained_symlink_attack_symlink_payload_rejected(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    src = tmp_path / "attack.tar"
    src.write_bytes(
        _tar_bytes(
            [
                ("sym", "sub", str(outside)),  # parent symlink escaping dest
                ("sym", "sub/leak", "target"),  # SYMLINK payload written through it
            ]
        )
    )
    dest = tmp_path / "out"
    with pytest.raises((SymlinkEscapeError, PathTraversalError)):
        extract(src, dest)
    assert list(outside.iterdir()) == []  # nothing leaked outside the destination


def test_chained_symlink_attack_file_payload_rejected(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    src = tmp_path / "attack.tar"
    src.write_bytes(
        _tar_bytes(
            [
                ("sym", "sub", str(outside)),  # parent symlink escaping dest
                ("file", "sub/leak.txt", b"pwned"),  # FILE payload written through it
            ]
        )
    )
    dest = tmp_path / "out"
    # Under CONTINUE the escaping parent symlink is rejected (never planted) while the run
    # proceeds, proving the FILE payload cannot follow it out of dest.
    results = extract(src, dest, on_error=OnError.CONTINUE).results
    statuses = {r.member.name: r.status for r in results}
    assert statuses["sub"] is ExtractionStatus.REJECTED
    assert not (outside / "leak.txt").exists()
    assert list(outside.iterdir()) == []


@pytest.mark.skipif(
    os.name != "posix",
    reason="directory symlinks / os.symlink semantics are POSIX here",
)
def test_file_payload_through_preexisting_parent_symlink_rejected(
    tmp_path: Path,
) -> None:
    # The runtime layer: a hostile symlink already sits at the destination (e.g. planted by
    # a prior partial run). The universal check resolves the member's parent on disk, so a
    # FILE written through it is rejected before any bytes escape.
    outside = tmp_path / "outside"
    outside.mkdir()
    dest = tmp_path / "out"
    dest.mkdir()
    os.symlink(outside, dest / "sub", target_is_directory=True)
    src = tmp_path / "a.tar"
    src.write_bytes(_tar_bytes([("file", "sub/leak.txt", b"pwned")]))
    with pytest.raises(PathTraversalError):
        extract(src, dest)
    assert not (outside / "leak.txt").exists()


@pytest.mark.skipif(
    os.name != "posix",
    reason="directory symlinks / os.symlink semantics are POSIX here",
)
def test_symlink_payload_through_preexisting_parent_symlink_rejected(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    dest = tmp_path / "out"
    dest.mkdir()
    os.symlink(outside, dest / "sub", target_is_directory=True)
    src = tmp_path / "a.tar"
    src.write_bytes(_tar_bytes([("sym", "sub/leak", "x")]))
    with pytest.raises(PathTraversalError):
        extract(src, dest)
    assert not (outside / "leak").exists()


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
        results = r.extract_all(dest, filter=drop_source).results
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
        results = r.extract_all(dest2, filter=drop_source, on_error=OnError.CONTINUE).results
    statuses = {r.member.name: r.status for r in results}
    assert statuses["hard.txt"] is ExtractionStatus.FAILED
    assert not (dest2 / "hard.txt").exists()


# ---------------------------------------------------------------------------
# Seekable + non-seekable compressed TAR (tasks 5.3, 6.3)
# ---------------------------------------------------------------------------


def test_seekable_targz_extract(tmp_path: Path) -> None:
    src = tmp_path / "a.tar.gz"
    src.write_bytes(
        _tar_bytes([("file", "a.txt", b"hello"), ("dir", "d", None)], mode="w:gz")
    )
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
        extract(
            src,
            dest,
            limits=ExtractionLimits(max_ratio=5.0, ratio_activation_threshold=1024),
        )


def test_non_seekable_targz_extract(tmp_path: Path) -> None:
    from tests.streams_util import NonSeekableBytesIO

    raw = _tar_bytes(
        [("file", "a.txt", b"hello"), ("file", "b.txt", b"world")], mode="w:gz"
    )
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


# ---------------------------------------------------------------------------
# Live (streaming) decompression-ratio guard (live-decompression-ratio-guard)
# ---------------------------------------------------------------------------


def test_counting_reader_counts_bytes() -> None:
    from archivey.internal.streams.counting import CountingReader

    cr = CountingReader(io.BytesIO(b"abcdefghij"))
    assert cr.bytes_read == 0
    assert cr.read(4) == b"abcd"
    assert cr.bytes_read == 4
    assert cr.read() == b"efghij"
    assert cr.bytes_read == 10


def test_compressed_bytes_consumed_grows_on_piped_targz() -> None:
    from tests.streams_util import NonSeekableBytesIO

    raw = _tar_bytes([("file", "a.txt", b"x" * 200_000)], mode="w:gz")
    with open_archive(NonSeekableBytesIO(raw), streaming=True) as r:
        # A compressed pipe has no cheaply-knowable total, but the live counter is present.
        assert r.compressed_source_size is None
        assert r.compressed_bytes_consumed is not None
        for _member, stream in r.stream_members():
            if stream is not None:
                stream.read()
        assert r.compressed_bytes_consumed > 0


def test_streaming_targz_bomb_caught_by_live_ratio(tmp_path: Path) -> None:
    from tests.streams_util import NonSeekableBytesIO

    # 8 MiB of zeros -> a tiny gz; from a pipe both static denominators are unknown, so
    # only the live ratio (and the absolute cap, set high) can catch it.
    raw = _tar_bytes([("file", "z.bin", b"\x00" * (8 * 1024 * 1024))], mode="w:gz")
    dest = tmp_path / "out"
    with open_archive(NonSeekableBytesIO(raw), streaming=True) as r:
        assert r.compressed_source_size is None  # no static archive-wide denominator
        with pytest.raises(ExtractionError):
            r.extract_all(
                dest,
                limits=ExtractionLimits(
                    max_ratio=10.0,
                    ratio_activation_threshold=1024,
                    max_extracted_bytes=100
                    * 2**20,  # high, so the cap is not what trips
                ),
            )


def test_streaming_live_ratio_halts_under_continue(tmp_path: Path) -> None:
    from tests.streams_util import NonSeekableBytesIO

    raw = _tar_bytes([("file", "z.bin", b"\x00" * (8 * 1024 * 1024))], mode="w:gz")
    dest = tmp_path / "out"
    with open_archive(NonSeekableBytesIO(raw), streaming=True) as r:
        with pytest.raises(ExtractionError):
            r.extract_all(
                dest,
                limits=ExtractionLimits(
                    max_ratio=10.0,
                    ratio_activation_threshold=1024,
                    max_extracted_bytes=100 * 2**20,
                ),
                on_error=OnError.CONTINUE,  # global guard halts anyway
            )


def test_streaming_plain_tar_no_live_ratio_trip(tmp_path: Path) -> None:
    from tests.streams_util import NonSeekableBytesIO

    payload = os.urandom(2 * 1024 * 1024)  # incompressible; plain (uncompressed) tar
    raw = _tar_bytes([("file", "a.bin", payload)], mode="w")
    dest = tmp_path / "out"
    with open_archive(NonSeekableBytesIO(raw), streaming=True) as r:
        assert r.compressed_bytes_consumed is None  # uncompressed: nothing counted
        r.extract_all(
            dest,
            limits=ExtractionLimits(max_ratio=10.0, ratio_activation_threshold=1024),
        )
    assert (dest / "a.bin").read_bytes() == payload


def test_seekable_targz_uses_static_not_live(tmp_path: Path) -> None:
    # A path (seekable) .tar.gz has a knowable compressed_source_size, so the static
    # archive-wide ratio applies and no live counter is created.
    src = tmp_path / "a.tar.gz"
    src.write_bytes(_tar_bytes([("file", "a.txt", b"hi")], mode="w:gz"))
    with open_archive(src) as r:
        assert r.compressed_source_size is not None
        assert r.compressed_bytes_consumed is None


def test_streaming_bare_gz_bomb_caught_by_live_ratio(tmp_path: Path) -> None:
    # A bare .gz single-file compressor from a pipe (SingleFileReader) is covered too.
    import gzip

    from tests.streams_util import NonSeekableBytesIO

    raw = gzip.compress(b"\x00" * (8 * 1024 * 1024))
    dest = tmp_path / "out"
    with open_archive(NonSeekableBytesIO(raw), streaming=True) as r:
        assert r.compressed_source_size is None
        assert (
            r.compressed_bytes_consumed is not None
        )  # counter wired for single-file too
        with pytest.raises(ExtractionError):
            r.extract_all(
                dest,
                limits=ExtractionLimits(
                    max_ratio=10.0,
                    ratio_activation_threshold=1024,
                    max_extracted_bytes=100 * 2**20,
                ),
            )


# ---------------------------------------------------------------------------
# Orphaned-hardlink second pass + selector semantics + live-guard coverage
# (post-4b review regressions — each test pins a bug found reviewing #28/#31)
# ---------------------------------------------------------------------------


def _orphan_tar(tmp_path: Path, links: list[str]) -> Path:
    """A tar with source ``A.txt`` followed by hardlinks to it (the orphan-recovery shape:
    extracting only the links leaves the source excluded)."""
    src = tmp_path / "a.tar"
    src.write_bytes(
        _tar_bytes(
            [("file", "A.txt", b"hello world")]
            + [("hard", ln, "A.txt") for ln in links]
        )
    )
    return src


def test_orphan_first_link_skipped_writes_to_next(tmp_path: Path) -> None:
    # Regression: with the excluded source's FIRST link skipped (OverwritePolicy.SKIP over
    # an existing destination), the coordinator crashed with a raw KeyError when linking
    # the second orphan (source_paths never got an entry). The content must instead be
    # written to the first link whose destination is writable.
    src = _orphan_tar(tmp_path, ["L1.txt", "L2.txt"])
    dest = tmp_path / "out"
    dest.mkdir()
    (dest / "L1.txt").write_bytes(b"pre-existing")

    with open_archive(src) as r:
        results = r.extract_all(
            dest,
            members=["L1.txt", "L2.txt"],  # source A.txt excluded
            overwrite=OverwritePolicy.SKIP,
            on_error=OnError.CONTINUE,
        ).results

    statuses = {res.member.name: res.status for res in results}
    assert statuses["L1.txt"] is ExtractionStatus.SKIPPED
    assert statuses["L2.txt"] is ExtractionStatus.EXTRACTED
    assert (dest / "L1.txt").read_bytes() == b"pre-existing"  # untouched under SKIP
    assert (
        dest / "L2.txt"
    ).read_bytes() == b"hello world"  # content moved to next link
    assert not (dest / "A.txt").exists()  # excluded source never materialized by name


def test_orphan_all_links_skipped_writes_nothing(tmp_path: Path) -> None:
    # Companion to the above: when EVERY selected link's destination already exists under
    # SKIP, all links are recorded SKIPPED, existing files stay untouched, and no stray
    # content or temp file is written anywhere.
    src = _orphan_tar(tmp_path, ["L1.txt", "L2.txt"])
    dest = tmp_path / "out"
    dest.mkdir()
    (dest / "L1.txt").write_bytes(b"keep-1")
    (dest / "L2.txt").write_bytes(b"keep-2")

    with open_archive(src) as r:
        results = r.extract_all(
            dest,
            members=["L1.txt", "L2.txt"],
            overwrite=OverwritePolicy.SKIP,
        ).results

    assert [res.status for res in results] == [ExtractionStatus.SKIPPED] * 2
    assert (dest / "L1.txt").read_bytes() == b"keep-1"
    assert (dest / "L2.txt").read_bytes() == b"keep-2"
    assert sorted(p.name for p in dest.iterdir()) == ["L1.txt", "L2.txt"]  # no strays


def test_orphan_materialized_source_carries_link_metadata(tmp_path: Path) -> None:
    # Regression: the second-pass-materialized source was written with member=None, so no
    # chmod/utime ever ran and the file kept mkstemp's 0600 forever. The link's transformed
    # copy supplies the on-disk identity (spec: "the copy supplies ... mode, timestamps");
    # hardlinks share one inode, so it must be applied to the file carrying the content.
    import stat as stat_mod

    link_mtime = 1_600_000_000
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as t:
        info = tarfile.TarInfo("A.txt")
        info.size = 11
        info.mode = 0o644
        info.mtime = (
            1_500_000_000  # source's own mtime differs, proving we use the link's
        )
        t.addfile(info, io.BytesIO(b"hello world"))
        info = tarfile.TarInfo("L1.txt")
        info.type = tarfile.LNKTYPE
        info.linkname = "A.txt"
        info.mode = 0o644
        info.mtime = link_mtime
        t.addfile(info)
    src = tmp_path / "a.tar"
    src.write_bytes(buf.getvalue())
    dest = tmp_path / "out"

    with open_archive(src) as r:
        results = r.extract_all(dest, members=["L1.txt"]).results

    assert [res.status for res in results] == [ExtractionStatus.EXTRACTED]
    st = (dest / "L1.txt").lstat()
    if os.name == "posix":  # Windows has no Unix permission bits (see _posix_perms)
        assert stat_mod.S_IMODE(st.st_mode) == 0o644  # not mkstemp's 0600
    assert int(st.st_mtime) == link_mtime  # the link member's mtime, not the source's


def test_hardlink_duplicate_name_extraction_links_first_inode(tmp_path: Path) -> None:
    src = tmp_path / "dup-hardlink.tar"
    src.write_bytes(
        _tar_bytes(
            [
                ("file", "A.txt", b"content1"),
                ("hard", "L.txt", "A.txt"),
                ("file", "A.txt", b"content2"),
            ]
        )
    )
    dest = tmp_path / "out"
    with open_archive(src) as r:
        results = r.extract_all(dest, overwrite=OverwritePolicy.REPLACE).results
    assert all(res.status is ExtractionStatus.EXTRACTED for res in results)
    assert (dest / "L.txt").read_bytes() == b"content1"
    assert (dest / "A.txt").read_bytes() == b"content2"
    # L was linked against the first A.txt inode; the second duplicate replaced the path.
    if os.name == "posix":
        assert not os.path.samefile(dest / "A.txt", dest / "L.txt")


def test_hardlink_before_source_shares_inode_and_counts_once(tmp_path: Path) -> None:
    # Regression: a hardlink PRECEDING its source in archive order (legal in crafted /
    # non-GNU-ordered archives) was re-read in the second pass and written as an
    # independent copy — content matched but the two paths did not share an inode, and the
    # source's bytes were bomb-counted twice. It must be os.link'd to the extracted source.
    payload = b"payload bytes"
    src = tmp_path / "a.tar"
    src.write_bytes(
        _tar_bytes([("hard", "L1.txt", "A.txt"), ("file", "A.txt", payload)])
    )
    dest = tmp_path / "out"
    progress_bytes: list[int] = []

    with open_archive(src) as r:
        results = r.extract_all(
            dest, on_progress=lambda p: progress_bytes.append(p.bytes_written)
        ).results

    assert all(res.status is ExtractionStatus.EXTRACTED for res in results)
    assert (dest / "L1.txt").read_bytes() == payload
    assert os.path.samefile(dest / "A.txt", dest / "L1.txt")  # one inode, truly linked
    assert progress_bytes[-1] == len(payload)  # bytes read/counted once, not twice


def test_selector_archivemember_entry_matches_by_identity(tmp_path: Path) -> None:
    # The collection selector's ArchiveMember entries match by object identity (the Phase 5
    # semantics, now specced in safe-extraction): with duplicate names, passing one member
    # object selects only that occurrence — while a str entry matches every duplicate.
    src = tmp_path / "a.tar"
    src.write_bytes(
        _tar_bytes([("file", "dup.txt", b"first"), ("file", "dup.txt", b"second")])
    )

    with open_archive(src) as r:
        first, second = r.members()
        assert first.name == second.name == "dup.txt"
        results = r.extract_all(tmp_path / "by_member", members=[first]).results
    assert len(results) == 1
    assert (tmp_path / "by_member" / "dup.txt").read_bytes() == b"first"

    with open_archive(src) as r:
        results = r.extract_all(
            tmp_path / "by_name", members=["dup.txt"], overwrite=OverwritePolicy.REPLACE
        ).results
    assert len(results) == 2  # a str entry matches every same-named duplicate
    assert (tmp_path / "by_name" / "dup.txt").read_bytes() == b"second"


def test_opaque_seekable_compressed_source_gets_live_ratio(tmp_path: Path) -> None:
    # Regression: the live counter was only wired for NON-seekable streams, while the
    # static denominator needs a cheaply-sizable source — so a compressed archive on a
    # seekable-but-opaque stream (a custom stream type: not SEEK_END-whitelisted, no
    # `.size`, no try_get_size()) had NO archive-wide ratio guard at all, leaving only the
    # absolute byte cap. The counter must engage exactly when the static size is absent.
    from tests.streams_util import (
        CountingBytesIO,  # seekable, opaque to source_byte_size
    )

    raw = _tar_bytes([("file", "z.bin", b"\x00" * (8 * 1024 * 1024))], mode="w:gz")
    dest = tmp_path / "out"
    with open_archive(CountingBytesIO(raw)) as r:
        assert r.compressed_source_size is None  # opaque: no static denominator...
        assert (
            r.compressed_bytes_consumed is not None
        )  # ...so the live counter is wired
        with pytest.raises(ExtractionError):
            r.extract_all(
                dest,
                limits=ExtractionLimits(
                    max_ratio=10.0,
                    ratio_activation_threshold=1024,
                    max_extracted_bytes=100
                    * 2**20,  # high, so the cap is not what trips
                ),
            )
