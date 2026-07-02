# Tasks — Minimal, meaning-preserving name normalization

> Run tools through uv: `uv run pytest`, `uv run pyrefly check`, `uv run ty check`,
> `uv run ruff`. Sequence: land this **before** finalizing `phase-4-safe-extraction`, then
> drop that change's interim `raw_name` check.

## 1. Normalization

- [ ] 1.1 Update `normalize_member_name` (`internal/naming.py`) to the meaning-preserving rule
      set: keep `\`→`/`, `./` and `/./` cleanup, `//` collapse, trailing `/` for dirs,
      empty/root → `"."`; **stop** stripping a leading `/` and **stop** collapsing `..`.
- [ ] 1.2 Update `tests/test_naming.py`: leading `/` retained, internal `..` retained,
      escaping `..` retained, `\`→`/` and `//`/`./ ` cleanups still apply, dir trailing slash,
      root → `"."`.

## 2. Backend fallout audit

- [ ] 2.1 Audit `get()` / `_members_by_name`, `resolve_link_target_name`, link cycle
      detection, and dedup for reliance on the *collapsed* form. Confirm legitimate archives
      (no `..`, no leading `/`) are byte-identical; fix any spot that keyed on the collapsed
      form for correctness on legitimate input.
- [ ] 2.2 Run the ZIP / TAR / directory / ISO reader suites to confirm no regression in
      listing, lookup, or link resolution.

## 3. Extraction check on `member.name` (depends on phase-4-safe-extraction)

- [ ] 3.1 Point `check_universal` (`internal/filters.py`) at `member.name`: reject absolute,
      reject an **escaping** `..`, reject null bytes; keep the parent-directory
      resolve-within-`dest` guarantor; allow an internal non-escaping `..`. Remove the interim
      `raw_name` structural check (`_stored_name_violation` / `_decoded_raw`).
- [ ] 3.2 Confirm the `safe-extraction` traversal/escape/symlinked-parent tests still pass
      (they already assert rejection); add a test that a *listed* member exposes the true
      unsafe `name` (e.g. `member.name == "../evil"`).

## 4. Gates

- [ ] 4.1 `uv run pyrefly check` + `uv run ty check` + `uv run ruff` clean.
- [ ] 4.2 Full suite green; `testing-contract` traversal scenarios raise on `member.name`.
