"""Multi-password ZIP disambiguation for traditional ZipCrypto."""

from __future__ import annotations

import io
import zipfile
from collections.abc import Callable
from typing import Any, cast

import pytest

from archivey import PasswordRequest, open_archive
from archivey.exceptions import CorruptionError, EncryptionError
from archivey.internal.backends import zip_reader
from tests.zipcrypto import (
    build_zipcrypto_zip,
    corrupt_zipcrypto_payload,
    find_check_byte_collision,
    find_check_byte_collisions,
)

RIGHT = b"very_secret_password"
DATA = b"This is very secret" * 8
NAME = "very_secret.txt"
PasswordArg = (
    str
    | bytes
    | list[str | bytes]
    | Callable[[PasswordRequest], str | bytes | None]
)

COMPRESSION_METHODS = [
    pytest.param(zipfile.ZIP_STORED, id="stored"),
    pytest.param(zipfile.ZIP_DEFLATED, id="deflated"),
    pytest.param(zipfile.ZIP_BZIP2, id="bzip2"),
    pytest.param(zipfile.ZIP_LZMA, id="lzma"),
]


def _read_member(blob: bytes, password: PasswordArg) -> bytes:
    with open_archive(io.BytesIO(blob), password=password) as ar:
        member = next(m for m in ar.members() if m.name == NAME)
        with ar.open(member) as fh:
            return fh.read()


@pytest.mark.parametrize("compression", COMPRESSION_METHODS)
def test_wrong_candidate_false_accept_does_not_shadow_right_password(
    compression: int,
) -> None:
    blob = build_zipcrypto_zip(
        RIGHT, NAME.encode(), DATA, compression=compression
    )
    collider = find_check_byte_collision(blob, NAME, RIGHT)

    assert collider != RIGHT
    assert _read_member(blob, [collider, RIGHT]) == DATA


def test_all_wrong_colliding_passwords_report_ambiguous_failure() -> None:
    blob = build_zipcrypto_zip(RIGHT, NAME.encode(), DATA)
    colliders = find_check_byte_collisions(blob, NAME, RIGHT, count=2)

    with pytest.raises(
        EncryptionError, match=r"password\(s\) may be wrong, or .* may be corrupt"
    ):
        _read_member(blob, colliders)


def test_provider_continues_after_colliding_password() -> None:
    blob = build_zipcrypto_zip(RIGHT, NAME.encode(), DATA)
    collider = find_check_byte_collision(blob, NAME, RIGHT)
    seen: list[PasswordRequest] = []

    def provider(request: PasswordRequest) -> bytes | None:
        seen.append(request)
        return {1: collider, 2: RIGHT}.get(request.attempt)

    assert _read_member(blob, provider) == DATA
    assert [request.attempt for request in seen] == [1, 2]
    assert all(request.member is not None for request in seen)


@pytest.mark.parametrize("passwords", [[RIGHT], [RIGHT, RIGHT]], ids=["one", "duplicate"])
@pytest.mark.parametrize("compression", COMPRESSION_METHODS)
def test_single_distinct_candidate_is_not_eagerly_read(
    passwords: list[bytes], compression: int
) -> None:
    blob = corrupt_zipcrypto_payload(
        build_zipcrypto_zip(RIGHT, NAME.encode(), DATA, compression=compression)
    )

    with open_archive(io.BytesIO(blob), password=passwords) as ar:
        stream = ar.open(NAME)
        with stream, pytest.raises(CorruptionError):
            stream.read()


def test_corrupt_encrypted_data_with_multiple_candidates_reports_ambiguity() -> None:
    blob = corrupt_zipcrypto_payload(
        build_zipcrypto_zip(RIGHT, NAME.encode(), DATA)
    )

    with open_archive(io.BytesIO(blob), password=[RIGHT, b"also-wrong"]) as ar:
        with pytest.raises(
            EncryptionError, match=r"password\(s\) may be wrong, or .* may be corrupt"
        ):
            ar.open(NAME)


def test_structural_bad_zip_is_corruption_not_password_ambiguity() -> None:
    blob = bytearray(build_zipcrypto_zip(RIGHT, NAME.encode(), DATA))
    blob[30] ^= 0x01  # local-header name no longer matches the central directory

    with open_archive(io.BytesIO(blob), password=[RIGHT, b"also-wrong"]) as ar:
        with pytest.raises(CorruptionError, match="Error reading ZIP archive"):
            ar.open(NAME)


def test_provider_encryption_error_is_not_rewritten_after_candidate_failure() -> None:
    blob = build_zipcrypto_zip(RIGHT, NAME.encode(), DATA)
    collider = find_check_byte_collision(blob, NAME, RIGHT)
    provider_error = EncryptionError("password service unavailable")

    def provider(request: PasswordRequest) -> bytes:
        if request.attempt == 1:
            return collider
        raise provider_error

    with pytest.raises(EncryptionError, match="password service unavailable") as caught:
        _read_member(blob, provider)

    assert caught.value is provider_error


def test_winning_candidate_is_decompressed_once(monkeypatch: pytest.MonkeyPatch) -> None:
    blob = build_zipcrypto_zip(RIGHT, NAME.encode(), DATA)
    collider = find_check_byte_collision(blob, NAME, RIGHT)

    with open_archive(io.BytesIO(blob), password=[collider, RIGHT]) as ar:
        archive = cast(Any, ar)._archive  # focused ZIP backend test
        original_open = archive.open
        tried: list[bytes | None] = []

        def tracking_open(
            name: str | zipfile.ZipInfo,
            mode: str = "r",
            pwd: bytes | None = None,
            *,
            force_zip64: bool = False,
        ) -> zipfile.ZipExtFile:
            tried.append(pwd)
            return original_open(
                name, mode=mode, pwd=pwd, force_zip64=force_zip64
            )

        monkeypatch.setattr(archive, "open", tracking_open)
        assert ar.read(NAME) == DATA

    assert tried == [collider, RIGHT]


def test_spooled_winner_supports_partial_reads_after_disk_rollover(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(zip_reader, "_VALIDATION_SPOOL_MAX_SIZE", 32)
    blob = build_zipcrypto_zip(RIGHT, NAME.encode(), DATA)

    with open_archive(io.BytesIO(blob), password=[RIGHT, b"also-wrong"]) as ar:
        with ar.open(NAME) as stream:
            assert stream.read(17) + stream.read() == DATA


def test_provider_password_is_reused_as_known_good() -> None:
    blob = build_zipcrypto_zip(RIGHT, NAME.encode(), DATA)
    calls = 0

    def provider(request: PasswordRequest) -> bytes:
        nonlocal calls
        calls += 1
        return RIGHT

    with open_archive(io.BytesIO(blob), password=provider) as ar:
        assert ar.read(NAME) == DATA
        assert ar.read(NAME) == DATA

    assert calls == 1


def test_unrelated_oserror_propagates_and_failed_stream_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    blob = build_zipcrypto_zip(RIGHT, NAME.encode(), DATA)

    class FailingStream(io.BytesIO):
        def read(self, _size: int = -1) -> bytes:
            raise OSError("source storage unavailable")

    failed_stream = FailingStream()

    with open_archive(io.BytesIO(blob), password=[b"one", b"two"]) as ar:
        archive = cast(Any, ar)._archive  # focused ZIP backend test
        monkeypatch.setattr(archive, "open", lambda *_args, **_kwargs: failed_stream)
        with pytest.raises(OSError, match="source storage unavailable"):
            ar.open(NAME)

    assert failed_stream.closed


def test_source_close_failure_still_closes_validation_spool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    blob = build_zipcrypto_zip(RIGHT, NAME.encode(), DATA)
    created_spools: list[io.BufferedIOBase] = []
    original_spool_factory = zip_reader.tempfile.SpooledTemporaryFile

    def tracking_spool_factory(*args: Any, **kwargs: Any) -> io.BufferedIOBase:
        spool = original_spool_factory(*args, **kwargs)
        created_spools.append(spool)
        return spool

    class CloseFailingStream(io.BytesIO):
        close_attempted = False

        def close(self) -> None:
            self.close_attempted = True
            super().close()
            raise OSError("source close failed")

    failed_source = CloseFailingStream()
    monkeypatch.setattr(
        zip_reader.tempfile, "SpooledTemporaryFile", tracking_spool_factory
    )

    with open_archive(io.BytesIO(blob), password=[b"one", b"two"]) as ar:
        archive = cast(Any, ar)._archive
        monkeypatch.setattr(archive, "open", lambda *_args, **_kwargs: failed_source)
        with pytest.raises(OSError, match="source close failed"):
            ar.open(NAME)

    assert failed_source.close_attempted
    assert len(created_spools) == 1
    assert created_spools[0].closed
