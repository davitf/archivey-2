#!/usr/bin/env python3
"""Build RAR fixtures with hostile member NAMES to exercise the F3 argv-injection.

Run this LOCALLY, where the RARLAB **`rar` writer** is on PATH (the review
container only has the `unrar` reader, which cannot author archives). Then push
the generated fixtures + this script to the branch.

    python review/next/01-rar-reader-findings/make_hostile_fixtures.py

What it does:
  1. Writes three compressible files whose basenames are:
        canary.txt   -- a control member (normal name)
        -inul        -- a name identical to a real `unrar` switch
        @atfile      -- a name `unrar` treats as an "@listfile" argument
     (created on disk with those exact names; passed to `rar` as `./NAME` so
      `rar`'s OWN arg parser doesn't eat the leading `-`/`@`).
  2. Builds a NONSOLID, COMPRESSED RAR4 and RAR5 archive of them. Compression is
     forced (`-m5` + repetitive content) because archivey reads *stored* members
     directly and never invokes `unrar` for them -- the injection only fires on
     the compressed / `unrar`-backed per-member path (`RarReader._open_member`).
  3. VERIFIES with `unrar lb` that the stored member names are EXACTLY the hostile
     strings (so a `rar` that rewrote `./-inul` -> `-inul` etc. is what we need),
     and with the native parser that the hostile members are actually compressed.
  4. Copies the archives into tests/fixtures/rar/ and, if archivey imports,
     demonstrates the observable behaviour when opening the hostile members.

Exit non-zero (and keep nothing) if any archive fails verification, so a `rar`
whose name-storage quirks differ is caught loudly rather than shipping a dud.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "rar"

CANARY = "canary.txt"
SWITCH_NAME = "-inul"  # a real unrar switch (disable messages)
AT_NAME = "@atfile"  # unrar reads `atfile` from CWD as a listfile

# Distinct, highly compressible content per member so a wrong-member read is
# obvious and `rar` picks a real compression method (not M0/stored).
CONTENTS = {
    CANARY: b"CANARY-CANARY-CANARY-\n" * 64,
    SWITCH_NAME: b"DASH-INUL-PAYLOAD-\n" * 64,
    AT_NAME: b"AT-ATFILE-PAYLOAD-\n" * 64,
}

# (fixture filename, rar `-ma` archive-format switch)
VARIANTS = [
    ("hostile_argv__.rar", "-ma5"),
    ("hostile_argv__rar4.rar", "-ma4"),
]


def _find_rar() -> str:
    rar = shutil.which("rar")
    if rar is None:
        sys.exit(
            "error: the RARLAB `rar` WRITER is not on PATH. This script authors "
            "archives, which `unrar` cannot do. Install WinRAR/`rar` and re-run."
        )
    return rar


def _find_unrar() -> str:
    unrar = shutil.which("unrar")
    if unrar is None:
        sys.exit("error: `unrar` not on PATH (needed to verify stored names).")
    return unrar


def _build_one(rar: str, unrar: str, out_name: str, ma_flag: str, work: Path) -> Path:
    out = work / out_name
    # -m5 max compression, -s- nonsolid, -ep exclude paths (store bare basenames),
    # -o+ overwrite, -idq quiet. Pass files as ./NAME so rar's parser treats the
    # leading -/@ as part of a path, not a switch/listfile.
    args = [rar, "a", ma_flag, "-m5", "-s-", "-ep", "-o+", "-idq", str(out)]
    args += [f"./{name}" for name in CONTENTS]
    proc = subprocess.run(args, cwd=work, capture_output=True, check=False)
    if proc.returncode != 0 or not out.exists():
        sys.exit(
            f"error: `rar` failed for {out_name} (exit {proc.returncode}):\n"
            f"{proc.stdout.decode(errors='replace')}\n"
            f"{proc.stderr.decode(errors='replace')}"
        )

    # Verify the stored names are EXACTLY the hostile strings.
    listing = subprocess.run(
        [unrar, "lb", str(out)], cwd=work, capture_output=True, check=False
    )
    stored = set(listing.stdout.decode("utf-8", "replace").split())
    expected = set(CONTENTS)
    if not expected <= stored:
        sys.exit(
            f"error: {out_name} did not store the expected names.\n"
            f"  expected: {sorted(expected)}\n"
            f"  stored:   {sorted(stored)}\n"
            "Your `rar` rewrote the hostile names. Try a different `rar` build, or "
            "adjust the path-passing form in _build_one()."
        )
    print(f"  {out_name}: stored names OK -> {sorted(stored)}")
    return out


def _verify_compressed(path: Path) -> None:
    """Confirm the hostile members are compressed (else archivey won't call unrar)."""
    try:
        from archivey.internal.backends.rar_parser import parse_rar_archive
    except Exception as exc:  # noqa: BLE001
        print(f"  (skip compress check: cannot import parser: {exc})")
        return
    with path.open("rb") as fh:
        archive = parse_rar_archive(fh)
    by_name = {m.filename: m for m in archive.members}
    for name in (SWITCH_NAME, AT_NAME):
        m = by_name.get(name)
        if m is None:
            sys.exit(f"error: {path.name}: parser did not surface member {name!r}")
        if m.compress_type == 0x30:  # STORED / M0
            sys.exit(
                f"error: {path.name}: member {name!r} is STORED, so archivey reads it "
                "directly and never invokes unrar. Make the content more compressible "
                "or raise -m; the injection path needs a compressed member."
            )
    print(f"  {path.name}: hostile members are compressed (unrar path will fire)")


def _demo(path: Path) -> None:
    """Show what archivey returns when opening the hostile members."""
    try:
        from archivey import open_archive
    except Exception as exc:  # noqa: BLE001
        print(f"  (skip demo: cannot import archivey: {exc})")
        return
    print(f"  demo {path.name}:")
    with open_archive(path) as arc:
        members = {m.name: m for m in arc.members()}
        for name in (CANARY, SWITCH_NAME, AT_NAME):
            m = members.get(name)
            if m is None:
                print(f"    {name!r:12}: NOT LISTED")
                continue
            try:
                with arc.open(m) as fh:
                    data = fh.read()
                expected = CONTENTS[name]
                verdict = "MATCH" if data == expected else "WRONG BYTES"
                print(f"    {name!r:12}: {verdict} ({len(data)}B, wanted {len(expected)}B)")
            except Exception as exc:  # noqa: BLE001
                # A switch/@-named member typically makes unrar emit the wrong data
                # (or read a local file), which trips CRC verification -> the request
                # for that member fails instead of returning its bytes.
                print(f"    {name!r:12}: {type(exc).__name__}: {exc}")


def main() -> None:
    rar = _find_rar()
    unrar = _find_unrar()
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="rar-hostile-") as tmp:
        work = Path(tmp)
        for name, content in CONTENTS.items():
            # os.open with the literal name; no shell, no rar arg parsing involved.
            (work / name).write_bytes(content)

        built: list[Path] = []
        print("building:")
        for out_name, ma_flag in VARIANTS:
            out = _build_one(rar, unrar, out_name, ma_flag, work)
            _verify_compressed(out)
            dest = FIXTURE_DIR / out_name
            shutil.copy2(out, dest)
            built.append(dest)
            print(f"  -> {dest.relative_to(REPO_ROOT)}")

    print("\ndemo (observable archivey behaviour):")
    # Run the demo from a scratch dir so a stray CWD file can't satisfy the
    # `@atfile` listfile read by accident.
    cwd = os.getcwd()
    with tempfile.TemporaryDirectory(prefix="rar-demo-") as demo_cwd:
        os.chdir(demo_cwd)
        try:
            for dest in built:
                _demo(dest)
        finally:
            os.chdir(cwd)

    print(
        "\nDone. Review the fixtures, then commit them:\n"
        "  git add tests/fixtures/rar/hostile_argv__*.rar "
        "review/next/01-rar-reader-findings/make_hostile_fixtures.py\n"
        "  git commit -m 'tests: RAR fixtures with hostile member names (F3 argv injection)'"
    )


if __name__ == "__main__":
    main()
