## 1. Library: make OnError govern failures only

- [x] 1.1 In `internal/extraction.py`, at the per-member outcome handler (the site that
      classifies `BLOCKED` vs `FAILED` and does `if on_error is OnError.STOP: raise`),
      make the STOP raise conditional on the outcome being `FAILED`. A `BLOCKED` result
      is always recorded-and-continued regardless of `on_error`.
- [x] 1.2 Confirm the always-stop guards are untouched: `ResourceLimitError` (cumulative
      bytes / archive-wide / live ratio / max entries), `DiagnosticRaisedError`,
      `KeyboardInterrupt`, `MemoryError`, and unexpected programming errors still halt
      under any `OnError` — none of these are `FilterRejectionError`, so 1.1 must not
      broaden or narrow them.
- [x] 1.3 Verify the hardlink-orphan and other secondary raise sites (e.g. the
      `if self._on_error is OnError.STOP: raise` around orphan handling) only ever carry
      `FAILED` outcomes; leave them as-is (they are failures, not blocks).
- [x] 1.4 Update the `OnError.STOP` docstring comment in `internal/extraction_types.py`
      to say it stops on the first member *failure* (blocks are always continued).

## 2. Spec + docs

- [x] 2.1 Land the `safe-extraction` delta (this change's
      `specs/safe-extraction/spec.md`): STOP no longer raises on a `FilterRejectionError`;
      a STOP run can complete with `BLOCKED` results.
- [x] 2.2 Update `docs/safe-extraction.md` (and any `docs/usage.md` note) to describe
      STOP as failures-only, and that aborting on unsafe members is a separate future
      opt-in.
- [x] 2.3 Record `review/cli-product/QUESTIONS.md` Q8 as **resolved by scoping** — STOP
      never halts on policy, so the "STOP + policy block" rows are moot; exit codes are
      Option A. Note the resolution supersedes the A/B framing.

## 3. CLI (coordinate with PR #163 — see design Decision 3)

- [x] 3.1 After PR #163's `--stop-on-error` / CONTINUE-default plumbing is present, confirm
      `--stop-on-error` maps to `OnError.STOP` and — via task 1.1 — stops on failures only
      while blocks are reported-and-continued. No block-specific CLI logic should be needed.
- [x] 3.2 Implement Option-A exit codes in `cli/extract_cmd.py`: `0` clean; `3` when the
      run completed with ≥1 `BLOCKED` and no `FAILED`; `1` on any abort/failure; `2` usage.
- [x] 3.3 Add a named constant for the reserved `3` in `cli/exit_codes.py`
      (e.g. `EXIT_BLOCKED = 3`) with a one-line comment tying it to the Q8 decision.
- [x] 3.4 If PR #163 has merged, add a `cli` delta under this change MODIFYING the (then
      rewritten) exit-code requirement to the Option-A map; otherwise leave the CLI spec
      to #163 and cross-reference this change's decision.

## 4. Tests

- [x] 4.1 Update/replace `OnError` matrix tests: STOP + first-member policy block now
      *completes* with a `BLOCKED` result and continues to later `EXTRACTED` members
      (was: raised `FilterRejectionError`).
- [x] 4.2 Add a regression test: STOP + a genuine member failure still raises immediately
      (unchanged), and a mixed archive under STOP yields `BLOCKED` for unsafe members but
      raises on the first real failure.
- [x] 4.3 Update CLI exit-code tests (once 3.x lands): STOP-path abort/failure → `1`;
      a completed run with blocks and no failures → `3`; assert no test asserts `3` for a
      STOP+policy abort.

## 5. Verify

- [ ] 5.1 `uv run pytest tests/ -k "extraction or on_error or cli"` (and the full suite
      before push) across `[all]`, `[all-lowest]`, and `[core-only]` per CONTRIBUTING.
- [ ] 5.2 `uv run pyrefly check` and `uv run ty check`; `uv run ruff format` + `ruff check`.
- [ ] 5.3 `openspec validate --strict stop-on-failure-not-policy`.
