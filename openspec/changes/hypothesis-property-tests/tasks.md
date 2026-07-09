# Tasks — Hypothesis property tests (Phase 6 entry gate)

> Run tools through uv: `uv run pytest`, `uv run pyrefly check`, `uv run ty check`,
> `uv run ruff`. Additive tests + `dev` dependency; no production API change expected
> unless a property finds a bug.

## 1. Dependency + scaffolding

- [ ] 1.1 Add `hypothesis` to `[dependency-groups] dev` in `pyproject.toml` and
      `uv sync --group dev --extra all` so the lockfile picks it up.
- [ ] 1.2 Add a small Hypothesis profile helper (optional `ci` / `deep` via env) if
      the default `max_examples` is too slow or too shallow for CI; document the knob
      in the test module docstring.
- [ ] 1.3 Create `tests/test_properties_*.py` modules (naming, filters, volumes,
      detection) — keep them separate from `test_mutation_fuzz.py`.

## 2. Naming + link-target properties

- [ ] 2.1 `@given` strategies for decoded names (Unicode, separators, empty, `.`,
      `..`, absolute, mixed) × `MemberType` × `backslash_is_separator`.
- [ ] 2.2 Properties for `normalize_member_name` per design.md invariants
      (non-empty; directory suffix; backslash rule; `..` retained; never raises).
- [ ] 2.3 Properties for `resolve_link_target_name` (absolute symlink → `None`;
      root escape → `None`; never raises).

## 3. Universal filter properties

- [ ] 3.1 Strategy that builds `ArchiveMember` stubs (or minimal stand-ins) with
      generated dangerous vs safe names and types.
- [ ] 3.2 Property: dangerous names (null, `..`, absolute, non-dir `"."`/empty)
      → `check_universal` raises a `FilterRejectionError` subclass.
- [ ] 3.3 Property: ordinary relative file/dir names under a temp `dest` pass
      without error.

## 4. Volume discovery properties

- [ ] 4.1 Strategy that creates a temp directory with generated volume-style
      filenames (`.7z.NNN`, `.partN.rar`, `.rNN` + base) and probes one path.
- [ ] 4.2 Property: result is `None` or a naturally ordered `list[Path]` including
      the probe; never raises on ordinary paths.
- [ ] 4.3 Cross-check ordering against a deterministic sort key for at least the
      three known patterns (can reuse fixtures from `test_volumes.py` as examples).

## 5. Detection properties

- [ ] 5.1 `@given(binary(...))` over bounded prefixes (≤ detection peek window).
- [ ] 5.2 Property: `detect_format` on an in-memory stream returns `FormatInfo` or
      raises only `ArchiveyError` (and subclasses) — assert with `pytest.raises` /
      exception-type checks, not bare `except Exception`.
- [ ] 5.3 Ensure the strategy cannot hang (no real files with accelerator paths;
      keep accelerators irrelevant by feeding raw buffers only).

## 6. Spec sync + threat-model note

- [ ] 6.1 Sync the `testing-contract` delta into `openspec/specs/testing-contract/spec.md`.
- [ ] 6.2 Update `docs/threat-model.md` O5 item 2 from "Still open" to "Landed"
      with a pointer to the new tests.
- [ ] 6.3 `openspec validate --strict hypothesis-property-tests` clean.

## 7. Gates

- [ ] 7.1 `uv run --no-sync pytest tests/test_properties_*.py` green.
- [ ] 7.2 Full `uv run --no-sync pytest` green (no flaky interactions with mutation
      fuzz).
- [ ] 7.3 `uv run --no-sync pyrefly check`, `ty check`, `ruff check` clean.
