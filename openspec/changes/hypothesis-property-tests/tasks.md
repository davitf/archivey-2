# Tasks — Hypothesis property tests for the safety logic

> Run tools through uv: `uv run pytest`, `uv run pyrefly check`, `uv run ty check`,
> `uv run ruff`. Test-only change; runtime core stays zero-dependency.
> Target functions (note: **not all are I/O-free** — test accordingly):
> - `src/archivey/internal/naming.py` — `normalize_member_name`, `resolve_link_target_name`
>   (pure: generated-string strategies)
> - `src/archivey/internal/filters.py` — `check_universal` (calls `Path.resolve()`: use
>   `tmp_path`-rooted strategies with real symlink layouts, not pure inputs)
> - `src/archivey/internal/volumes.py` — `discover_volume_siblings` (name-parsing regexes are
>   pure; the `is_file()`/`iterdir()` discovery needs a `tmp_path` tree)
> - `src/archivey/internal/detection.py` — detection over a byte prefix, wrapped in a
>   `PeekableStream`/`BytesIO` (a **seekable/peekable** source — raw non-seekable streams are
>   consumed by design and are out of scope)

## 0. Decisions locked in this change

- [x] 0.1 **Inline in the normal suite** — bounded, deterministic example budget on every
      CI job; deeper sweep behind `ARCHIVEY_FUZZ_EXAMPLES`. No separate nightly job (that
      slot is reserved for Phase 6 Atheris).
- [x] 0.2 **Invariants, not oracles** — assert structural safety properties; do not
      re-implement the function under test as the checker.
- [x] 0.3 **Counterexamples become pinned regressions** — every shrunk failure is added as
      an `@example`/unit case and (if it is a real bug) fixed in this change.
- [x] 0.4 **Scope = the five listed safety targets** — string/parse properties plus the
      `tmp_path`-rooted filesystem cases for `check_universal` / volume discovery. No
      property tests over backends or the accelerator C-extensions.

## 1. Dependency + harness setup

- [x] 1.1 Add `hypothesis` to the `dev` dependency group in `pyproject.toml`; `uv lock`.
- [x] 1.2 Add a shared Hypothesis **settings profile**, registered/loaded in `conftest.py`:
      **default `max_examples=100`, `deadline=None`** (disabled — avoids flaky failures on
      slow/shared CI runners, matching the mutation harness's cheap default posture),
      `derandomize=True` for reproducibility. Env-var `ARCHIVEY_FUZZ_EXAMPLES` selects a deep
      profile (e.g. `2000`) for local/nightly deepening.
- [x] 1.3 Confirm `hypothesis` absent under `[core-only]` does not break collection (the
      test module is `dev`-only; guard/skip if the group is not installed).

## 2. `normalize_member_name` properties

- [x] 2.1 Strategy: arbitrary text names (incl. separators, `..`, leading `/`, backslashes,
      control chars, unicode) × `MemberType` × `backslash_is_separator`.
- [x] 2.2 Properties: idempotence (`f(f(x)) == f(x)`); never *introduces* a `..` component
      or a leading `/` absent from the input's meaning; backslash handling matches the flag;
      output is a `str` and never raises. **Logging is permitted** — the function logs when it
      changes a name; run under a captured/silenced logger (`caplog`) and assert on the return
      value, not on log-free execution.

## 3. `check_universal` properties

- [x] 3.1 Strategy: `ArchiveMember`s with adversarial names/types (traversal, absolute,
      drive/UNC, null byte, root-named file, special types) × a dest root.
- [x] 3.2 Properties: **always raises** a `FilterRejectionError` subclass for any name with
      a `..` component, an absolute/drive/UNC prefix, a null byte, or a non-directory member
      normalizing to the root; **never** raises for a plain safe relative file; total (raises
      a typed error or returns `None`, never a raw exception).

## 4. `resolve_link_target_name` properties

- [x] 4.1 Strategy: link/target string pairs × `MemberType` (SYMLINK/HARDLINK).
- [x] 4.2 Properties: returns `None` for absolute symlink targets and `..`-escaping targets;
      a returned name never `..`-escapes the archive namespace; hardlink vs symlink namespace
      rule (symlink joined to the link's own dir) holds; total.

## 5. Volume-discovery + detection properties

- [x] 5.1 `discover_volume_siblings` **name-parsing** (pure): arbitrary `*.NNN` / `*.partN` /
      `.rNN` style names never crash the part-number / regex helpers and never produce an
      out-of-order or duplicated volume sequence from the parse alone.
- [x] 5.1b `discover_volume_siblings` **discovery** (`tmp_path`): materialize sibling volume
      trees (present / missing anchor / mixed bases) and assert the public function returns
      an ordered sibling list or `None` without raising a raw exception.
- [x] 5.2 Detection over an arbitrary byte prefix wrapped in a `PeekableStream`/`BytesIO`
      (seekable/peekable — **not** a raw non-seekable stream, which detection consumes by
      design): `detect_format()` on random bytes never raises, never hangs, and **leaves the
      peek source unadvanced** (peek/replay invariant from `format-detection`).

## 6. Gate

- [x] 6.1 `uv run pytest tests/test_property_safety.py` green at the default profile in all
      three dependency configs where `dev` is present.
- [x] 6.2 Any counterexample fixed + pinned (task 0.3); re-run deep profile
      (`ARCHIVEY_FUZZ_EXAMPLES=…`) locally once, green.
- [x] 6.3 Pyrefly + ty + ruff clean.
