## MODIFIED Requirements

### Requirement: exit codes are argparse-aligned with a policy-refusal code

The system SHALL exit `0` on success and `2` on CLI usage errors (unknown
verb/flag or bad arguments — the argparse default). Operational failures
(unreadable, unsupported, or corrupt archive; read/integrity failure; member
extraction `FAILED`; incomplete listing whose `MemberListReport.error` is set;
an early abort under `--stop-on-error` on a member **failure**, or any
always-stop / hoist failure) SHALL exit `1`. When `extract` **completes**
(under CONTINUE or STOP) with one or more members `BLOCKED` by safety policy
and no member `FAILED`, the system SHALL exit `3` (refused by safety policy —
safe members are on disk). Because `OnError.STOP` / `--stop-on-error` never
halts on a policy block, a STOP+policy abort cannot occur; exit `3` MUST NOT
be used for an aborted STOP-path failure. Exit codes `≥4` SHALL remain
reserved. Documentation SHALL direct callers to treat any nonzero code other
than `2` as a failure and MUST NOT assume `1` is the only failure code.

#### Scenario: exit codes

| Case | Expected |
| --- | --- |
| `archivey list <valid-archive>` | Exit `0` |
| `archivey --badflag` / unknown verb | Exit `2` (usage) |
| `archivey list <corrupt-or-unreadable>` | Exit `1` |
| `archivey list <archive-with-recoverable-prefix-and-terminal-error>` | Exit `1` (after printing recovered members) |
| `archivey test <archive-with-failing-member>` | Exit `1` |
| `archivey test <indexed-archive>` when the member stream aborts early | Summary includes `K not tested` for the untested remainder; exit `1` |
| `archivey extract <archive-with-traversal-and-safe-members>` | Extracts safe members; prints `blocked:`; exit `3` |
| `archivey extract --stop-on-error <archive-with-traversal-and-safe-members>` | Extracts safe members; prints `blocked:`; exit `3` (blocks always continue) |
| `archivey extract <archive-with-corrupt-member>` | Extracts recoverable members; prints `failed:`; exit `1` |
| `archivey extract --stop-on-error <archive-with-corrupt-member>` | Stops at first failure; exit `1` |
