"""CLI behavior-matrix tests (argv → exit / stdout / stderr)."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from archivey.cli.exit_codes import EXIT_FAIL, EXIT_OK, EXIT_USAGE
from archivey.cli.main import _inject_default_list, main


def _zip(path: Path, entries: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return path


@pytest.fixture
def sample_zip(tmp_path: Path) -> Path:
    return _zip(
        tmp_path / "sample.zip",
        {
            "a.txt": b"hello",
            "b/c.py": b"print(1)\n",
            "b/d_test.py": b"x",
        },
    )


def test_inject_default_list() -> None:
    assert _inject_default_list(["a.zip"]) == ["list", "a.zip"]
    assert _inject_default_list(["--track-io", "a.zip"]) == [
        "--track-io",
        "list",
        "a.zip",
    ]
    assert _inject_default_list(["x", "a.zip"]) == ["x", "a.zip"]
    assert _inject_default_list(["create", "a.zip"]) == ["create", "a.zip"]
    # Bare "-" is a positional (reserved stdin), not an option.
    assert _inject_default_list(["-"]) == ["list", "-"]


def test_global_flags_before_verb(
    sample_zip: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Shared-parents argparse pitfall: pre-verb globals must survive subparser defaults.
    assert main(["--track-io", str(sample_zip)]) == EXIT_OK
    err = capsys.readouterr().err
    assert "track-io:" in err

    assert main(["-v", "test", str(sample_zip)]) == EXIT_OK
    err = capsys.readouterr().err
    assert "OK   a.txt" in err or "a.txt" in err
    assert "OK," in err or "failed" in err


def test_password_before_verb_reaches_dispatch(
    sample_zip: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from archivey.cli import main as main_mod

    seen: dict[str, object] = {}

    def _capture_test(**kwargs: object) -> int:
        seen.update(kwargs)
        return EXIT_OK

    monkeypatch.setattr(main_mod, "run_test", _capture_test)
    assert main(["--password", "secret", "test", str(sample_zip)]) == EXIT_OK
    assert seen.get("password") == "secret"


def test_abbrev_password_rejected(sample_zip: Path) -> None:
    # allow_abbrev=False: --pass must not become --password with a mangled value.
    assert main(["--pass", "secret", str(sample_zip)]) == EXIT_USAGE


def test_abbrev_overwrite_rejected_post_verb(sample_zip: Path, tmp_path: Path) -> None:
    # Subparsers also need allow_abbrev=False (R2) — --over must not become --overwrite.
    dest = tmp_path / "out"
    assert (
        main(["x", str(sample_zip), "-d", str(dest), "--over", "error"]) == EXIT_USAGE
    )


def test_bare_invocation_is_usage() -> None:
    assert main([]) == EXIT_USAGE


def test_version() -> None:
    assert main(["--version"]) == EXIT_OK


def test_default_list_dispatch(
    sample_zip: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main([str(sample_zip)]) == EXIT_OK
    out = capsys.readouterr().out
    assert "a.txt" in out
    assert "b/c.py" in out


def test_list_alias(sample_zip: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["l", str(sample_zip)]) == EXIT_OK
    assert "a.txt" in capsys.readouterr().out


def test_list_incomplete_members_report_exits_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """CLI list prints recovered members then exits 1 when members_report.error is set."""
    from contextlib import contextmanager

    from archivey.cli import list_cmd
    from archivey.diagnostics import DiagnosticSummary, MemberListReport
    from archivey.exceptions import TruncatedError
    from archivey.types import ArchiveMember, MemberType

    member = ArchiveMember(type=MemberType.FILE, name="recovered.txt", size=4)
    report = MemberListReport(
        members=(member,),
        error=TruncatedError("truncated for test"),
        diagnostics=DiagnosticSummary.empty(),
    )

    class _FakeReader:
        def members_report(self) -> MemberListReport:
            return report

    @contextmanager
    def fake_open(*_args: object, **_kwargs: object):
        yield _FakeReader()

    monkeypatch.setattr(list_cmd, "open_for_cli", fake_open)
    archive = tmp_path / "dummy.tar"
    archive.write_bytes(b"x")
    assert (
        list_cmd.run_list(
            archive=str(archive),
            patterns=[],
            exclude=[],
            digests=False,
            verbose=False,
            salvage=False,
            password=None,
            track_io=False,
        )
        == EXIT_FAIL
    )
    captured = capsys.readouterr()
    assert "recovered.txt" in captured.out
    assert "truncated for test" in captured.err


def test_verb_named_file_known_verb_wins(tmp_path: Path) -> None:
    # A file named "x" collides with the extract alias; known-verb-wins dispatches extract.
    # Escape hatch: archivey list ./x
    named = tmp_path / "x"
    _zip(named, {"f.txt": b"data"})
    # extract into dest — should not fall through to list
    dest = tmp_path / "out"
    code = main(["x", str(named), "-d", str(dest)])
    assert code == EXIT_OK
    assert (dest / "f.txt").read_bytes() == b"data"
    # escape hatch lists the verb-named archive
    code = main(["list", str(named)])
    assert code == EXIT_OK


def test_dash_prefixed_verb_rejected(sample_zip: Path) -> None:
    assert main(["-x", str(sample_zip)]) == EXIT_USAGE


def test_stdin_token_reserved() -> None:
    assert main(["list", "-"]) == EXIT_USAGE
    assert main(["-"]) == EXIT_USAGE


def test_reserved_verbs(sample_zip: Path) -> None:
    assert main(["create", str(sample_zip)]) == EXIT_USAGE
    assert main(["hash", str(sample_zip)]) == EXIT_USAGE
    assert main(["convert", str(sample_zip)]) == EXIT_USAGE
    assert main(["cat", str(sample_zip)]) == EXIT_USAGE


def test_salvage_reserved(sample_zip: Path) -> None:
    assert main(["list", str(sample_zip), "--salvage"]) == EXIT_USAGE
    assert main(["--salvage", "list", str(sample_zip)]) == EXIT_USAGE
    assert (
        main(
            [
                "extract",
                str(sample_zip),
                "--salvage",
                "-d",
                str(sample_zip.parent / "o"),
            ]
        )
        == EXIT_USAGE
    )


def test_include_flag_rejected(sample_zip: Path) -> None:
    assert main(["list", str(sample_zip), "--include", "x"]) == EXIT_USAGE


def test_exclude_filter(sample_zip: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["list", str(sample_zip), "*.py", "--exclude", "*_test.py"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "b/c.py" in out
    assert "d_test.py" not in out
    assert "a.txt" not in out


def test_test_quiet_summary(
    sample_zip: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["test", str(sample_zip)]) == EXIT_OK
    captured = capsys.readouterr()
    assert "OK," in captured.err
    assert "failed" in captured.err
    # Quiet: no per-member OK lines on stderr by default
    assert "OK   a.txt" not in captured.err


def test_test_verbose_per_member(
    sample_zip: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["test", "-v", str(sample_zip)]) == EXIT_OK
    err = capsys.readouterr().err
    assert "OK   a.txt" in err or "OK   " in err


def test_extract_policy_and_dest(sample_zip: Path, tmp_path: Path) -> None:
    dest = tmp_path / "out"
    assert (
        main(
            [
                "extract",
                str(sample_zip),
                "-d",
                str(dest),
                "--policy",
                "strict",
                "--overwrite",
                "rename",
            ]
        )
        == EXIT_OK
    )
    assert (dest / "a.txt").read_bytes() == b"hello"


def test_extract_strict_blocks_device_name_and_continues(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Windows-reserved device names are rejected under STRICT; default CONTINUE
    # still extracts the remaining members (Q1). Trailing-dot names are stripped,
    # not rejected — see #123.
    from archivey.cli.exit_codes import EXIT_POLICY

    bad = _zip(tmp_path / "bad.zip", {"NUL": b"x", "ok.txt": b"y"})
    dest = tmp_path / "out"
    code = main(["extract", str(bad), "-d", str(dest), "--policy", "strict"])
    assert code == EXIT_POLICY
    err = capsys.readouterr().err
    assert "NUL" in err
    assert "blocked:" in err
    assert "extraction stopped" not in err.lower()
    assert (dest / "ok.txt").read_bytes() == b"y"


def test_extract_strict_stop_on_error_aborts_on_device_name(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from archivey.cli.exit_codes import EXIT_POLICY

    bad = _zip(tmp_path / "bad.zip", {"NUL": b"x", "ok.txt": b"y"})
    dest = tmp_path / "out"
    code = main(
        [
            "extract",
            str(bad),
            "-d",
            str(dest),
            "--policy",
            "strict",
            "--stop-on-error",
        ]
    )
    assert code == EXIT_POLICY
    err = capsys.readouterr().err
    assert "NUL" in err
    assert "extraction stopped" in err.lower()
    assert "remaining members" in err.lower()
    assert not (dest / "ok.txt").exists()


def test_extract_zip_root_slash_under_default_strict(tmp_path: Path) -> None:
    # ZIP root entry "/" normalizes to "."; STRICT must not abort on that spelling.
    archive = tmp_path / "rooted.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(zipfile.ZipInfo("/"), b"")
        zf.writestr("a.txt", b"hello")
    dest = tmp_path / "out"
    assert main(["extract", str(archive), "-d", str(dest)]) == EXIT_OK
    assert (dest / "a.txt").read_bytes() == b"hello"


def test_extract_smart_dest_multi_toplevel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    z = _zip(tmp_path / "multi.zip", {"a.txt": b"a", "b.txt": b"b"})
    assert main(["extract", str(z)]) == EXIT_OK
    assert (tmp_path / "multi" / "a.txt").exists()
    assert not (tmp_path / "a.txt").exists()


def test_extract_smart_dest_single_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    z = _zip(tmp_path / "one.zip", {"root/a.txt": b"a", "root/b.txt": b"b"})
    assert main(["extract", str(z)]) == EXIT_OK
    assert (tmp_path / "root" / "a.txt").exists()
    assert not (tmp_path / "one").exists()


def test_extract_dest_dot_splatter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    z = _zip(tmp_path / "splat.zip", {"a.txt": b"a", "b.txt": b"b"})
    assert main(["extract", str(z), "-d", "."]) == EXIT_OK
    assert (tmp_path / "a.txt").exists()
    assert (tmp_path / "b.txt").exists()


def test_info_and_detect(sample_zip: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["info", str(sample_zip)]) == EXIT_OK
    out = capsys.readouterr().out
    assert "format:      zip" in out or "format:" in out and "zip" in out
    assert "ArchiveFormat.ZIP" not in out
    assert "SEVEN_Z" not in out
    assert main(["detect", str(sample_zip)]) == EXIT_OK


def test_info_track_io_is_explicit_na(
    sample_zip: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["info", str(sample_zip), "--track-io"]) == EXIT_OK
    err = capsys.readouterr().err
    assert "track-io: n/a" in err


def test_extract_reports_renames(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    z = _zip(tmp_path / "c.zip", {"a.txt": b"new"})
    dest = tmp_path / "out"
    dest.mkdir()
    (dest / "a.txt").write_bytes(b"old")
    assert (
        main(
            [
                "extract",
                str(z),
                "-d",
                str(dest),
                "--overwrite",
                "rename",
            ]
        )
        == EXIT_OK
    )
    err = capsys.readouterr().err
    assert "renamed:" in err
    assert "extracted," in err and "renamed," in err
    assert (dest / "a.txt").read_bytes() == b"old"
    # Library rename spelling: "a (1).txt"
    assert any(p.name.startswith("a (") for p in dest.iterdir())


def test_extract_verbose_lists_members(
    sample_zip: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dest = tmp_path / "out"
    assert main(["-v", "extract", str(sample_zip), "-d", str(dest)]) == EXIT_OK
    err = capsys.readouterr().err
    assert "extracted: a.txt" in err
    assert "→" in err


def test_list_missing_archive(tmp_path: Path) -> None:
    assert main(["list", str(tmp_path / "missing.zip")]) == EXIT_FAIL


def test_cli_list_unencrypted_format_without_password(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Regression: the CLI used to always pass a PasswordProvider (TTY getpass
    # fallback). Formats with SUPPORTS_PASSWORD=False (TAR, ISO, …) rejected that as
    # "does not support passwords" even when the user never passed --password.
    import tarfile

    src = tmp_path / "plain.tar"
    with tarfile.open(src, "w") as t:
        info = tarfile.TarInfo("hello.txt")
        info.size = 5
        t.addfile(info, io.BytesIO(b"hello"))

    assert main([str(src)]) == EXIT_OK
    assert "hello.txt" in capsys.readouterr().out


def test_c_is_not_integrity_alias(sample_zip: Path) -> None:
    # Integrity check is `test`/`t`; letter `c` is reserved for future `create`.
    from archivey.cli.main import _VERBS

    assert "c" not in _VERBS
    assert main(["t", str(sample_zip)]) == EXIT_OK
    assert main(["create", str(sample_zip)]) == EXIT_USAGE


def test_no_tqdm_progress_still_extracts(
    sample_zip: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Patch where extract_cmd looks up the helper (import-by-name binding).
    import archivey.cli.extract_cmd as extract_mod

    monkeypatch.setattr(extract_mod, "make_progress_callback", lambda **_: None)
    dest = tmp_path / "out"
    assert main(["extract", str(sample_zip), "-d", str(dest)]) == EXIT_OK
    assert (dest / "a.txt").exists()


def test_progress_callback_requires_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    from archivey.cli import progress as progress_mod

    class _NonTTY(io.StringIO):
        def isatty(self) -> bool:  # noqa: A003 - match TextIO API
            return False

    monkeypatch.setattr(progress_mod.sys, "__stderr__", _NonTTY())
    assert (
        progress_mod.make_progress_callback(hide_progress=False, stream=_NonTTY())
        is None
    )


def test_progress_callback_on_tty_updates_bar(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys
    import types

    from archivey.cli import progress as progress_mod
    from archivey.internal.extraction_types import ExtractionProgress
    from archivey.types import ArchiveMember, MemberType

    class _TTY(io.StringIO):
        def isatty(self) -> bool:
            return True

    created: list[object] = []

    class _FakeBar:
        def __init__(self, **kwargs: object) -> None:
            created.append(kwargs)
            self.n = 0
            self.total = kwargs.get("total")
            self.desc = kwargs.get("desc")
            self.closed = False

        def set_description(self, desc: str, refresh: bool = True) -> None:
            self.desc = desc

        def update(self, n: int) -> None:
            self.n += n

        def refresh(self) -> None:
            pass

        def close(self) -> None:
            self.closed = True

    def _fake_tqdm(**kwargs: object) -> _FakeBar:
        return _FakeBar(**kwargs)

    # Inject a fake tqdm so this runs in core-only (no real tqdm installed).
    fake_mod = types.ModuleType("tqdm")
    fake_mod.tqdm = _fake_tqdm  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "tqdm", fake_mod)

    cb = progress_mod.make_progress_callback(hide_progress=False, stream=_TTY())
    assert cb is not None

    member = ArchiveMember(type=MemberType.FILE, name="big.bin", size=100)
    cb(
        ExtractionProgress(
            member=member,
            bytes_written=40,
            total_bytes_estimated=100,
            members_done=0,
            members_total=1,
            member_bytes_written=40,
        )
    )
    cb(
        ExtractionProgress(
            member=member,
            bytes_written=100,
            total_bytes_estimated=100,
            members_done=1,
            members_total=1,
            member_bytes_written=100,
        )
    )
    assert len(created) == 1
    assert created[0]["mininterval"] == 0  # type: ignore[index]
    assert created[0]["disable"] is False  # type: ignore[index]


def test_progress_callback_without_tqdm_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sys

    from archivey.cli import progress as progress_mod

    class _TTY(io.StringIO):
        def isatty(self) -> bool:
            return True

    monkeypatch.setitem(sys.modules, "tqdm", None)  # type: ignore[arg-type]
    assert (
        progress_mod.make_progress_callback(hide_progress=False, stream=_TTY()) is None
    )


def test_track_io_reports(sample_zip: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["test", str(sample_zip), "--track-io"]) == EXIT_OK
    err = capsys.readouterr().err
    assert "track-io:" in err
    assert "bytes_decompressed=" in err


def test_test_open_failure_still_prints_summary(
    sample_zip: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Open-time failures must count as FAIL and still reach the summary (F4).
    from archivey.exceptions import ReadError
    from archivey.internal.base_reader import BaseArchiveReader

    def _immediate(self: BaseArchiveReader, members: object = None) -> object:
        raise ReadError("simulated open failure")
        yield  # pragma: no cover — make this a generator

    monkeypatch.setattr(BaseArchiveReader, "stream_members", _immediate)
    assert main(["test", str(sample_zip)]) == EXIT_FAIL
    err = capsys.readouterr().err
    assert "FAIL:" in err
    assert "0 OK, 1 failed" in err


def test_archive_stem_uses_format_extension() -> None:
    from archivey.cli.extract_cmd import _archive_stem
    from archivey.types import ArchiveFormat, ContainerFormat, StreamFormat

    assert _archive_stem(Path("photos.tar.gz"), format=ArchiveFormat.TAR_GZ) == "photos"
    assert _archive_stem(Path(".tar.gz"), format=ArchiveFormat.TAR_GZ) == "archive"
    tar_z = ArchiveFormat(ContainerFormat.TAR, StreamFormat.UNIX_COMPRESS)
    assert _archive_stem(Path("data.tar.Z"), format=tar_z) == "data"


def test_smart_dest_uses_filtered_tops_when_indexed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Indexed ZIP with multi-top archive, but filter selects a single root → cwd.
    monkeypatch.chdir(tmp_path)
    z = _zip(
        tmp_path / "pack.zip",
        {"a/x.txt": b"a", "b/y.txt": b"b", "c/z.txt": b"c"},
    )
    assert main(["extract", str(z), "b/*"]) == EXIT_OK
    assert (tmp_path / "b" / "y.txt").read_bytes() == b"b"
    assert not (tmp_path / "pack").exists()
    assert not (tmp_path / "a").exists()


def test_smart_dest_hoists_single_root_when_no_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Plain TAR has no cheap index — wrap then hoist a single top-level root (R4).
    import tarfile

    monkeypatch.chdir(tmp_path)
    archive = tmp_path / "bundle.tar"
    with tarfile.open(archive, "w") as tf:
        info = tarfile.TarInfo("root/a.txt")
        info.size = 1
        tf.addfile(info, io.BytesIO(b"x"))
    assert main(["extract", str(archive)]) == EXIT_OK
    err = capsys.readouterr().err
    assert "extracting into bundle/" in err
    assert "moved to root/" in err
    assert (tmp_path / "root" / "a.txt").read_bytes() == b"x"
    assert not (tmp_path / "bundle").exists()


def test_smart_dest_keeps_wrapper_for_multi_top_tar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tarfile

    monkeypatch.chdir(tmp_path)
    archive = tmp_path / "messy.tar"
    with tarfile.open(archive, "w") as tf:
        for name, data in (("a.txt", b"a"), ("b.txt", b"b")):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    assert main(["extract", str(archive)]) == EXIT_OK
    assert (tmp_path / "messy" / "a.txt").read_bytes() == b"a"
    assert (tmp_path / "messy" / "b.txt").read_bytes() == b"b"
    assert not (tmp_path / "a.txt").exists()


def test_password_rejected_message_distinct_from_required() -> None:
    from archivey.exceptions import EncryptionError
    from archivey.internal.password import _PasswordCandidates

    candidates = _PasswordCandidates.from_input("wrong")
    with pytest.raises(EncryptionError, match="rejected") as caught:
        candidates.attempt(
            None,
            lambda _pwd: (_ for _ in ()).throw(EncryptionError("nope")),
        )
    assert "Password required" not in caught.value.message

    empty = _PasswordCandidates.from_input(None)
    with pytest.raises(EncryptionError, match="Password required"):
        empty.attempt(
            None,
            lambda _pwd: (_ for _ in ()).throw(EncryptionError("unreachable")),
        )


def _tar(path: Path, entries: dict[str, bytes]) -> Path:
    import tarfile

    with tarfile.open(path, "w") as tf:
        for name, data in entries.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return path


def test_hoist_root_named_like_wrapper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # src.tar containing src/ — the "collision" is the wrapper itself; flatten in
    # place instead of renaming away (H2) or deleting extracted data (H1).
    monkeypatch.chdir(tmp_path)
    archive = _tar(tmp_path / "src.tar", {"src/f.txt": b"data"})
    for overwrite in ("rename", "replace", "error", "skip"):
        assert main(["extract", str(archive), "--overwrite", overwrite]) == EXIT_OK
        err = capsys.readouterr().err
        assert "removed wrapper; content at src/" in err
        assert "moved to src/" not in err
        assert (tmp_path / "src" / "f.txt").read_bytes() == b"data"
        assert not (tmp_path / "src (1)").exists()
        import shutil

        shutil.rmtree(tmp_path / "src")


def test_hoist_single_file_named_like_wrapper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # src.tar containing a single top-level FILE named "src".
    monkeypatch.chdir(tmp_path)
    archive = _tar(tmp_path / "src.tar", {"src": b"solo"})
    assert main(["extract", str(archive)]) == EXIT_OK
    assert (tmp_path / "src").read_bytes() == b"solo"
    assert not (tmp_path / "src (1)").exists()


def _seed_existing_root(tmp_path: Path) -> None:
    (tmp_path / "root").mkdir()
    (tmp_path / "root" / "keep.txt").write_bytes(b"MINE")
    (tmp_path / "root" / "clash.txt").write_bytes(b"MINE")


def _hoist_collision_archive(tmp_path: Path) -> Path:
    return _tar(
        tmp_path / "bundle.tar",
        {"root/new.txt": b"NEW", "root/clash.txt": b"ARCHIVE"},
    )


def test_hoist_merges_like_direct_extraction_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Equivalence contract: hoisting == extracting directly into cwd. Dirs merge;
    # the colliding file gets the library's "name (1)" treatment; nothing deleted.
    monkeypatch.chdir(tmp_path)
    archive = _hoist_collision_archive(tmp_path)
    _seed_existing_root(tmp_path)
    assert main(["extract", str(archive)]) == EXIT_OK
    root = tmp_path / "root"
    assert (root / "keep.txt").read_bytes() == b"MINE"
    assert (root / "clash.txt").read_bytes() == b"MINE"
    assert (root / "clash (1).txt").read_bytes() == b"ARCHIVE"
    assert (root / "new.txt").read_bytes() == b"NEW"
    assert not (tmp_path / "bundle").exists()
    assert not (tmp_path / "root (1)").exists()


def test_hoist_merges_like_direct_extraction_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    archive = _hoist_collision_archive(tmp_path)
    _seed_existing_root(tmp_path)
    assert main(["extract", str(archive), "--overwrite", "skip"]) == EXIT_OK
    root = tmp_path / "root"
    assert (root / "clash.txt").read_bytes() == b"MINE"
    assert (root / "new.txt").read_bytes() == b"NEW"
    assert not (tmp_path / "bundle").exists()


def test_hoist_merges_like_direct_extraction_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    archive = _hoist_collision_archive(tmp_path)
    _seed_existing_root(tmp_path)
    assert main(["extract", str(archive), "--overwrite", "replace"]) == EXIT_OK
    root = tmp_path / "root"
    assert (root / "clash.txt").read_bytes() == b"ARCHIVE"  # only this file replaced
    assert (root / "keep.txt").read_bytes() == b"MINE"
    assert (root / "new.txt").read_bytes() == b"NEW"
    assert not (tmp_path / "bundle").exists()


def test_hoist_collision_under_error_keeps_wrapper_and_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Direct extraction would fail on this collision; the hoist fails the same
    # way but keeps the unmoved remainder safely under the wrapper.
    monkeypatch.chdir(tmp_path)
    archive = _hoist_collision_archive(tmp_path)
    _seed_existing_root(tmp_path)
    assert main(["extract", str(archive), "--overwrite", "error"]) == EXIT_FAIL
    err = capsys.readouterr().err
    assert "Destination already exists" in err
    assert (tmp_path / "root" / "clash.txt").read_bytes() == b"MINE"
    # The conflicting file is still available under the wrapper, not lost.
    assert (tmp_path / "bundle" / "root" / "clash.txt").read_bytes() == b"ARCHIVE"


def test_hoist_never_deletes_colliding_tree_under_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Archive member "root/clash" is a FILE; on disk ./root/clash is a DIRECTORY.
    # replace must not rmtree the directory — it fails and keeps everything.
    monkeypatch.chdir(tmp_path)
    archive = _tar(tmp_path / "bundle.tar", {"root/clash": b"ARCHIVE"})
    (tmp_path / "root" / "clash").mkdir(parents=True)
    (tmp_path / "root" / "clash" / "precious.txt").write_bytes(b"MINE")
    assert main(["extract", str(archive), "--overwrite", "replace"]) == EXIT_FAIL
    assert (tmp_path / "root" / "clash" / "precious.txt").read_bytes() == b"MINE"
    assert (tmp_path / "bundle" / "root" / "clash").read_bytes() == b"ARCHIVE"


def test_cli_logging_leaves_no_global_state(sample_zip: Path) -> None:
    # The D4 handler must be scoped to one invocation: a leaked handler or
    # propagate=False blinds caplog-based library tests (pytest 8.3 floor).
    import logging

    root = logging.getLogger("archivey")
    assert main(["list", str(sample_zip)]) == EXIT_OK
    assert root.handlers == []
    assert root.propagate is True
    assert root.level == logging.NOTSET


# --- cli-product review follow-ups (P3 / P5 / P6 / P10–P13 / D1) -------------------------


def test_missing_archive_uses_prose_not_errno_repr(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "nope.zip"
    assert main(["list", str(missing)]) == EXIT_FAIL
    err = capsys.readouterr().err
    assert "cannot open" in err
    assert "no such file or directory" in err.lower()
    assert "[Errno" not in err


def test_extract_missing_archive_only_requires_archive(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["x"]) == EXIT_USAGE
    err = capsys.readouterr().err
    assert "required: archive" in err
    assert "patterns" not in err.split("required:")[-1]


def test_dash_x_hints_bare_verb(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["-x", "a.zip"]) == EXIT_USAGE
    err = capsys.readouterr().err
    assert "unrecognized arguments: -x" in err
    assert "bare words" in err
    assert "archivey x ARCHIVE" in err


def test_help_includes_examples(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--help"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "examples:" in out
    assert "archivey x archive.zip" in out


def test_password_eof_treated_as_no_password(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from archivey.cli import password as password_mod
    from archivey.config import PasswordRequest
    from tests.zipcrypto import build_zipcrypto_zip

    blob = build_zipcrypto_zip(b"secret", b"zc.txt", b"hello")
    path = tmp_path / "zc.zip"
    path.write_bytes(blob)

    monkeypatch.setattr(password_mod.sys.stdin, "isatty", lambda: True)

    def _eof(_prompt: str = "") -> str:
        raise EOFError

    monkeypatch.setattr(password_mod.getpass, "getpass", _eof)
    provider = password_mod.resolve_password(None)
    assert callable(provider)
    assert provider(PasswordRequest(member=None, attempt=1)) is None

    # End-to-end: EOF at prompt must not dump a traceback (P5).
    err = io.StringIO()
    assert main(["t", str(path)], out=io.StringIO(), err=err) == EXIT_FAIL
    text = err.getvalue()
    assert "Traceback" not in text
    assert "EOFError" not in text
    assert "Password required" in text


def test_escape_member_name_controls() -> None:
    from archivey.cli.format import escape_member_name

    assert escape_member_name("ok.txt") == "ok.txt"
    assert "\\x1b" in escape_member_name("evil\x1b[31mRED\x1b[0m.txt")
    assert "\\r" in escape_member_name("line1\rOK  fine.txt")
    assert "\\\\" in escape_member_name("a\\b")


def test_list_escapes_control_bytes_in_names(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    z = _zip(
        tmp_path / "hostile.zip", {"evil\x1b[31m.txt": b"x", "line1\rok.txt": b"y"}
    )
    assert main(["list", str(z)]) == EXIT_OK
    out = capsys.readouterr().out
    assert "\x1b" not in out
    assert "\\x1b" in out
    assert "\\r" in out


def test_list_marks_anti_and_non_current() -> None:
    from datetime import datetime

    from archivey.cli.format import format_member_line
    from archivey.types import ArchiveMember, MemberType

    anti = ArchiveMember(type=MemberType.ANTI, name="gone.txt")
    assert format_member_line(anti).startswith("A-")

    old = ArchiveMember(
        type=MemberType.FILE,
        name="a.txt",
        size=1,
        modified=datetime(2026, 1, 1),
        is_current=False,
    )
    assert format_member_line(old).startswith("f~")

    enc = ArchiveMember(
        type=MemberType.FILE,
        name="a.txt",
        size=1,
        is_encrypted=True,
        is_current=False,
    )
    assert format_member_line(enc).startswith("fE")


def test_extract_summary_names_single_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    z = _zip(tmp_path / "one.zip", {"root/a.txt": b"a", "root/b.txt": b"b"})
    assert main(["extract", str(z)]) == EXIT_OK
    err = capsys.readouterr().err
    assert "→ root/" in err
    assert "→ ." not in err


def test_truncated_zip_message_is_prose_not_repr(tmp_path: Path) -> None:
    import zipfile as zf

    from archivey import open_archive
    from archivey.exceptions import CorruptionError

    buf = io.BytesIO()
    with zf.ZipFile(buf, "w") as archive:
        archive.writestr("a.txt", "hello")
    path = tmp_path / "truncated.zip"
    path.write_bytes(buf.getvalue()[:20])
    with pytest.raises(CorruptionError) as caught:
        open_archive(path)
    msg = str(caught.value)
    assert "BadZipFile" not in msg
    assert "ArchiveFormat.ZIP" not in msg
    assert "format=ZIP" in msg
    assert "truncated" in msg.lower() or "corrupt" in msg.lower()


def test_stored_zipcrypto_provider_none_is_password_required(tmp_path: Path) -> None:
    import zipfile as zf

    from archivey import open_archive
    from archivey.exceptions import EncryptionError
    from tests.zipcrypto import build_zipcrypto_zip

    blob = build_zipcrypto_zip(
        b"secret", b"zc.txt", b"hello world", compression=zf.ZIP_STORED
    )
    path = tmp_path / "zc_stored.zip"
    path.write_bytes(blob)

    with pytest.raises(EncryptionError, match="Password required") as caught:
        with open_archive(path, password=lambda _r: None) as ar:
            ar.read(next(m for m in ar.members() if m.is_file))
    assert "Wrong password" not in caught.value.message


# --- Q1 / P1: extract continue-on-error + exit 3 + --stop-on-error -----------------------


def test_extract_continues_after_traversal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from archivey.cli.exit_codes import EXIT_POLICY

    monkeypatch.chdir(tmp_path)
    archive = _tar(
        tmp_path / "evil.tar",
        {
            "../escape.txt": b"bad",
            "safe1.txt": b"ok1",
            "safe2.txt": b"ok2",
            "dir/nested.txt": b"ok3",
        },
    )
    assert main(["extract", str(archive), "-d", "out"]) == EXIT_POLICY
    err = capsys.readouterr().err
    assert "blocked:" in err
    assert "escape.txt" in err or "../escape" in err
    assert "blocked" in err.split("→")[0]  # summary mentions blocked
    assert (tmp_path / "out" / "safe1.txt").read_bytes() == b"ok1"
    assert (tmp_path / "out" / "safe2.txt").read_bytes() == b"ok2"
    assert (tmp_path / "out" / "dir" / "nested.txt").read_bytes() == b"ok3"
    assert not (tmp_path / "escape.txt").exists()


def test_extract_stop_on_error_aborts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from archivey.cli.exit_codes import EXIT_POLICY

    monkeypatch.chdir(tmp_path)
    # Put the traversal first so STOP aborts before safe members.
    archive = _tar(
        tmp_path / "evil.tar",
        {
            "../escape.txt": b"bad",
            "safe.txt": b"ok",
        },
    )
    code = main(["extract", str(archive), "-d", "out", "--stop-on-error"])
    assert code == EXIT_POLICY
    err = capsys.readouterr().err
    assert "extraction stopped" in err
    assert not (tmp_path / "out" / "safe.txt").exists()


def test_extract_corrupt_member_continues_with_exit_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """CRC mismatch: recoverable members extracted; exit 1 (FAILED, not policy)."""
    monkeypatch.chdir(tmp_path)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a.txt", b"hello")
        zf.writestr("b.txt", b"world")
        zf.writestr("c.txt", b"end")
    data = bytearray(buf.getvalue())
    # Flip the central-directory CRC for b.txt so open succeeds but extract fails.
    pos = 0
    while True:
        i = data.find(b"PK\x01\x02", pos)
        if i < 0:
            break
        name_len = int.from_bytes(data[i + 28 : i + 30], "little")
        name = bytes(data[i + 46 : i + 46 + name_len])
        if name == b"b.txt":
            data[i + 16] ^= 0xFF
            break
        pos = i + 4
    path = tmp_path / "badcrc.zip"
    path.write_bytes(bytes(data))

    assert main(["extract", str(path), "-d", "out"]) == EXIT_FAIL
    err = capsys.readouterr().err
    assert "failed:" in err
    assert "b.txt" in err
    assert "extraction stopped" not in err
    assert (tmp_path / "out" / "a.txt").read_bytes() == b"hello"
    assert (tmp_path / "out" / "c.txt").read_bytes() == b"end"
    assert not (tmp_path / "out" / "b.txt").exists()
