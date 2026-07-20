## MODIFIED Requirements

### Requirement: Error Policy (OnError) for extraction failures

`OnError.STOP` and `OnError.CONTINUE` SHALL govern per-member **failures** only — a
non-rejection member-scoped `ArchiveyError`, a permitted read/write `OSError`, or a
per-member ratio violation. A policy **block** (a `FilterRejectionError` from a universal
path-safety check or a policy filter) is NOT a failure: it SHALL always be recorded as a
`BLOCKED` `ExtractionResult`, have its partial output removed, emit its
`EXTRACTION_MEMBER_BLOCKED` diagnostic, and let extraction proceed — under **either**
`OnError.STOP` or `OnError.CONTINUE`. `OnError.STOP` therefore never raises on a blocked
member; a STOP run can complete and return an `ExtractionReport` whose results include
`BLOCKED`. Aborting the whole extraction on the first unsafe member (fail-closed strict
security) is a separate, future opt-in and is not expressed through `OnError`.

Under `CONTINUE`, a member-scoped failure records `FAILED`, removes partial output, emits
the matching diagnostic under the active diagnostic policy, and proceeds.

Diagnostic disposition SHALL still be authoritative: `RAISE` emits `DiagnosticRaisedError`
and halts immediately even under `OnError.CONTINUE`; logging-handler and
diagnostic-callback exceptions propagate unchanged. Under `STOP`, a genuine member failure
raises immediately and is not converted to an extraction advisory. Global resource guards
(`ResourceLimitError` for cumulative bytes, archive-wide/live ratio, and max entries),
`KeyboardInterrupt`, `MemoryError`, and unexpected programming exceptions are always-stop
and are not swallowed.

#### Scenario: OnError matrix

| Case | Expected |
| --- | --- |
| Member blocked by policy/path-safety under `STOP` | `BLOCKED` result; partial output removed; `EXTRACTION_MEMBER_BLOCKED`; extraction does **not** halt; later members continue |
| Member blocked by policy/path-safety under `CONTINUE` | `BLOCKED` result; partial output removed; `EXTRACTION_MEMBER_BLOCKED`; later members continue |
| First member blocked, remaining members extractable, under `STOP` | Run completes; report contains `BLOCKED` + later `EXTRACTED`; no exception escapes |
| Corrupt member under `CONTINUE` and default diagnostics | Partial output removed; `FAILED` result; `EXTRACTION_MEMBER_FAILED`; later members continue |
| Default `STOP` member failure (e.g. `CorruptionError`) | Original error propagates immediately; failing partial file removed; earlier outputs remain; no continued-failure diagnostic |
| Filesystem `OSError` while writing under `CONTINUE` | Partial output removed; `FAILED` result/diagnostic; extraction proceeds |
| Extraction diagnostic resolves to `RAISE` under `CONTINUE` | `DiagnosticRaisedError` halts; no report returned |
| Cumulative bytes/live ratio/max entries exceed limit under any `OnError` | `ResourceLimitError` propagates and halts; no later member processed |
| Mixed good/corrupt/blocked archive under `CONTINUE` | Extractable members written; report includes `EXTRACTED` plus `FAILED`/`BLOCKED`; no per-member exception escapes |
