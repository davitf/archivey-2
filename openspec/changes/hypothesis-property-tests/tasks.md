# Tasks — Hypothesis property tests for the pure safety logic

> Run tools through uv: `uv run pytest`, `uv run pyrefly check`, `uv run ty check`,
> `uv run ruff`. Test-only change; runtime core stays zero-dependency.
> Target functions (all pure, no I/O):
> - `src/archivey/internal/naming.py` — `normalize_member_name`, `resolve_link_target_name`
> - `src/archivey/internal/filters.py` — `check_universal`
> - `src/archivey/internal/volumes.py` — `discover_volume_siblings` (name-parsing part)
> - `src/archivey/internal/detection.py` — format detection over a byte prefix

## 0. Decisions locked in this change

- [ ] 0.1 **Inline in the normal suite** — bounded, deterministic example budget on every
      CI job; deeper sweep behind `ARCHIVEY_FUZZ_EXAMPLES`. No separate nightly job (that
      slot is reserved for Phase 6 Atheris).
- [ ] 0.2 **Invariants, not oracles** — assert structural safety properties; do not
      re-implement the function under test as the checker.
- [ ] 0.3 **Counterexamples become pinned regressions** — every shrunk failure is added as
      an `@example`/unit case and (if it is a real bug) fixed in this change.
- [ ] 0.4 **Scope = the five listed pure functions** — no property tests over I/O paths,
      backends, or the accelerator C-extensions.

## 1. Dependency + harness setup

- [ ] 1.1 Add `hypothesis` to the `dev` dependency group in `pyproject.toml`; `uv lock`.
- [ ] 1.2 Add a shared Hypothesis **settings profile** (deterministic seed, bounded
      `max_examples`, a `deadline` tuned so pure-function tests don't flake on slow CI),
      registered/loaded in `conftest.py`; env-var `ARCHIVEY_FUZZ_EXAMPLES` selects a deep
      profile.
- [ ] 1.3 Confirm `hypothesis` absent under `[core-only]` does not break collection (the
      test module is `dev`-only; guard/skip if the group is not installed).

## 2. `normalize_member_name` properties

- [ ] 2.1 Strategy: arbitrary text names (incl. separators, `..`, leading `/`, backslashes,
      control chars, unicode) × `MemberType` × `backslash_is_separator`.
- [ ] 2.2 Properties: idempotence (`f(f(x)) == f(x)`); never *introduces* a `..` component
      or a leading `/` absent from the input's meaning; backslash handling matches the flag;
      output is a `str` and never raises.

## 3. `check_universal` properties

- [ ] 3.1 Strategy: `ArchiveMember`s with adversarial names/types (traversal, absolute,
      drive/UNC, null byte, root-named file, special types) × a dest root.
- [ ] 3.2 Properties: **always raises** a `FilterRejectionError` subclass for any name with
      a `..` component, an absolute/drive/UNC prefix, a null byte, or a non-directory member
      normalizing to the root; **never** raises for a plain safe relative file; total (raises
      a typed error or returns `None`, never a raw exception).

## 4. `resolve_link_target_name` properties

- [ ] 4.1 Strategy: link/target string pairs × `MemberType` (SYMLINK/HARDLINK).
- [ ] 4.2 Properties: returns `None` for absolute symlink targets and `..`-escaping targets;
      a returned name never `..`-escapes the archive namespace; hardlink vs symlink namespace
      rule (symlink joined to the link's own dir) holds; total.

## 5. Volume-discovery + detection properties

- [ ] 5.1 `discover_volume_siblings` name-parsing: arbitrary `*.NNN` / `*.partN` / `.z0N`
      style names never crash the parser and never produce an out-of-order or duplicated
      volume sequence.
- [ ] 5.2 Detection over an arbitrary byte prefix: `detect_format()` on random bytes never
      raises, never hangs, and **never consumes/leaves-advanced** a non-seekable peek source
      (peek/replay invariant from `format-detection`).

## 6. Gate

- [ ] 6.1 `uv run pytest tests/test_property_safety.py` green at the default profile in all
      three dependency configs where `dev` is present.
- [ ] 6.2 Any counterexample fixed + pinned (task 0.3); re-run deep profile
      (`ARCHIVEY_FUZZ_EXAMPLES=…`) locally once, green.
- [ ] 6.3 Pyrefly + ty + ruff clean.
