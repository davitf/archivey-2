"""
Custom filter functions for Archivey.

You don't need to use this package if you just want to use the default filters.
Just pass one of the :ref:`archivey.ExtractionFilter` values to the
`iter_members_with_streams` or `extractall` methods, or set it in the
:ref:`archivey.ArchiveyConfig.extraction_filter` field.

If you need a filter with custom options, you can use the `create_filter`
function. Or you can create your own filter function by implementing the
:ref:`archivey.FilterFunc` type.
"""

from __future__ import annotations

import functools
import logging
import ntpath
import os
import posixpath
from typing import Any

from archivey.config import ExtractionFilter
from archivey.exceptions import ArchiveFilterError
from archivey.types import (
    ArchiveMember,
    FilterFunc,
    MemberType,
)

logger = logging.getLogger(__name__)


def _check_target_inside_archive_root(
    target_path: str, dest_path: str | None, target_type_str: str
) -> None:
    if os.path.isabs(target_path) or ntpath.isabs(target_path):
        raise ArchiveFilterError(f"Absolute path not allowed: {target_path}")

    if target_path.startswith("..") or "/../" in target_path:
        raise ArchiveFilterError(
            f"{target_type_str} outside archive root: {target_path}"
        )

    if dest_path is not None:
        dest_real = os.path.realpath(dest_path)
        target_real = os.path.realpath(os.path.join(dest_real, target_path))
        if os.path.commonpath([dest_real, target_real]) != dest_real:
            raise ArchiveFilterError(
                f"{target_type_str} outside destination: {target_path}"
            )


def _sanitize_name(
    member: ArchiveMember,
    dest_path: str | None,
) -> str:
    # if member.is_dir:
    #     assert member.filename.endswith("/")
    name = posixpath.normpath(member.filename.lstrip("/\\"))
    _check_target_inside_archive_root(name, dest_path, "Path")

    if member.filename.endswith("/") and not name.endswith("/"):
        name += "/"
    return name


def _sanitize_link_target(
    member: ArchiveMember,
    dest_path: str | None,
) -> str | None:
    if member.link_target is None:
        return None

    link_target = posixpath.normpath(member.link_target.lstrip("/\\"))

    if member.type == MemberType.SYMLINK:
        # Symlink targets are relative to the symlink's own directory. Check that
        # the target is inside the archive root.
        rel_target = posixpath.normpath(
            os.path.join(os.path.dirname(member.filename), link_target)
        )
        _check_target_inside_archive_root(rel_target, dest_path, "Symlink target")
        return link_target

    # Hardlink targets are relative to the hardlink's own directory. Check that
    # the target is inside the archive root.
    _check_target_inside_archive_root(link_target, dest_path, "Hardlink target")

    # Return the link target unchanged, as it refers to another member
    # in the archive and should be an exact match to its name.
    return member.link_target


def _get_filtered_member(
    member: ArchiveMember,
    dest_path: str | None = None,
    *,
    for_data: bool,
    sanitize_names: bool,
    sanitize_link_targets: bool,
    sanitize_permissions: bool,
    raise_on_error: bool,
) -> ArchiveMember | None:
    try:
        if for_data and member.is_other:
            raise ArchiveFilterError(f"{member.filename} is a special file")

        new_attrs: dict[str, Any] = {}
        if for_data:
            if member.uid is not None:
                new_attrs["uid"] = None
            if member.gid is not None:
                new_attrs["gid"] = None
            if member.uname is not None:
                new_attrs["uname"] = None
            if member.gname is not None:
                new_attrs["gname"] = None

        if sanitize_names:
            name = _sanitize_name(member, dest_path)
            if name != member.filename:
                new_attrs["filename"] = name

        if sanitize_link_targets:
            link_target = _sanitize_link_target(member, dest_path)
            if link_target != member.link_target:
                new_attrs["link_target"] = link_target

        if sanitize_permissions and member.mode is not None:
            mode = member.mode & 0o777
            if for_data and member.is_file:
                mode &= ~0o111  # Remove executable bit
                mode |= 0o600  # Set read/write permissions for owner
            elif for_data and member.is_dir:
                mode = None
            if mode != member.mode:
                new_attrs["mode"] = mode

        return member.replace(**new_attrs)

    except ArchiveFilterError as e:
        if raise_on_error:
            raise
        logger.warning(
            "Filter error for %s (type=%s), skipping: %s",
            member.filename,
            member.type,
            e,
        )
        return None


def create_filter(
    *,
    for_data: bool,
    sanitize_names: bool,
    sanitize_link_targets: bool,
    sanitize_permissions: bool,
    raise_on_error: bool,
) -> FilterFunc:
    """Create a filter function with the given options.

    The filter function can be passed to `iter_members_with_streams` or `extractall`.

    Args:
        for_data: Whether the filter is for data members (files and directories).
        sanitize_names: Whether to sanitize the names of members.
        sanitize_link_targets: Whether to sanitize the link targets of members.
        sanitize_permissions: Whether to sanitize the permissions of members.
        raise_on_error: Whether to raise an error if a filter function returns None.

    """
    return functools.partial(
        _get_filtered_member,
        for_data=for_data,
        sanitize_names=sanitize_names,
        sanitize_link_targets=sanitize_link_targets,
        sanitize_permissions=sanitize_permissions,
        raise_on_error=raise_on_error,
    )


def fully_trusted(
    member: ArchiveMember, dest_path: str | None = None
) -> ArchiveMember | None:
    return member


# Default filters inspired by Python's tarfile module
tar_filter = create_filter(
    for_data=False,
    sanitize_names=True,
    sanitize_link_targets=True,
    sanitize_permissions=True,
    raise_on_error=True,
)

data_filter = create_filter(
    for_data=True,
    sanitize_names=True,
    sanitize_link_targets=True,
    sanitize_permissions=True,
    raise_on_error=True,
)


DEFAULT_FILTERS: dict[ExtractionFilter, FilterFunc] = {
    ExtractionFilter.FULLY_TRUSTED: fully_trusted,
    ExtractionFilter.TAR: tar_filter,
    ExtractionFilter.DATA: data_filter,
}
