"""Tests for member-name normalization rules (archive-data-model spec)."""

from __future__ import annotations

import logging

import pytest

from archivey.internal.naming import normalize_member_name
from archivey.internal.types import MemberType


@pytest.mark.parametrize(
    ("decoded", "member_type", "expected"),
    [
        ("foo\\bar\\baz.txt", MemberType.FILE, "foo/bar/baz.txt"),  # backslashes
        ("/etc/passwd", MemberType.FILE, "etc/passwd"),  # leading slash stripped
        ("./foo/bar", MemberType.FILE, "foo/bar"),  # leading ./ stripped
        ("foo/../bar", MemberType.FILE, "bar"),  # traversal collapsed
        ("foo//bar", MemberType.FILE, "foo/bar"),  # double slash collapsed
        ("mydir", MemberType.DIRECTORY, "mydir/"),  # dir trailing slash
        ("mydir/", MemberType.DIRECTORY, "mydir/"),  # already has slash
        ("/", MemberType.DIRECTORY, "."),  # root becomes dot
        ("", MemberType.FILE, "."),  # empty becomes dot
    ],
)
def test_normalize(decoded: str, member_type: MemberType, expected: str) -> None:
    assert normalize_member_name(decoded, member_type) == expected


def test_warns_when_name_changes(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="archivey.normalization"):
        normalize_member_name("foo/../bar", MemberType.FILE)
    assert any("normalized" in r.message for r in caplog.records)


def test_no_warning_when_unchanged(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="archivey.normalization"):
        normalize_member_name("foo/bar.txt", MemberType.FILE)
    assert not caplog.records
