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
    assert main(["list", "-"]) == EXIT_FAIL


def test_reserved_verbs(sample_zip: Path) -> None:
    assert main(["create", str(sample_zip)]) == EXIT_USAGE
    assert main(["hash", str(sample_zip)]) == EXIT_USAGE
    assert main(["convert", str(sample_zip)]) == EXIT_USAGE


def test_salvage_reserved(sample_zip: Path) -> None:
    assert main(["list", str(sample_zip), "--salvage"]) == EXIT_FAIL
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
        == EXIT_FAIL
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


def test_extract_strict_abort_explains_stop(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Windows-reserved device names are still rejected under STRICT; OnError.STOP must
    # say so clearly (trailing-dot names are stripped, not rejected — see #123).
    bad = _zip(tmp_path / "bad.zip", {"NUL": b"x", "ok.txt": b"y"})
    dest = tmp_path / "out"
    code = main(["extract", str(bad), "-d", str(dest), "--policy", "strict"])
    assert code == EXIT_FAIL
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
    assert "ArchiveFormat.ZIP" in out
    assert "format:" in out
    assert main(["detect", str(sample_zip)]) == EXIT_OK


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
    # Simulate missing tqdm: progress helper returns None; extract still works.
    import archivey.cli.progress as progress_mod

    monkeypatch.setattr(progress_mod, "make_progress_callback", lambda **_: None)
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

    # Patch the tqdm symbol the helper imports at call time.
    import tqdm as tqdm_mod

    monkeypatch.setattr(tqdm_mod, "tqdm", _fake_tqdm)
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


def test_track_io_reports(sample_zip: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["test", str(sample_zip), "--track-io"]) == EXIT_OK
    err = capsys.readouterr().err
    assert "track-io:" in err
    assert "bytes_decompressed=" in err
