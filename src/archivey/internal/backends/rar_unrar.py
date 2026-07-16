"""Locate RARLAB ``unrar`` and spawn ``unrar p`` for member data."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import BinaryIO, cast

from archivey.exceptions import PackageNotInstalledError, UnsupportedFeatureError

_cached_unrar: str | None = None

_NOT_INSTALLED_MSG = (
    "RARLAB unrar is required to read RAR member data, but it was not found on PATH "
    "(or the unrar on PATH is not RARLAB unrar). Install RARLAB unrar — "
    "unrar-free / unar / 7z are not supported as substitutes."
)


def _is_rarlab_unrar(path: str) -> bool:
    """Return True when ``path`` prints a RARLAB unrar banner."""
    try:
        completed = subprocess.run(
            [path],
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    banner = (completed.stdout or b"") + (completed.stderr or b"")
    text = banner.decode("utf-8", errors="replace")
    if "UNRAR" not in text:
        return False
    return "Alexander Roshal" in text or "RARLAB" in text


def find_rarlab_unrar() -> str:
    """Return path to RARLAB unrar, or raise PackageNotInstalledError naming RARLAB unrar."""
    global _cached_unrar
    if _cached_unrar is not None:
        return _cached_unrar

    candidate = shutil.which("unrar")
    if candidate is None or not _is_rarlab_unrar(candidate):
        raise PackageNotInstalledError(_NOT_INSTALLED_MSG)

    _cached_unrar = candidate
    return candidate


def _password_arg(password: str | bytes | None) -> str:
    if password is None or password == b"" or password == "":
        return "-p-"
    if isinstance(password, bytes):
        password = password.decode("utf-8", errors="surrogateescape")
    return "-p" + password


def _member_include_switch(member: str) -> str:
    """Build a safe ``unrar`` include-mask switch for one member name.

    A hostile archive can name a member like a switch (``-inul``) or an ``@listfile``
    argument; passed positionally those are mis-parsed by ``unrar`` (a switch, or a
    read of an attacker-chosen local file). Passing the name as the value of the ``-n``
    include-mask switch, prefixed with ``./``, neutralizes both: the leading ``-`` is
    not a switch (it is inside ``-n``) and the leading ``@`` is not a listfile (the
    value starts with ``.``). The ``./`` also anchors the mask to the exact archive path
    rather than matching the basename at any depth.

    ``unrar`` masks treat ``*`` and ``?`` as wildcards with no escape (``[]`` are
    literal, ``\\`` does not escape), so a name containing either cannot be addressed to
    exactly one member; such reads are refused rather than risk emitting another
    member's bytes.
    """
    if "*" in member or "?" in member:
        raise UnsupportedFeatureError(
            f"Cannot read RAR member {member!r} via unrar: its name contains a wildcard "
            "character ('*' or '?') that unrar interprets as a match pattern with no "
            "escape, so it cannot be addressed unambiguously."
        )
    return "-n./" + member


def open_unrar_p(
    archive_path: str | Path,
    *,
    password: str | bytes | None = None,
    member: str | None = None,
    version_control: bool = False,
) -> tuple[subprocess.Popen[bytes], BinaryIO]:
    """Spawn ``unrar p -inul [-ver] [-pPWD|-p-] [-n./member] archive``.

    ``version_control`` adds ``-ver`` so the pipe includes WinRAR file-version history
    payloads (needed for solid demux when versioned FILE rows are present, and for a
    named open of a ``path;n`` history member — the ``-n`` mask excludes history rows
    unless ``-ver`` is set).

    A named ``member`` is passed as a ``-n./`` include mask, never positionally, so a
    hostile member name cannot inject an ``unrar`` switch or ``@listfile`` argument
    (see :func:`_member_include_switch`).

    Returns ``(proc, stdout)``. Caller must terminate/wait/close.
    """
    unrar = find_rarlab_unrar()
    cmd = [unrar, "p", "-inul"]
    if version_control:
        cmd.append("-ver")
    cmd.append(_password_arg(password))
    if member is not None:
        cmd.append(_member_include_switch(member))
    cmd.append(str(archive_path))
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=1024 * 1024,
        )
    except OSError as exc:
        raise PackageNotInstalledError(_NOT_INSTALLED_MSG) from exc
    if proc.stdout is None:
        proc.kill()
        raise RuntimeError("unrar produced no stdout pipe")
    return proc, cast(BinaryIO, proc.stdout)


def terminate_unrar(proc: subprocess.Popen[bytes] | None) -> None:
    """Terminate an ``unrar`` process if it is still running."""
    if proc is None:
        return
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
