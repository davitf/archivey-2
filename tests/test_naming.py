"""Tests for member-name normalization rules (archive-data-model spec).

Normalization is meaning-preserving: a leading ``/`` and any ``..`` are retained faithfully
(rejected at extraction time, not silently re-rooted here); ``\\``→``/`` conversion is
format/entry-aware via ``backslash_is_separator``.
"""

from __future__ import annotations

import logging

import pytest

from archivey.internal.naming import normalize_member_name
from archivey.types import MemberType


@pytest.mark.parametrize(
    ("decoded", "member_type", "backslash_is_separator", "expected"),
    [
        # Backslash: converted for a Windows-origin entry, literal for a POSIX one.
        ("foo\\bar\\baz.txt", MemberType.FILE, True, "foo/bar/baz.txt"),
        ("weird\\name.txt", MemberType.FILE, False, "weird\\name.txt"),
        # Meaning-ALTERING rewrites are gone: leading "/" and ".." are retained.
        (
            "/etc/passwd",
            MemberType.FILE,
            False,
            "/etc/passwd",
        ),  # leading slash retained
        ("foo/../bar", MemberType.FILE, False, "foo/../bar"),  # internal .. retained
        (
            "../../etc/passwd",
            MemberType.FILE,
            False,
            "../../etc/passwd",
        ),  # escaping retained
        # Meaning-PRESERVING clean-ups still apply.
        ("./foo/bar", MemberType.FILE, False, "foo/bar"),  # leading ./ stripped
        ("foo//bar", MemberType.FILE, False, "foo/bar"),  # double slash collapsed
        ("a//b/./c", MemberType.FILE, False, "a/b/c"),  # combined cleanups
        ("mydir", MemberType.DIRECTORY, False, "mydir/"),  # dir trailing slash
        ("mydir/", MemberType.DIRECTORY, False, "mydir/"),  # already has slash
        ("/", MemberType.DIRECTORY, False, "."),  # bare root becomes dot
        ("", MemberType.FILE, False, "."),  # empty becomes dot
    ],
)
def test_normalize(
    decoded: str, member_type: MemberType, backslash_is_separator: bool, expected: str
) -> None:
    assert (
        normalize_member_name(
            decoded, member_type, backslash_is_separator=backslash_is_separator
        )
        == expected
    )


def test_warns_when_name_changes(caplog: pytest.LogCaptureFixture) -> None:
    from archivey.internal.diagnostics_collector import DiagnosticCollector
    from archivey.internal.naming import emit_member_name_normalized
    from archivey.types import ArchiveMember

    presented = "foo//bar"
    name = normalize_member_name(
        presented, MemberType.FILE, backslash_is_separator=False
    )
    member = ArchiveMember(type=MemberType.FILE, name=name, raw_name=None)
    collector = DiagnosticCollector()
    with caplog.at_level(logging.WARNING, logger="archivey.normalization"):
        emit_member_name_normalized(collector, member=member, presented_name=presented)
    assert any("normalized" in r.message for r in caplog.records)
    assert collector.snapshot().total_count == 1


def test_no_warning_when_unchanged(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="archivey.normalization"):
        # A faithful name with an internal ".." is no longer rewritten, so no warning.
        normalize_member_name(
            "foo/../bar", MemberType.FILE, backslash_is_separator=False
        )
    assert not caplog.records


def test_no_warning_for_directory_trailing_slash_only(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # tarfile strips the trailing slash; adding it back is not an observable override.
    from archivey.internal.diagnostics_collector import DiagnosticCollector
    from archivey.internal.naming import emit_member_name_normalized
    from archivey.types import ArchiveMember

    member = ArchiveMember(type=MemberType.DIRECTORY, name="pkg/", raw_name=None)
    collector = DiagnosticCollector()
    with caplog.at_level(logging.WARNING, logger="archivey.normalization"):
        emit_member_name_normalized(collector, member=member, presented_name="pkg")
    assert not caplog.records
    assert collector.snapshot().total_count == 0


def test_link_target_backslash_is_literal() -> None:
    # A link target follows the same backslash rule as member names: the backend already
    # converted separators where the format treats "\" as one, so here it is a literal
    # filename character (a POSIX tar can legitimately contain "b\c" as a name).
    from archivey.internal.naming import resolve_link_target_name

    assert (
        resolve_link_target_name("dir/link", "b\\c", MemberType.SYMLINK) == "dir/b\\c"
    )
    assert (
        resolve_link_target_name("link", "dir\\file", MemberType.HARDLINK)
        == "dir\\file"
    )


def test_infer_member_name_from_archive() -> None:
    import re

    from archivey.internal.naming import infer_member_name_from_archive

    assert infer_member_name_from_archive(None) == "data"
    assert infer_member_name_from_archive("") == "data"
    assert infer_member_name_from_archive("mystery.bin") == "mystery.bin.uncompressed"
    assert (
        infer_member_name_from_archive("file.gz", strip_suffixes={".gz", ".bz2"})
        == "file"
    )
    assert (
        infer_member_name_from_archive(
            "archive.7z.001",
            strip_suffix_re=re.compile(r"\.7z(?:\.\d{3})?$", re.IGNORECASE),
        )
        == "archive"
    )
