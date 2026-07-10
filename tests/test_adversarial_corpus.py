"""Sweep the adversarial *string* corpus through open / list / read / extract.

The safety invariant (see ``testing-contract`` "Unicode bombs" row and the threat model):
hostile bytes in a member name / link target / comment must never produce a raw,
untyped exception or a process abort, and never let a member escape the destination.
A member name that cannot be a path is rejected with a typed ``FilterRejectionError``.

The hostile archives are produced by splicing equal-length tokens into committed clean
base archives — see ``tests/create_adversarial.py``.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from archivey import ExtractionPolicy, extract, open_archive
from archivey.exceptions import ArchiveyError, FilterRejectionError
from tests.create_adversarial import Adversarial, adversarial_archives

_CASES = adversarial_archives()
_IDS = [entry.id for entry, _ in _CASES]


def _read_all(blob: bytes) -> None:
    """Open, list every member, and read every file member.

    Decoding hostile bytes is platform-independent, so any failure here must be a typed
    ``ArchiveyError`` — never a raw exception.
    """
    with open_archive(io.BytesIO(blob)) as ar:
        for member in ar.members():
            if member.is_file:
                try:
                    with ar.open(member) as fh:
                        fh.read()
                except ArchiveyError:
                    pass


@pytest.mark.parametrize(("entry", "blob"), _CASES, ids=_IDS)
def test_adversarial_open_list_read_is_typed(entry: Adversarial, blob: bytes) -> None:
    try:
        _read_all(blob)
    except ArchiveyError:
        pass  # a typed error is fine; a raw exception would fail the test by propagating


@pytest.mark.parametrize(("entry", "blob"), _CASES, ids=_IDS)
def test_adversarial_extract_is_safe(
    entry: Adversarial, blob: bytes, tmp_path: Path
) -> None:
    dest = tmp_path / "out"
    try:
        extract(io.BytesIO(blob), dest, policy=ExtractionPolicy.TRUSTED)
        raised: Exception | None = None
    except FilterRejectionError as exc:
        raised = exc
    except ArchiveyError as exc:
        raised = exc
    except OSError as exc:
        # A destination filesystem refusing an unrepresentable name at write time
        # (e.g. APFS/macOS EILSEQ) is a safe refusal, not a crash. (On branches that
        # translate this to a typed ExtractionError it is caught above instead.)
        raised = exc

    if entry.outcome == "reject":
        assert isinstance(raised, FilterRejectionError), (
            f"{entry.id}: expected a typed FilterRejectionError, got {raised!r}"
        )

    # Whatever happened, nothing may be created outside the destination root.
    root = tmp_path.resolve()
    dest_res = dest.resolve()
    for path in tmp_path.rglob("*"):
        resolved = path.resolve()
        if resolved == dest_res or dest_res in resolved.parents:
            continue
        # The only other thing under tmp_path should be the (empty) dest itself.
        assert resolved == root or root in resolved.parents
        assert not path.is_symlink(), f"{entry.id}: symlink escaped to {resolved}"


def test_base_fixtures_are_committed_and_current() -> None:
    # The committed base archives must match what create_adversarial builds now, so the
    # spliced corpus is reproducible (regenerate with `python -m tests.create_adversarial`).
    from tests import create_adversarial as gen

    base_dir = gen.FIXTURE_DIR
    assert (base_dir / "base.zip").read_bytes() == gen.build_base_zip()
    assert (base_dir / "base.tar").read_bytes() == gen.build_base_tar()


def test_nul_name_is_rejected_on_every_platform(tmp_path: Path) -> None:
    # The headline "Unicode bomb": a NUL in a member name is always a typed rejection,
    # regardless of the filesystem (it is caught before any write).
    ids = {entry.id for entry, _ in _CASES}
    assert {"zip-name-nul", "tar-name-nul"} <= ids
    for i, (entry, blob) in enumerate(_CASES):
        if entry.id in ("zip-name-nul", "tar-name-nul"):
            with pytest.raises(FilterRejectionError):
                extract(io.BytesIO(blob), tmp_path / f"out-{i}")
