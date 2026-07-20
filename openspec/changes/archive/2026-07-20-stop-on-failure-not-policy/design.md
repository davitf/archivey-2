## Context

`internal/extraction.py` handles every member that cannot be written normally at one
site (~L448–459): it classifies the outcome as `BLOCKED` when the error is a
`FilterRejectionError` (universal path-safety check or policy filter) and `FAILED`
otherwise, appends an `ExtractionResult`, then — for **both** classes — does
`if self._on_error is OnError.STOP: raise error`. So today `OnError.STOP` aborts on the
first policy block just as it aborts on the first genuine failure.

`OnError` (`internal/extraction_types.py`) is a two-value enum (`STOP` default,
`CONTINUE`), and the library default is `STOP`. The CLI overrides to `CONTINUE` by
default and adds `--stop-on-error` in PR #163 (branch `cursor/cli-product-findings-cefd`,
not yet merged); `main` still passes the library `STOP` default and has no such flag.

This change is the library-side follow-up that PR #163's Q8 comment points to. It was
discussed on PR #163 and the maintainer endorsed "change STOP to stop only on failures,
not policy."

## Goals / Non-Goals

**Goals:**
- `OnError` governs member **failures** only; a policy `BLOCKED` is always recorded and
  never halts extraction, under `STOP` or `CONTINUE`.
- Preserve all existing always-stop guards (resource limits, `DiagnosticRaisedError`,
  `KeyboardInterrupt`, `MemoryError`, programming errors) unchanged.
- Give PR #163 a coherent target: `--stop-on-error` = stop on failures only, and the
  Q8 exit-code map resolves to Option A structurally.

**Non-Goals:**
- A fail-closed "reject the whole archive if any member is unsafe" mode. That is a
  distinct security posture and a **separate future opt-in** (a strict policy mode or a
  `--reject-unsafe-archive` CLI flag), deliberately not folded into `OnError` here.
- Re-speccing the `cli` capability's exit-code requirement — PR #163 owns that rewrite
  (see Decision 3).
- Changing what counts as a `BLOCKED` vs `FAILED` outcome; only the *stop/continue*
  disposition of `BLOCKED` changes.

## Decisions

### 1. Split the stop disposition by outcome class, at the existing handler site
The block/fail classification already exists two lines above the `raise`. The change is
to make the `STOP` raise conditional on the outcome being `FAILED`: a `BLOCKED` result is
recorded-and-continued regardless of `on_error`; a `FAILED` result raises under `STOP`
and records-and-continues under `CONTINUE`. No new enum value, no signature change — the
narrowing lives entirely in the per-member handler.

**Rejected:** a third `OnError` value (e.g. `STOP_ON_FAILURE`). It would leave the old
`STOP`-halts-on-block behavior reachable, which is exactly the conflation we are removing;
callers who genuinely want abort-on-unsafe should reach for an explicit *policy* opt-in,
not an error-handling mode. Keeping `OnError` two-valued keeps the axis clean: `OnError`
is about failures, policy strictness is about blocks.

### 2. This is a behavior change to the shipped library default (accepted)
`extract_all(..., on_error=OnError.STOP)` previously raised `FilterRejectionError` on the
first blocked member; it now completes and returns a report containing `BLOCKED` results.
A caller that relied on STOP to fail-closed on unsafe members must switch to the
forthcoming strict-policy opt-in (Non-Goal above). This is called out as **BREAKING** in
the proposal. It is the right default for a safe-extraction library: skipping the unsafe
member and continuing is the defining behavior, and a lone hostile entry should not deny
the caller the archive's legitimate contents.

**Rejected:** gating the new behavior behind a new default-off knob to preserve strict
back-compat. That would keep the trap (STOP surprising callers into aborting) as the
default and bury the fix behind opt-in — inverting the priority.

### 3. Coordinate the CLI parts with PR #163 rather than re-speccing `cli` here
PR #163 introduces CONTINUE-by-default, `--stop-on-error`, and lifts the `exit ≥3 is
reserved` rule from `cli/spec.md`. This change supplies the two decision inputs #163
should encode, and the `cli` spec delta lands wherever the two changes are sequenced:

- **`--stop-on-error` narrows to failures only.** With Decision 1, passing library `STOP`
  already means "stop on failures, continue on blocks", so the flag needs no special CLI
  logic — it just maps to `OnError.STOP`.
- **Exit codes follow Q8 Option A.** `0` clean; `3` reserved for a completed run with ≥1
  `BLOCKED` and no `FAILED`; `1` for any abort/failure; `2` usage. Because STOP no longer
  halts on a block, the "STOP + policy block" rows of the Q8 table cannot occur — Option A
  becomes structural, not a convention. `cli/exit_codes.py` gains a named `EXIT_BLOCKED =
  3` (or similar) constant.

If #163 merges first, the implementer adds a `cli` delta MODIFYING the (by then rewritten)
exit-code requirement here; if this merges first, #163 rebases onto the narrowed flag
meaning. Either ordering is a rebase, not a conflict of intent.

### 4. Leave the `BLOCKED` status/definition prose as-is
`safe-extraction`'s "result/status matrix" already defines `BLOCKED` as a *continued*
`FilterRejectionError`; that stays true (blocks are now always continued). Only the
"Error Policy (OnError)" requirement, which currently subordinates blocks to `OnError`,
is modified. Avoids a wider delta than the behavior change warrants.

## Risks / Trade-offs

- [Existing library callers using `OnError.STOP` for fail-closed security lose the abort]
  → Documented as BREAKING; the strict-policy opt-in is the migration path. Until it
  ships, such callers can inspect the returned report for `BLOCKED` results and raise
  themselves.
- [Spec delta on `safe-extraction` while PR #163 edits `cli` in parallel] → The two
  capabilities are disjoint; only design/tasks reference the CLI, and Decision 3 spells
  out the rebase either way.
- [Tests currently assert STOP raises on a policy block] → Those assertions invert to
  "STOP completes with a `BLOCKED` result"; enumerated in tasks.

## Open Questions

- Name and shape of the future fail-closed opt-in (strict policy mode vs. dedicated flag)
  — out of scope here; tracked for a later change so this one is not blocked on it.
