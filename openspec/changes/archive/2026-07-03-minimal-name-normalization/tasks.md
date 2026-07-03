# Tasks — Minimal, meaning-preserving name normalization

> Run tools through uv: `uv run pytest`, `uv run pyrefly check`, `uv run ty check`,
> `uv run ruff`. Sequence: land this **before** finalizing `phase-4-safe-extraction`, then
> drop that change's interim `raw_name` check. The read-time `on_unsafe_name` block option and
> the extraction `SANITIZE` policy are **phase 5** (see design.md) — not implemented here.

## 1. Normalization (`internal/naming.py`)

- [x] 1.1 Add a `backslash_is_separator: bool` parameter to `normalize_member_name`; convert
      `\`→`/` only when it is `True`. Keep `./` and `/./` cleanup, `//` collapse, trailing `/`
      for dirs, empty/root → `"."`; **stop** stripping a leading `/` and **stop** collapsing
      `..`.
- [x] 1.2 Wire the signal at each backend: TAR passes `False` (POSIX literal backslash); RAR
      passes `True`; ZIP derives it per entry from `create_system` (DOS/Windows →`True`, Unix
      →`False`); directory/ISO as appropriate. ZIP also reads the raw name from
      `ZipInfo.orig_filename` (not `.filename`, which stdlib rewrites per-OS) so backslash and
      null-byte handling is the same on every platform.
- [x] 1.3 Update `tests/test_naming.py`: leading `/` retained, `..` retained (internal and
      escaping), backslash converted only when `backslash_is_separator`, `//`/`./ ` cleanups
      still apply, dir trailing slash, root → `"."`.

## 2. Backend fallout audit

- [x] 2.1 Audit `get()` / `_members_by_name`, `resolve_link_target_name`, link cycle
      detection, and dedup for reliance on the *collapsed* form. Confirm legitimate archives
      (no `..`, no leading `/`) are byte-identical; fix any spot that keyed on the collapsed
      form for correctness on legitimate input.
- [x] 2.2 Add per-entry backslash coverage as **committed fixtures** under
      `tests/fixtures/zip_backslash/` (+ `generate.py`): a DOS/Windows entry converts, a Unix
      entry keeps the literal backslash. Fixtures (not runtime-built zips) so the tests run
      identically on Windows, where stdlib would rewrite a runtime backslash away.
- [x] 2.3 Run the ZIP / TAR / directory / ISO reader suites to confirm no regression in
      listing, lookup, or link resolution.

## 3. Extraction check on `member.name` (depends on phase-4-safe-extraction)

> **Done at the phase-4b rebase** (`phase-4-safe-extraction` / #28): once this change merged,
> #28 was reset onto the new `main` and these tasks landed in that same PR, removing the
> interim `raw_name` check.

- [x] 3.1 Point `check_universal` (`internal/filters.py`) at `member.name`: reject absolute,
      reject **any** `..` component (split on `/` and `\`), reject null bytes; keep the
      parent-directory resolve-within-`dest` guarantor for `..`-free names. Removed the interim
      `raw_name` structural check (`_stored_name_violation` / `_decoded_raw`) and the
      `# NOTE (interim)` comment.
- [x] 3.2 Update the `safe-extraction` extraction tests: internal `foo/../bar` now rejected
      under the default `RAISE`; a *listed* member exposes the true unsafe `name` (verified
      `member.name == "../evil.txt"`).

## 4. Gates

- [x] 4.1 `uv run pyrefly check` + `uv run ty check` + `uv run ruff` clean.
- [x] 4.2 Full suite green (684 passed on the rebased phase-4b branch); the `testing-contract`
      traversal scenarios now raise on `member.name` (verified end-to-end).
