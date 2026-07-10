"""Multi-password ZIP disambiguation (traditional ZipCrypto).

When several candidate passwords are supplied and a member is ZipCrypto-encrypted, a
*wrong* candidate can pass the cipher's single verification byte (~1/256) at open()
time. Before the fix the reader accepted that false-accept and never tried the correct
password, so the CRC mismatch surfaced later as a spurious ``CorruptionError`` — an
intermittent failure. The reader now confirms a candidate by reading the member (CRC +
decompressor) before accepting it while disambiguating.
"""

from __future__ import annotations

import io

import pytest

from archivey import open_archive
from archivey.exceptions import EncryptionError
from tests.zipcrypto import build_zipcrypto_zip, find_check_byte_collision

RIGHT = b"very_secret_password"
DATA = b"This is very secret" * 8
NAME = "very_secret.txt"


def _read_member(blob: bytes, passwords: list[str]) -> bytes:
    with open_archive(io.BytesIO(blob), password=passwords) as ar:
        member = next(m for m in ar.members() if m.name == NAME)
        with ar.open(member) as fh:
            return fh.read()


@pytest.mark.parametrize("compress", [True, False], ids=["deflated", "stored"])
def test_wrong_candidate_false_accept_does_not_shadow_right_password(
    compress: bool,
) -> None:
    # A wrong candidate whose verification byte collides is tried *before* the correct
    # one; the reader must still find and use the correct password, not fail with a
    # spurious CorruptionError.
    blob = build_zipcrypto_zip(RIGHT, NAME.encode(), DATA, compress=compress)
    collider = find_check_byte_collision(blob, NAME, RIGHT)
    assert collider != RIGHT

    got = _read_member(blob, [collider.decode("latin-1"), RIGHT.decode()])
    assert got == DATA


@pytest.mark.parametrize("compress", [True, False], ids=["deflated", "stored"])
def test_correct_password_alone_still_reads(compress: bool) -> None:
    # The single-candidate fast path is unchanged: the one correct password reads.
    blob = build_zipcrypto_zip(RIGHT, NAME.encode(), DATA, compress=compress)
    assert _read_member(blob, [RIGHT.decode()]) == DATA


def test_only_wrong_passwords_raise_encryption_error() -> None:
    # No supplied password is correct: a typed EncryptionError, never a raw crash and
    # never silently-wrong data.
    blob = build_zipcrypto_zip(RIGHT, NAME.encode(), DATA)
    collider = find_check_byte_collision(blob, NAME, RIGHT)
    with pytest.raises(EncryptionError):
        _read_member(blob, [collider.decode("latin-1"), "another-wrong-one"])
