"""Cooperative concurrency / lifecycle / password tests (provisional CONCURRENT).

Heavier free-threaded and adversarial lock-order stress is deferred with D15 — see
``openspec/changes/concurrent-member-streams/tasks.md`` reminders on 7.3/7.4/7.6/7.8.
"""

from __future__ import annotations

import gzip
import io
from pathlib import Path

import pytest

from archivey import (
    ArchiveyError,
    ArchiveyUsageError,
    ConcurrentAccessError,
    MemberStreams,
    open_archive,
)
from archivey.internal.password import _PasswordCandidates
from archivey.internal.streams.archive_stream import ArchiveStream


def _dir_with_files(tmp_path: Path) -> Path:
    (tmp_path / "a.txt").write_bytes(b"aaa")
    (tmp_path / "b.txt").write_bytes(b"bbb")
    return tmp_path


# --- 7.3 cooperative state / overlap ----------------------------------------------------


def test_open_during_stream_members_raises(tmp_path: Path) -> None:
    root = _dir_with_files(tmp_path)
    with open_archive(root) as reader:
        it = reader.stream_members()
        member, stream = next(it)
        assert stream is not None
        with pytest.raises(ArchiveyUsageError, match="already active"):
            reader.open("b.txt")
        assert stream.read() == b"aaa"
        # Exhaust / close the pass so the reader can be closed cleanly.
        list(it)


def test_members_then_open_ok(tmp_path: Path) -> None:
    root = _dir_with_files(tmp_path)
    with open_archive(root, member_streams=MemberStreams.CONCURRENT) as reader:
        names = {m.name for m in reader.members()}
        assert names == {"a.txt", "b.txt"}
        s1 = reader.open("a.txt")
        s2 = reader.open("b.txt")
        assert s1.read() == b"aaa"
        assert s2.read() == b"bbb"
        s1.close()
        s2.close()


def test_close_during_stream_members_raises(tmp_path: Path) -> None:
    root = _dir_with_files(tmp_path)
    reader = open_archive(root)
    it = reader.stream_members()
    next(it)
    with pytest.raises(ArchiveyUsageError, match="is active"):
        reader.close()
    list(it)
    reader.close()


# --- 7.4 cooperative lifecycle ----------------------------------------------------------


def test_escaped_stream_survives_reader_close(tmp_path: Path) -> None:
    root = _dir_with_files(tmp_path)
    reader = open_archive(root)
    stream = reader.open("a.txt")
    reader.close()
    with pytest.raises(ArchiveyUsageError, match="closed"):
        reader.open("b.txt")
    assert stream.read() == b"aaa"
    stream.close()


def test_caller_owned_source_not_closed_by_reader() -> None:
    buf = io.BytesIO(gzip.compress(b"payload"))
    with open_archive(buf) as reader:
        with reader.open(reader.members()[0]) as stream:
            assert stream.read() == b"payload"
    assert not buf.closed


def test_usage_errors_escape_archivey_error(tmp_path: Path) -> None:
    root = _dir_with_files(tmp_path)
    with open_archive(root) as reader:
        s = reader.open("a.txt")
        try:
            with pytest.raises(ConcurrentAccessError):
                reader.open("b.txt")
        finally:
            s.close()
    assert not issubclass(ConcurrentAccessError, ArchiveyError)


# --- 7.5 password (simplified D10) ------------------------------------------------------


def test_password_provider_reentry_raises() -> None:
    box: dict[str, _PasswordCandidates] = {}

    def provider(req):  # noqa: ANN001
        # Same-reader reentry while the provider lock marks depth > 0.
        with pytest.raises(ArchiveyUsageError, match="reentered"):
            box["c"].ask_provider(None, 99)
        return b"pw" if req.attempt == 1 else None

    candidates = _PasswordCandidates.from_input(provider)
    box["c"] = candidates
    assert candidates.attempt(None, lambda _p: b"data") == b"data"


def test_password_known_good_promotion_converges() -> None:
    state = _PasswordCandidates.from_input([b"wrong", b"right"])
    calls: list[bytes] = []

    def decrypt(password: bytes) -> bytes:
        calls.append(password)
        if password != b"right":
            from archivey.exceptions import EncryptionError

            raise EncryptionError("nope")
        return b"ok"

    assert state.attempt(None, decrypt) == b"ok"
    assert calls == [b"wrong", b"right"]
    # Second unit prefers known-good first.
    calls.clear()
    assert state.attempt(None, decrypt) == b"ok"
    assert calls[0] == b"right"


# --- 7.6 ArchiveStream open_fn without stream lock --------------------------------------


def test_archive_stream_open_fn_runs_outside_stream_lock() -> None:
    held = {"during_open": False}

    def open_fn() -> io.BytesIO:
        held["during_open"] = True
        return io.BytesIO(b"xyz")

    stream = ArchiveStream(
        open_fn,
        translate=lambda _e: None,
        lazy=True,
        seekable=False,
    )
    # open_fn must complete without needing the stream lock (a non-reentrant Lock would
    # deadlock if _ensure_open held it across the call).
    assert stream._open_lock.acquire(blocking=False)
    stream._open_lock.release()
    assert stream.read() == b"xyz"
    assert held["during_open"]


# --- 7.7 stream_members ownership -------------------------------------------------------


def test_stream_members_advance_closes_prior(tmp_path: Path) -> None:
    root = _dir_with_files(tmp_path)
    with open_archive(root) as reader:
        it = reader.stream_members()
        _m1, s1 = next(it)
        assert s1 is not None
        _m2, s2 = next(it)
        assert s2 is not None
        assert s1.closed
        assert s2.read() == b"bbb"
        list(it)


def test_stream_members_abandon_releases_pass(tmp_path: Path) -> None:
    root = _dir_with_files(tmp_path)
    with open_archive(root) as reader:
        it = reader.stream_members()
        next(it)
        it.close()  # abandon
        # Pass released: a new open is allowed.
        with reader.open("a.txt") as s:
            assert s.read() == b"aaa"
