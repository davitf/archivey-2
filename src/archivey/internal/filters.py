"""Universal path-safety checks and policy permission transforms for extraction.

Two independent stages sit in front of every on-disk write (see ``safe-extraction``):

* :func:`check_universal` — the non-bypassable path/link/special-file constraints,
  enforced under **every** :class:`ExtractionPolicy` (including ``TRUSTED``). Run on the
  **original** member before any transform.
* the policy transforms in :data:`POLICY_TRANSFORMS` — permission/ownership normalization
  applied to a transient copy of the member, selected by the active policy.

The post-``os.symlink`` re-resolution check (the third defense-in-depth layer) lives in
the coordinator, next to the ``os.symlink`` call it guards.
"""

from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path
from typing import Callable

from archivey.exceptions import (
    PathTraversalError,
    SpecialFileError,
    SymlinkEscapeError,
    UnportableNameError,
)
from archivey.internal.extraction_types import ExtractionPolicy
from archivey.types import ArchiveMember, MemberType

# Split a member name into path components on either separator; a ".." component after
# this split is a traversal attempt regardless of which separator the archive used.
_SEP_SPLIT = re.compile(r"[\\/]")


def _is_absolute(name: str) -> bool:
    """Whether ``name`` is an absolute path: a POSIX root, a UNC share, or a drive letter."""
    if name.startswith("/") or name.startswith("\\"):
        return True  # POSIX root or UNC / rooted-backslash
    # Drive letter: a single ASCII letter followed by ':' (e.g. "C:\\", "C:foo").
    return len(name) >= 2 and name[0].isalpha() and name[1] == ":"


def _within(path: Path, root: Path) -> bool:
    return path == root or path.is_relative_to(root)


def check_universal(member: ArchiveMember, dest: Path) -> None:
    """Enforce the non-bypassable universal path-safety constraints on ``member``.

    ``dest`` is the extraction root. Raises a :class:`FilterRejectionError` subclass
    (``PathTraversalError`` / ``SymlinkEscapeError`` / ``SpecialFileError``) on the first
    violation; returns ``None`` when the member is safe to extract. Applied to the
    original member, before any policy transform, regardless of the active policy.
    """
    name = member.name

    # (1) String checks on member.name. Names are faithful (meaning-preserving
    # normalization keeps a leading "/" and every ".."), so the danger is visible directly
    # on member.name — no separate raw_name inspection is needed. Any ".." component is
    # rejected (escaping and internal alike): a well-formed archive has no reason to carry
    # one. (A future opt-in SANITIZE policy may re-root such names instead of rejecting.)
    if "\x00" in name:
        raise PathTraversalError(
            f"Null byte in member name: {name!r}", member_name=name
        )
    # A name the platform filesystem encoding cannot represent (a lone surrogate outside
    # the surrogateescape range, on POSIX) can never be materialized under dest — and it
    # would otherwise crash the parent-resolution below with a raw UnicodeEncodeError.
    # (Windows' surrogatepass encoding represents lone surrogates, so this passes there.)
    try:
        os.fsencode(name)
    except UnicodeEncodeError as exc:
        raise PathTraversalError(
            f"Member name cannot be encoded for the filesystem: {name!r}",
            member_name=name,
        ) from exc
    if _is_absolute(name):
        raise PathTraversalError(
            f"Absolute path not allowed: {name!r}", member_name=name
        )
    if ".." in _SEP_SPLIT.split(name):
        raise PathTraversalError(
            f"Path traversal ('..') in member name: {name!r}", member_name=name
        )

    rel = name.rstrip("/")
    if member.type != MemberType.DIRECTORY and rel in ("", "."):
        raise PathTraversalError(
            f"Member name refers to the extraction root: {name!r}", member_name=name
        )

    if member.type == MemberType.OTHER:
        raise SpecialFileError(
            f"Special file (device/FIFO/socket) not allowed: {name!r}",
            member_name=name,
        )

    # Pre-extraction path computation: the destination's PARENT directory must resolve
    # within the root. We resolve the parent, not the full path — the final component may
    # be a pre-existing (possibly hostile) symlink that the OverwritePolicy will unlink
    # rather than follow, so following it here would wrongly reject a REPLACE. Combined
    # with the no-".." name check above, a parent inside the root guarantees the member
    # lands inside the root. A symlinked *parent* that escapes is still caught.
    dest_root = dest.resolve()
    if rel not in ("", "."):  # "" / "." is the root dir member itself
        parent = (dest_root / rel).parent.resolve()
        if not _within(parent, dest_root):
            raise PathTraversalError(
                f"Member {name!r} resolves outside the destination root",
                member_name=name,
            )

    # Link-target escape at planning time (the authoritative symlink check is re-run
    # post-creation in the coordinator). A symlink target is relative to the link's own
    # directory; a hardlink target is archive-root relative. An absolute target makes the
    # join absolute, so it resolves outside dest and is caught here too.
    if member.link_target is not None:
        target = member.link_target
        if member.type in (MemberType.SYMLINK, MemberType.HARDLINK):
            # Same string-level guards as for names: a NUL or an unencodable target
            # cannot name a filesystem path, and would crash the resolves below with a
            # raw ValueError / UnicodeEncodeError instead of a typed rejection.
            if "\x00" in target:
                raise SymlinkEscapeError(
                    f"Null byte in link target: {name!r} -> {target!r}",
                    member_name=name,
                )
            try:
                os.fsencode(target)
            except UnicodeEncodeError as exc:
                raise SymlinkEscapeError(
                    f"Link target cannot be encoded for the filesystem: "
                    f"{name!r} -> {target!r}",
                    member_name=name,
                ) from exc
        if member.type == MemberType.SYMLINK:
            link_parent = (dest_root / name).parent
            resolved_target = (link_parent / member.link_target).resolve()
            if not _within(resolved_target, dest_root):
                raise SymlinkEscapeError(
                    f"Symlink target escapes destination: "
                    f"{name!r} -> {member.link_target!r}",
                    member_name=name,
                )
        elif member.type == MemberType.HARDLINK:
            resolved_target = (dest_root / member.link_target).resolve()
            if not _within(resolved_target, dest_root):
                raise SymlinkEscapeError(
                    f"Hardlink target escapes destination: "
                    f"{name!r} -> {member.link_target!r}",
                    member_name=name,
                )


# --- Policy permission transforms (applied to a transient copy) ---------------------

_HIGH_BITS = 0o7000  # setuid | setgid | sticky
_EXEC_BITS = 0o111


def transform_strict(member: ArchiveMember) -> ArchiveMember:
    """STRICT: drop ownership, strip high/execute bits, normalize to 644/755."""
    new: dict[str, object] = {
        "uid": None,
        "gid": None,
        "uname": None,
        "gname": None,
    }
    if member.is_dir:
        new["mode"] = 0o755
    elif member.is_file:
        mode = member.mode
        if mode is None:
            new["mode"] = 0o644
        else:
            mode = (mode & ~_HIGH_BITS) & ~_EXEC_BITS
            new["mode"] = min(mode & 0o666, 0o644)
    return member.replace(**new)


def transform_standard(member: ArchiveMember) -> ArchiveMember:
    """STANDARD: strip setuid/setgid/sticky, keep execute and ownership."""
    new: dict[str, object] = {}
    if member.is_dir:
        new["mode"] = 0o755 if member.mode is None else (member.mode & ~_HIGH_BITS)
    elif member.is_file:
        new["mode"] = 0o644 if member.mode is None else (member.mode & ~_HIGH_BITS)
    return member.replace(**new)


def transform_trusted(member: ArchiveMember) -> ArchiveMember:
    """TRUSTED: apply stored metadata as-is (a fresh copy, no changes)."""
    return member.replace()


POLICY_TRANSFORMS: dict[ExtractionPolicy, Callable[[ArchiveMember], ArchiveMember]] = {
    ExtractionPolicy.STRICT: transform_strict,
    ExtractionPolicy.STANDARD: transform_standard,
    ExtractionPolicy.TRUSTED: transform_trusted,
}


# --- Cross-platform portable-name policy (safe-extraction O3/O4/O7) ------------------
#
# These rules are keyed on ``ExtractionPolicy`` and applied to the FINAL member name (after
# the policy permission transform and any user filter). They make destination-name handling
# deterministic on every OS: ``STRICT`` is portable-by-default, ``STANDARD`` is portable but
# not paranoid, ``TRUSTED`` defers to the local OS (no name rejection or rewrite). See
# ``docs/decisions/0013-cross-platform-name-safety-policies.md``.

# Windows reserved device names (case-insensitive, with or without an extension). Matched
# against the first dot-separated component of each path segment — ``NUL`` and ``NUL.txt``
# both mangle on Win32.
_RESERVED_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)


def _sanitize_portable_name(name: str) -> str:
    """O7: rewrite a name carrying non-UTF-8 (surrogateescape) bytes to a deterministic,
    reversible portable spelling. Each surrogateescape char ``U+DC80``–``U+DCFF`` (a raw
    byte 0x80–0xFF that did not decode as UTF-8) becomes ``%XX`` (uppercase hex of the
    byte); a literal ``%`` becomes ``%25`` so the escaping is unambiguously reversible.

    Only names that actually carry such bytes are rewritten — valid Unicode (including
    NFC/NFD forms) is representable on every filesystem and is returned unchanged; its
    cross-platform folding is the collision-tracking concern, not a representability one.
    """
    if not any("\udc80" <= c <= "\udcff" for c in name):
        return name
    out: list[str] = []
    for c in name:
        if "\udc80" <= c <= "\udcff":
            out.append("%%%02X" % (ord(c) - 0xDC00))
        elif c == "%":
            out.append("%25")
        else:
            out.append(c)
    return "".join(out)


def _strip_trailing_dot_space(name: str) -> str:
    """O3: strip a trailing dot/space from each path segment — the portable spelling Win32
    itself produces (``stuff_etc.`` → ``stuff_etc``). Deterministic on every OS, so the
    result is identical everywhere and the O2 collision map catches any name it now clashes
    with. A segment that is *entirely* dots/spaces has no portable spelling and is rejected
    (an all-dots segment like ``...`` cannot round-trip and would collapse a path)."""
    parts = name.split("/")
    out: list[str] = []
    for part in parts:
        # Empty (from a leading/trailing/`//` separator) and the path-navigation spellings
        # "." / ".." are structural, not trailing-dot hazards — pass them through untouched
        # ("." is the never-empty root from normalize_member_name; ".." is caught earlier by
        # check_universal). Stripping them would wrongly collapse the segment to empty.
        if part in ("", ".", ".."):
            out.append(part)
            continue
        stripped = part.rstrip(". ")
        if stripped == "":
            raise UnportableNameError(
                f"Path segment is entirely dots/spaces: {part!r}", member_name=name
            )
        out.append(stripped)
    return "/".join(out)


def apply_name_policy(member: ArchiveMember, policy: ExtractionPolicy) -> ArchiveMember:
    """Enforce the portable-name policy on ``member``'s final name.

    ``TRUSTED`` returns the member unchanged (faithful bytes, defer to the local OS).
    ``STRICT``/``STANDARD`` **reject** only the unsafe name shapes — Windows-reserved device
    names and ``:`` (NTFS alternate data stream) — and **rewrite** the merely-non-portable
    ones: ``STRICT`` strips trailing dots/spaces (O3) and both levels normalize
    non-representable bytes (O7). Rewriting (not rejecting) a legitimate-but-awkward name
    keeps extraction working; refusal is reserved for structures that cannot be safely
    written. Raises :class:`UnportableNameError` (a ``FilterRejectionError``, so the
    coordinator records ``REJECTED``) on a rejected name; otherwise returns ``member`` or a
    rewritten ``.replace()`` copy.
    """
    if policy is ExtractionPolicy.TRUSTED:
        return member

    name = member.name
    for segment in _SEP_SPLIT.split(name):
        if not segment:
            continue
        # Reserved device names and ':' are unsafe (device capture / NTFS alternate data
        # stream), not merely awkward — rejected under STRICT and STANDARD on every platform.
        stem = segment.split(".", 1)[0].strip().upper()
        if stem in _RESERVED_NAMES:
            raise UnportableNameError(
                f"Windows-reserved device name in path: {segment!r}", member_name=name
            )
        if ":" in segment:
            raise UnportableNameError(
                f"Colon in path segment (NTFS alternate data stream): {segment!r}",
                member_name=name,
            )

    # A trailing dot/space is silently stripped by Win32 — a legitimate macOS/Linux name
    # (e.g. a folder ending in '.'), not an attack. STRICT rewrites it to the portable
    # spelling so extraction succeeds and is identical on every OS; STANDARD/TRUSTED keep it
    # faithful. The O2 collision map catches any clash the rewrite creates.
    if policy is ExtractionPolicy.STRICT:
        name = _strip_trailing_dot_space(name)
    name = _sanitize_portable_name(name)
    if name != member.name:
        return member.replace(name=name)
    return member


def collision_key(name: str, policy: ExtractionPolicy) -> str:
    """The per-run duplicate-detection key for a member's relative ``name`` (O2).

    ``STRICT``/``STANDARD`` fold case and Unicode normalization so ``README``/``readme`` and
    NFC/NFD ``café`` collide on every platform; ``TRUSTED`` keys on the exact name (defer to
    the local OS). Separators are normalized so ``a/b`` and ``a\\b`` share a key.
    """
    rel = name.replace("\\", "/").rstrip("/")
    if policy is ExtractionPolicy.TRUSTED:
        return rel
    return unicodedata.normalize("NFC", rel).casefold()
