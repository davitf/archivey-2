# diagnostics — record-once / escalate-always delta

## MODIFIED Requirements

### Requirement: Complete per-code policy and delivery contract

The system SHALL provide a frozen `DiagnosticPolicy` with a default disposition
and immutable per-code overrides. The only dispositions SHALL be `IGNORE`,
`COLLECT`, and `RAISE` (no severity/logger matching).

| Disposition | Counts | Retain/attach | WARNING log | Callback | Raise |
| --- | --- | --- | --- | --- | --- |
| `IGNORE` | yes | no | no | no | no |
| `COLLECT` | yes | budget permitting | yes | if configured | no |
| `RAISE` | yes | budget permitting | yes | if configured | `DiagnosticRaisedError` |

Per event: validate typed payload → resolve policy → under collector lock allocate
id, build immutable value, update counts/retention → **release all locks** → log →
synchronous callback on calling thread → escalate. Logs/callbacks see
already-updated state, in emission order.

| Failure | Behavior |
| --- | --- |
| Logging-handler exception | Propagates; blocks later callback/escalation |
| Callback exception | Propagates unchanged; not under `OnError.CONTINUE`; blocks later `DiagnosticRaisedError`; operation still halted |

No collector/reader/stream/backend/registry lock while calling handlers/callbacks.
Callbacks MAY read snapshots; same-emitting-reader/stream operational reentry →
`UnsupportedOperationError`; other readers OK.

**Deduplication is a presentation concern; escalation is not.** Where a code is
documented as recorded *at most once* per stream or per reader, that bound SHALL apply to
counting, retention, logging and callbacks only. The configured policy SHALL be evaluated
on **every** occurrence, so a `RAISE` disposition raises on the second and later
occurrences as well as the first: a report reader wants bounded, readable output, while a
caller who asked to be stopped wants to be stopped, and a guard that disarms after firing
once is not a guard.

This SHALL be the rule for **every** once-per-stream code, not a per-code exception, so
that a future deduplicated code inherits an answer rather than the question. The
collector SHALL expose a way to evaluate a code's policy without recording an occurrence;
the deduplication bookkeeping itself lives with the emitter, which is what knows the scope
("this stream").

#### Scenario: policy / delivery matrix

| Case | Expected |
| --- | --- |
| Code → `IGNORE` | Count++; no retain/attach/log/callback/raise |
| Callback reads `reader.diagnostics` | Sees current event counted/retained; no lock held |
| Callback raises during `RAISE` | Callback error propagates; no replacement `DiagnosticRaisedError`; no `OnError.CONTINUE` |
| Callback starts op on same emitting reader | `UnsupportedOperationError` |

#### Scenario: deduplicated code policy matrix

| Case | Expected |
| --- | --- |
| Once-per-stream code, second qualifying occurrence, policy `COLLECT` | No second count, retention, log or callback |
| Once-per-stream code, second qualifying occurrence, policy `RAISE` | `DiagnosticRaisedError` raised again; still no second record |
| Once-per-stream code, second qualifying occurrence, policy `IGNORE` | Nothing happens |
| Escalation-only evaluation | Never appears in `retained`, never changes `counts`, never logs or calls back. The raised `DiagnosticRaisedError` still carries a full `Diagnostic` describing *this* occurrence — the caller being stopped should see the event that stopped them, not the first one |
