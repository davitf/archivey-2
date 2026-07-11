# format-zip — member metadata diagnostics

## ADDED Requirements

### Requirement: Invalid ZIP timestamps are member diagnostic data

The existing ZIP mapping and timestamp precedence remain. If a DOS `date_time` or NTFS
FILETIME cannot be represented as a Python `datetime`, the affected field SHALL fall
through to the next valid precedence layer or `None`, and the reader SHALL emit
`MEMBER_TIMESTAMP_INVALID` with member identity, timestamp source/field, and a JSON-safe
stored-value representation.

Under default policy the occurrence is collected/logged and MAY attach to the member under
the shared retention budget. Under `RAISE`, listing halts with
`DiagnosticRaisedError`.

#### Scenario: invalid NTFS timestamp remains queryable

- **WHEN** a ZIP member has an out-of-range NTFS FILETIME
- **THEN** timestamp precedence falls back as specified, `MEMBER_TIMESTAMP_INVALID` is counted, and retained detail may attach to the member

#### Scenario: invalid DOS timestamp can be escalated

- **WHEN** a ZIP DOS `date_time` is invalid and the timestamp code resolves to `RAISE`
- **THEN** listing halts with `DiagnosticRaisedError` carrying the typed timestamp context

### Requirement: Unavailable encrypted symlink target is member diagnostic data

When ZIP listing cannot read a symlink target because a correct password is unavailable,
the member's `link_target` SHALL remain unset and the reader SHALL emit
`SYMLINK_TARGET_UNAVAILABLE` with the member identity and non-secret reason
`"password_required"`. The event MAY attach to the member under the shared budget.

No message/context/log/error SHALL include attempted passwords, candidates, provider
returns, key material, or decrypted target bytes. Under `RAISE`, listing SHALL halt with
`DiagnosticRaisedError`.

#### Scenario: unavailable target is collected without a secret

- **WHEN** an encrypted ZIP symlink target cannot be read under default policy
- **THEN** listing continues with `link_target=None`, the member may carry `SYMLINK_TARGET_UNAVAILABLE`, and serialized diagnostic data contains no password or decrypted target

#### Scenario: strict caller rejects unavailable target

- **WHEN** `SYMLINK_TARGET_UNAVAILABLE` resolves to `RAISE`
- **THEN** listing halts with `DiagnosticRaisedError`
