## Why

`OnError.STOP` currently halts on **both** a member *failure* (corrupt / truncated /
undecodable data) and a policy *block* (an unsafe member refused by a universal
path-safety check or a policy filter). These are different in kind: a failure means
the archive is broken; a block is the safe-extraction library working **as designed**.
Conflating them means STOP aborts an otherwise-good archive the moment one member is
unsafe — the opposite of "skip the unsafe member and keep going," which is the
library's defining behavior. It also forces the CLI's `--stop-on-error` (PR #163) into
an unwanted meaning and creates the unanswerable exit-code question in
`review/cli-product/QUESTIONS.md` Q8.

## What Changes

- **BREAKING** (library default behavior): `OnError` governs **member failures only**.
  A policy `BLOCKED` outcome (`FilterRejectionError` from a universal check or a policy
  filter) is **always** recorded-and-continued and never halts extraction, under either
  `OnError.STOP` or `OnError.CONTINUE`. `OnError.STOP` continues to raise on the first
  genuine member failure.
- Global always-stop guards are unchanged: cumulative-byte / archive-wide / live-ratio /
  max-entries `ResourceLimitError`, `DiagnosticRaisedError` (diagnostic `RAISE`),
  `KeyboardInterrupt`, `MemoryError`, and unexpected programming errors still halt under
  any `OnError`.
- CLI (depends on PR #163): `--stop-on-error` narrows to stop on **failures only**;
  policy blocks are always reported-and-continued. Exit codes follow Q8 **Option A** —
  the abort/STOP path exits `1`; exit `3` is reserved for a run that *completed* with
  ≥1 policy `BLOCKED` and no `FAILED`. This makes the "STOP + policy block" row of the
  Q8 table structurally impossible and resolves Q8.
- "Abort the whole extraction on any unsafe member" (fail-closed strict-security
  posture) is explicitly **out of scope** here and deferred to a future, separate opt-in
  (e.g. a strict policy mode / `--reject-unsafe-archive`), not folded into `OnError`.

## Capabilities

### New Capabilities
<!-- none -->

### Modified Capabilities
- `safe-extraction` — the "Error Policy (OnError) for extraction failures" requirement
  changes so `OnError` no longer governs policy blocks; STOP no longer raises on a
  `FilterRejectionError`.

_(The CLI's `--stop-on-error` narrowing and the Option-A exit-code map live on the `cli`
capability, whose exit-code requirement is being rewritten by PR #163. To avoid dueling
deltas, this change does not re-spec `cli`; it supplies the decision inputs #163 encodes
and is captured in design.md / tasks.md as coordinated-with-#163. If #163 has already
merged when this is implemented, add the `cli` delta here instead.)_

## Impact

- **Public API / behavior**: `Reader.extract_all(..., on_error=OnError.STOP)` no longer
  raises `FilterRejectionError` on the first blocked member; such runs now complete and
  return an `ExtractionReport` containing `BLOCKED` results. Callers relying on STOP to
  fail-closed on unsafe members must switch to the forthcoming strict-policy opt-in.
- **Modules**: `internal/extraction.py` (the `OnError.STOP` raise site at the
  per-member handler), `cli/extract_cmd.py` (stop path + exit codes), `cli/exit_codes.py`
  (name the reserved `3`).
- **Docs**: `docs/safe-extraction.md`, `docs/usage.md` (CLI stop/exit-code behavior);
  `review/cli-product/QUESTIONS.md` Q8 recorded as resolved.
- **Tests**: `OnError` matrix tests (STOP + policy block now continues), CLI exit-code
  tests (STOP+policy → `1`, and the `3`-reserved-for-completed-with-blocks invariant).
- **Extras/deps**: none.
