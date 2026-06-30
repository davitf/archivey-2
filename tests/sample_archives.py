"""Declarative sample archive data structures for parametrized tests.

This module provides the infrastructure for describing expected archive contents
that tests can verify against actual archive readers.

Phase 1: minimal data structures only — archive generation and full
parametrization will be added in later phases.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from archivey.types import MemberType


@dataclass
class ExpectedMember:
    """Describes expected properties of a single archive member."""

    name: str
    type: MemberType = MemberType.FILE
    size: int | None = None
    contents: bytes | None = None
    link_target: str | None = None
    modified: datetime | None = None
    mode: int | None = None
    uid: int | None = None
    gid: int | None = None
    uname: str | None = None
    gname: str | None = None
    extra_checks: dict[str, Any] = field(default_factory=dict)


@dataclass
class SampleArchive:
    """Describes a sample archive with known expected contents."""

    name: str
    description: str
    members: list[ExpectedMember] = field(default_factory=list)
    is_encrypted: bool = False
    password: str | None = None
    comment: str | None = None


def ExpectedFile(
    name: str,
    contents: bytes,
    *,
    size: int | None = None,
    modified: datetime | None = None,
    mode: int | None = None,
    uid: int | None = None,
    gid: int | None = None,
    uname: str | None = None,
    gname: str | None = None,
) -> ExpectedMember:
    """Convenience constructor for a file member."""
    return ExpectedMember(
        name=name,
        type=MemberType.FILE,
        size=size if size is not None else len(contents),
        contents=contents,
        modified=modified,
        mode=mode,
        uid=uid,
        gid=gid,
        uname=uname,
        gname=gname,
    )


def ExpectedDir(
    name: str,
    *,
    modified: datetime | None = None,
    mode: int | None = None,
) -> ExpectedMember:
    """Convenience constructor for a directory member."""
    return ExpectedMember(
        name=name,
        type=MemberType.DIRECTORY,
        modified=modified,
        mode=mode,
    )


def ExpectedSymlink(
    name: str,
    link_target: str,
    *,
    modified: datetime | None = None,
    mode: int | None = None,
) -> ExpectedMember:
    """Convenience constructor for a symlink member."""
    return ExpectedMember(
        name=name,
        type=MemberType.SYMLINK,
        link_target=link_target,
        modified=modified,
        mode=mode,
    )
