"""Member-name normalization.

Implements the ``ArchiveMember.name`` normalization rules from the
``archive-data-model`` spec. Format backends call :func:`normalize_member_name`
when decoding a stored name so that every backend produces names under the same
deterministic rules, while keeping the verbatim bytes in ``ArchiveMember.raw_name``.
"""

from __future__ import annotations

import logging

from archivey.internal.types import MemberType

logger = logging.getLogger("archivey.normalization")


def normalize_member_name(decoded: str, member_type: MemberType) -> str:
    """Normalize a decoded member name per the spec rules.

    Rules, applied in order:
      1. Replace all ``\\`` with ``/``.
      2. Strip leading ``/`` and ``./``.
      3. Collapse ``//`` and ``foo/../bar`` sequences.
      4. Append ``/`` for directory members if not already present.
      5. Never produce an empty string — the root directory becomes ``"."``.

    A warning is emitted via the ``archivey.normalization`` logger when
    normalization changes the logical path.
    """
    name = decoded

    # 1. Backslashes -> forward slashes.
    name = name.replace("\\", "/")

    # 2. Strip leading "/" and "./".
    while name.startswith("/") or name.startswith("./"):
        name = name[1:] if name.startswith("/") else name[2:]

    # 3. Collapse "//" and "foo/../bar" sequences.
    normalized_parts: list[str] = []
    for part in name.split("/"):
        if part == "..":
            if normalized_parts:
                normalized_parts.pop()
        elif part not in (".", ""):
            normalized_parts.append(part)
    name = "/".join(normalized_parts)

    # 4. Trailing "/" for directories.
    if member_type == MemberType.DIRECTORY and not name.endswith("/"):
        name = name + "/"

    # 5. Never empty — root becomes ".".
    if not name or name == "/":
        name = "."

    if name != decoded:
        logger.warning("Member name normalized: %r -> %r", decoded, name)

    return name
