"""Multi-password ZIP disambiguation for traditional ZipCrypto."""

from __future__ import annotations

import io
import os
import tempfile
import zipfile
import zlib
from collections.abc import Callable
from typing import Any, cast

import pytest

from archivey import PasswordRequest, open_archive
from archivey.exceptions import CorruptionError, EncryptionError
from archivey.internal import password_confirm, zipcrypto
from archivey.internal.backends import zip_reader
from archivey.internal.password_confirm import CONFIRM_PREFIX_BYTES
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
    str | bytes | list[str | bytes] | Callable[[PasswordRequest], str | bytes | None]
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
    blob = build_zipcrypto_zip(RIGHT, NAME.encode(), DATA, compression=compression)
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


@pytest.mark.parametrize(
    "passwords", [[RIGHT], [RIGHT, RIGHT]], ids=["one", "duplicate"]
)
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
    blob = corrupt_zipcrypto_payload(build_zipcrypto_zip(RIGHT, NAME.encode(), DATA))

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


def test_confirmed_winner_is_reopened_fresh(monkeypatch: pytest.MonkeyPatch) -> None:
    """Confirmation opens the winner once to validate, then re-opens for the caller."""
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
            return original_open(name, mode=mode, pwd=pwd, force_zip64=force_zip64)

        monkeypatch.setattr(archive, "open", tracking_open)
        assert ar.read(NAME) == DATA

    assert tried == [collider, RIGHT, RIGHT]


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


def test_source_close_failure_after_prefix_confirm_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A close failure on the confirmation stream must not be swallowed."""
    blob = build_zipcrypto_zip(RIGHT, NAME.encode(), DATA)

    class CloseFailingStream(io.BytesIO):
        close_attempted = False

        def read(self, size: int = -1) -> bytes:
            # Pretend confirmation consumed the prefix successfully.
            if size == 0:
                return b""
            return b"x" * (size if size > 0 else 16)

        def close(self) -> None:
            self.close_attempted = True
            super().close()
            raise OSError("source close failed")

    failed_source = CloseFailingStream()

    with open_archive(io.BytesIO(blob), password=[b"one", b"two"]) as ar:
        archive = cast(Any, ar)._archive
        monkeypatch.setattr(archive, "open", lambda *_args, **_kwargs: failed_source)
        with pytest.raises(OSError, match="source close failed"):
            ar.open(NAME)

    assert failed_source.close_attempted


# ---------------------------------------------------------------------------
# Task 1.2 — wrong-key confirmation fails within the bound (wide margin)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "compression",
    [
        pytest.param(zipfile.ZIP_DEFLATED, id="deflated"),
        pytest.param(zipfile.ZIP_BZIP2, id="bzip2"),
        pytest.param(zipfile.ZIP_LZMA, id="lzma"),
    ],
)
def test_wrong_key_rejected_within_tight_prefix_bound(
    compression: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Even a 4 KiB confirmation bound rejects colliding wrong keys (≪ 64 KiB)."""
    monkeypatch.setattr(password_confirm, "CONFIRM_PREFIX_BYTES", 4 * 1024)
    monkeypatch.setattr(zip_reader, "CONFIRM_PREFIX_BYTES", 4 * 1024)
    blob = build_zipcrypto_zip(RIGHT, NAME.encode(), DATA * 64, compression=compression)
    collider = find_check_byte_collision(blob, NAME, RIGHT)
    assert _read_member(blob, [collider, RIGHT]) == DATA * 64


# ---------------------------------------------------------------------------
# Task 3.5 — large compressed member confirmation is bounded
# ---------------------------------------------------------------------------


def test_large_compressed_confirmation_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plaintext = b"bounded-confirm-payload\n" * 8000  # well over 64 KiB when repeated
    plaintext = plaintext * 8  # ~1.5 MiB+
    assert len(plaintext) > CONFIRM_PREFIX_BYTES
    blob = build_zipcrypto_zip(
        RIGHT, NAME.encode(), plaintext, compression=zipfile.ZIP_DEFLATED
    )
    collider = find_check_byte_collision(blob, NAME, RIGHT)

    created_temps: list[str] = []
    real_named = tempfile.NamedTemporaryFile

    def tracking_temp(*args: Any, **kwargs: Any) -> Any:
        tmp = real_named(*args, **kwargs)
        created_temps.append(tmp.name)
        return tmp

    monkeypatch.setattr(tempfile, "NamedTemporaryFile", tracking_temp)
    monkeypatch.setattr(tempfile, "SpooledTemporaryFile", tracking_temp)

    bytes_read = {"n": 0}
    original_exact = zip_reader.read_exact

    def counting_exact(stream: Any, n: int) -> bytes:
        data = original_exact(stream, n)
        bytes_read["n"] = max(bytes_read["n"], len(data))
        return data

    monkeypatch.setattr(zip_reader, "read_exact", counting_exact)

    assert _read_member(blob, [collider, RIGHT]) == plaintext
    assert created_temps == []
    assert bytes_read["n"] <= CONFIRM_PREFIX_BYTES


# ---------------------------------------------------------------------------
# Task 3.6 — STORED shared CRC pass
# ---------------------------------------------------------------------------


def test_stored_uses_shared_crc_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    plaintext = os.urandom(128 * 1024)
    blob = build_zipcrypto_zip(
        RIGHT, NAME.encode(), plaintext, compression=zipfile.ZIP_STORED
    )
    collider = find_check_byte_collision(blob, NAME, RIGHT)

    crc_calls = {"n": 0}
    original = zipcrypto.parallel_plaintext_crc32

    def counting_crc(*args: Any, **kwargs: Any) -> Any:
        crc_calls["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(zipcrypto, "parallel_plaintext_crc32", counting_crc)
    monkeypatch.setattr(zip_reader, "parallel_plaintext_crc32", counting_crc)

    assert _read_member(blob, [collider, RIGHT]) == plaintext
    assert crc_calls["n"] == 1


def test_stored_crc_match_ties_resolve_by_candidate_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plaintext = b"tie-break stored"
    blob = build_zipcrypto_zip(
        RIGHT, NAME.encode(), plaintext, compression=zipfile.ZIP_STORED
    )
    colliders = find_check_byte_collisions(blob, NAME, RIGHT, count=2)
    expected = zlib.crc32(plaintext) & 0xFFFFFFFF

    def fake_crc(
        passwords: list[bytes], header: bytes, body: Any, **kwargs: Any
    ) -> list[tuple[bytes, int]]:
        while body.read(65536):
            pass
        return [(p, expected) for p in passwords]

    monkeypatch.setattr(zipcrypto, "parallel_plaintext_crc32", fake_crc)
    monkeypatch.setattr(zip_reader, "parallel_plaintext_crc32", fake_crc)

    with open_archive(
        io.BytesIO(blob), password=[colliders[0], colliders[1], RIGHT]
    ) as ar:
        stream = ar.open(NAME)
        # Earliest CRC "match" wins confirmation and is recorded known-good.
        assert cast(Any, ar)._passwords._known_good[0] == colliders[0]
        with stream, pytest.raises(CorruptionError):
            stream.read()


def test_stored_caller_stream_is_crc_checked() -> None:
    plaintext = b"stored caller crc\n" * 100
    blob = corrupt_zipcrypto_payload(
        build_zipcrypto_zip(
            RIGHT, NAME.encode(), plaintext, compression=zipfile.ZIP_STORED
        )
    )
    with open_archive(io.BytesIO(blob), password=RIGHT) as ar:
        with pytest.raises(CorruptionError):
            ar.read(NAME)


# ---------------------------------------------------------------------------
# Task 3.7 — corruption beyond confirmed prefix surfaces on caller's read
# ---------------------------------------------------------------------------


def test_corruption_beyond_prefix_fails_caller_read_as_corruption(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prefix confirmation accepts; trailing corruption fails the caller's CRC."""
    import struct

    plaintext = b"prefix-ok-then-corrupt\n" * 8000
    assert len(plaintext) > 64 * 1024
    blob = bytearray(
        build_zipcrypto_zip(
            RIGHT, NAME.encode(), plaintext, compression=zipfile.ZIP_DEFLATED
        )
    )
    name_len, extra_len = struct.unpack_from("<HH", blob, 26)
    comp_size = struct.unpack_from("<I", blob, 18)[0]
    payload_start = 30 + name_len + extra_len
    # Flip a late ciphertext byte (keep the ZipCrypto header intact).
    late = payload_start + comp_size - 8
    assert late > payload_start + 12
    blob[late] ^= 0xFF

    # Tight bound so confirmation only sees the good prefix.
    monkeypatch.setattr(password_confirm, "CONFIRM_PREFIX_BYTES", 4096)
    monkeypatch.setattr(zip_reader, "CONFIRM_PREFIX_BYTES", 4096)

    with open_archive(io.BytesIO(bytes(blob)), password=[RIGHT, b"also-wrong"]) as ar:
        stream = ar.open(NAME)
        with stream, pytest.raises(CorruptionError):
            stream.read()
