"""Tests for password candidates and provider (Phase 5 stage 2)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from archivey import PasswordRequest, open_archive
from archivey.exceptions import EncryptionError
from archivey.internal.password import _PasswordCandidates
from archivey.types import ArchiveMember, MemberType
from tests.conftest import requires_binary


def _make_multi_password_zip(path: Path) -> None:
    (path.parent / "f1.txt").write_bytes(b"secret1\n")
    (path.parent / "f2.txt").write_bytes(b"secret2\n")
    subprocess.run(
        ["7z", "a", "-tzip", str(path), "f1.txt", "-psecret1", "-y"],
        cwd=path.parent,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["7z", "a", "-tzip", str(path), "f2.txt", "-psecret2", "-y"],
        cwd=path.parent,
        check=True,
        capture_output=True,
    )


def test_password_candidates_provider_header_request() -> None:
    seen: list[PasswordRequest] = []

    def provider(request: PasswordRequest) -> str | None:
        seen.append(request)
        return None

    candidates = _PasswordCandidates.from_input(provider)
    with pytest.raises(EncryptionError):
        candidates.attempt(
            None,
            lambda _pwd: (_ for _ in ()).throw(EncryptionError("wrong")),
        )
    assert len(seen) == 1
    assert seen[0].member is None
    assert seen[0].attempt == 1


def test_password_candidates_provider_attempt_increments() -> None:
    attempts: list[int] = []

    def provider(request: PasswordRequest) -> str | None:
        attempts.append(request.attempt)
        # Distinct guesses each time: a *repeated* guess is provably useless (decrypt is
        # deterministic in the password) and terminates the loop early — see
        # test_password_candidates_provider_repeat_terminates.
        if request.attempt < 3:
            return f"wrong{request.attempt}"
        return None

    candidates = _PasswordCandidates.from_input(provider)
    with pytest.raises(EncryptionError):
        candidates.attempt(
            None,
            lambda _pwd: (_ for _ in ()).throw(EncryptionError("bad")),
        )
    assert attempts == [1, 2, 3]


def test_password_candidates_provider_reuses_known_good() -> None:
    calls = 0

    def provider(request: PasswordRequest) -> str | None:
        nonlocal calls
        calls += 1
        return "from-provider"

    candidates = _PasswordCandidates.from_input(provider)

    def decrypt(password: bytes) -> bytes:
        if password == b"from-provider":
            return b"ok"
        raise EncryptionError("bad")

    first = ArchiveMember(type=MemberType.FILE, name="first")
    second = ArchiveMember(type=MemberType.FILE, name="second")
    assert candidates.attempt(first, decrypt) == b"ok"
    assert candidates.attempt(second, decrypt) == b"ok"
    assert calls == 1


def test_password_candidates_provider_repeat_terminates() -> None:
    # A provider that keeps returning the same wrong password can make no progress;
    # attempt() must stop instead of re-running decrypt on it forever.
    calls = 0

    def provider(request: PasswordRequest) -> str | None:
        nonlocal calls
        calls += 1
        return "same-wrong"

    decrypt_calls = 0

    def decrypt(password: bytes) -> bytes:
        nonlocal decrypt_calls
        decrypt_calls += 1
        raise EncryptionError("bad")

    candidates = _PasswordCandidates.from_input(provider)
    with pytest.raises(EncryptionError):
        candidates.attempt(None, decrypt)
    # The repeated password is tried once; the repeat breaks the loop.
    assert calls == 2
    assert decrypt_calls == 1


def test_password_candidates_provider_repeat_of_candidate_terminates() -> None:
    # A provider echoing a static candidate that already failed also makes no progress.
    decrypt_calls: list[bytes] = []

    def decrypt(password: bytes) -> bytes:
        decrypt_calls.append(password)
        raise EncryptionError("bad")

    def provider(request: PasswordRequest) -> str | None:
        return "cand"

    candidates = _PasswordCandidates(candidates=[b"cand"], provider=provider)
    with pytest.raises(EncryptionError):
        candidates.attempt(None, decrypt)
    # "cand" is tried once as a static candidate; the provider echo doesn't re-run it.
    assert decrypt_calls == [b"cand"]


def test_password_candidates_sequence_order() -> None:
    tried: list[bytes] = []

    def decrypt(password: bytes) -> bytes:
        tried.append(password)
        if password == b"second":
            return b"data"
        raise EncryptionError("bad")

    candidates = _PasswordCandidates.from_input([b"first", b"second"])
    assert candidates.attempt(None, decrypt) == b"data"
    assert tried == [b"first", b"second"]


@requires_binary("7z")
def test_multi_password_zip_streaming_pass(tmp_path: Path) -> None:
    archive = tmp_path / "mpw.zip"
    _make_multi_password_zip(archive)

    with open_archive(archive, password=[b"secret1", b"secret2"], streaming=True) as ar:
        contents = {
            member.name: stream.read() if stream is not None else None
            for member, stream in ar.stream_members()
            if member.type is MemberType.FILE
        }
    assert contents == {"f1.txt": b"secret1\n", "f2.txt": b"secret2\n"}


@requires_binary("7z")
def test_zip_provider_receives_member(tmp_path: Path) -> None:
    archive = tmp_path / "one.zip"
    (tmp_path / "only.txt").write_bytes(b"hello\n")
    subprocess.run(
        ["7z", "a", "-tzip", str(archive), "only.txt", "-phunter2", "-y"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    seen: list[PasswordRequest] = []

    def provider(request: PasswordRequest) -> str | None:
        seen.append(request)
        return "hunter2"

    with open_archive(archive, password=provider) as ar:
        assert ar.read("only.txt") == b"hello\n"
    assert len(seen) == 1
    assert seen[0].member is not None
    assert seen[0].member.name == "only.txt"
    assert seen[0].attempt == 1
