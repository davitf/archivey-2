"""Guards on the public ``archivey`` namespace.

``archivey.__all__`` is curated by hand (not generated) so it lists only the public
API and not re-imported helpers like ``version``/``PackageNotFoundError``. This test
is the safety net that keeps the hand-maintained list from drifting.
"""

from __future__ import annotations

import archivey


def test_all_entries_are_exported() -> None:
    """Every name in __all__ must actually be an attribute of the package."""
    missing = [name for name in archivey.__all__ if not hasattr(archivey, name)]
    assert not missing, f"__all__ lists names that are not exported: {missing}"


def test_all_has_no_duplicates() -> None:
    assert len(archivey.__all__) == len(set(archivey.__all__))


def test_public_symbols_are_in_all() -> None:
    """Imported public symbols (classes/functions, not modules or dunders) must be
    listed in __all__, so a new export can't be silently omitted."""
    import inspect

    public = {
        name
        for name, obj in vars(archivey).items()
        if not name.startswith("_")
        and not inspect.ismodule(obj)
        and name not in ("annotations",)
    }
    not_listed = public - set(archivey.__all__)
    assert not not_listed, f"public symbols missing from __all__: {sorted(not_listed)}"
