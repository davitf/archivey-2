"""Reproducers for the native-RAR-reader deep review (brief 01).

Run from the repo root:

    uv run --no-sync python review/next/01-rar-reader-findings/repro.py

Each check reports what it saw. F1/F2 need no external binary. F3 shows the argv
archivey builds; if RARLAB ``unrar`` is installed it also demonstrates, at the
``unrar`` CLI level, that a switch/``@``-listfile member name is mis-parsed and
that ``--`` fixes switches but not ``@`` (the behaviour a fix must account for).
"""

from __future__ import annotations

import io
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from archivey import open_archive
from archivey.exceptions import CorruptionError, EncryptionError
from archivey.internal.backends.rar_parser import RAR5_ID, parse_rar_archive

FIX = "tests/fixtures/rar"


def f1_wrong_password_contract() -> None:
    print("\n[F1] RAR3 header-encrypted: wrong password -> which error?")
    data = open(f"{FIX}/encrypted_header__rar4.rar", "rb").read()
    try:
        parse_rar_archive(io.BytesIO(data), password="WRONG_PASSWORD")
        print("  UNEXPECTED: no error raised")
    except EncryptionError as e:
        print(f"  EncryptionError (expected/correct): {e}")
    except CorruptionError as e:
        print(f"  CorruptionError (FINDING: should be EncryptionError): {e}")

    print("[F1] Reader candidate list [wrong, correct] on RAR3 -> should list, but:")
    try:
        with open_archive(
            f"{FIX}/encrypted_header__rar4.rar",
            password=["WRONG_PASSWORD", "header_password"],
        ) as arc:
            names = [m.name for m in arc.get_members()]
            print(f"  listed OK: {names[:3]}")
    except Exception as e:  # noqa: BLE001
        print(f"  {type(e).__name__} (FINDING: correct pw never tried): {e}")


def f2_header_size_vint_dos() -> None:
    print("\n[F2] RAR5 header-size vint pre-read is O(n^2) (no length cap):")
    prev = None
    for n in (20_000, 40_000, 80_000):
        payload = RAR5_ID + b"\x00\x00\x00\x00" + b"\x80" * n
        t = time.time()
        try:
            parse_rar_archive(io.BytesIO(payload))
        except Exception:  # noqa: BLE001
            pass
        dt = time.time() - t
        ratio = f" (x{dt / prev:.1f} for 2x input)" if prev else ""
        print(f"  n={n:>7} 0x80 bytes -> {dt:.3f}s{ratio}")
        prev = dt
    print("  ~quadratic; a few-MB all-0x80 input extrapolates to tens of seconds CPU.")


def f3_unrar_argv_injection() -> None:
    print("\n[F3] Hostile member names reach unrar argv unescaped (no `--` guard):")
    import archivey.internal.backends.rar_unrar as ru

    captured: dict[str, list[str]] = {}

    class FakePopen:
        def __init__(self, cmd: list[str], **_: object) -> None:
            captured["cmd"] = cmd
            self.stdout = io.BytesIO(b"")

        def kill(self) -> None:  # pragma: no cover - not reached
            pass

    orig_find = ru.find_rarlab_unrar
    orig_popen = subprocess.Popen
    ru.find_rarlab_unrar = lambda: "/usr/bin/unrar"  # type: ignore[assignment]
    subprocess.Popen = FakePopen  # type: ignore[assignment,misc]
    try:
        for name in ("-inul", "@/etc/passwd", "-p-secret", "normal.txt"):
            ru.open_unrar_p("archive.rar", member=name)
            print(f"  member={name!r:16} -> argv tail {captured['cmd'][3:]}")
    finally:
        ru.find_rarlab_unrar = orig_find  # type: ignore[assignment]
        subprocess.Popen = orig_popen  # type: ignore[assignment,misc]


def f3b_unrar_cli_behaviour() -> None:
    """CLI-level proof of the mis-parse (needs a real RARLAB unrar)."""
    unrar = shutil.which("unrar")
    if unrar is None:
        print("\n[F3b] skipped: unrar not on PATH")
        return
    print("\n[F3b] unrar CLI mis-parses switch/@ member names (RARLAB unrar):")
    fx = f"{FIX}/basic_nonsolid__.rar"

    def run(args: list[str]) -> tuple[int, bytes]:
        p = subprocess.run(
            [unrar, "p", "-inul", *args], capture_output=True, check=False
        )
        return p.returncode, p.stdout

    rc, out = run([fx, "file1.txt"])
    print(f"  real member 'file1.txt'      -> exit {rc}, {out!r}")
    rc, out = run([fx, "-inul"])
    print(f"  member '-inul' (a switch)    -> exit {rc}, {out!r}  <- ALL members' data")
    with tempfile.TemporaryDirectory() as d:
        lst = Path(d) / "list.txt"
        lst.write_text("file1.txt\n")
        rc, out = run([fx, f"@{lst}"])
        print(f"  member '@{lst.name}'  -> exit {rc}, {out!r}  <- read a LOCAL file")
    rc, out = run([fx, "--", "-inul"])
    print(f"  '-- -inul' (`--` guard)      -> exit {rc}, {out!r}  <- switch neutralized")
    with tempfile.TemporaryDirectory() as d:
        lst = Path(d) / "list.txt"
        lst.write_text("file1.txt\n")
        rc, out = run([fx, "--", f"@{lst}"])
        print(f"  '-- @list' (`--` guard)      -> exit {rc}, {out!r}  <- @ STILL expands")


if __name__ == "__main__":
    f1_wrong_password_contract()
    f2_header_size_vint_dos()
    f3_unrar_argv_injection()
    f3b_unrar_cli_behaviour()
