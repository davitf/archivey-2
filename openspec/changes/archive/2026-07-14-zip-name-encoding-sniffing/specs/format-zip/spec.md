## ADDED Requirements

### Requirement: Decode unflagged ZIP member names by UTF-8-validity sniff

The ZIP backend SHALL decode a member name whose general-purpose bit 11 (UTF-8/EFS flag) is
**clear** by first attempting UTF-8, and only falling back to a configurable legacy encoding
(default cp437, per APPNOTE) when the bytes are not valid UTF-8. This sniff SHALL apply only
in the absence of an authoritative encoding signal: a set bit 11 SHALL be honored as UTF-8,
and an explicit caller-supplied `encoding=` SHALL be used verbatim and SHALL disable the
sniff. When the sniff selects a non-default encoding — i.e. UTF-8 for an unflagged name — the
backend SHALL emit a `diagnostics` warning identifying the member and the chosen encoding, so
the decision is observable and escalatable via `DiagnosticPolicy`. Decoding SHALL NOT raise a
bare `UnicodeDecodeError`; the fallback encoding (cp437 by default) decodes every byte.

#### Scenario: UTF-8 bytes without the flag

- **WHEN** an archive stores a member name as valid UTF-8 bytes (e.g. `Español.txt`,
  `emoji_😀.txt`) with bit 11 **clear** and the caller passes no `encoding=`
- **THEN** the member name is decoded as UTF-8 (`Español.txt`, `emoji_😀.txt`), not cp437
  mojibake, and a diagnostic records that UTF-8 was inferred for an unflagged name

#### Scenario: Legacy bytes without the flag

- **WHEN** an unflagged member name is not valid UTF-8
- **THEN** it is decoded with the configured legacy fallback (default cp437), and no bare
  `UnicodeDecodeError` escapes

#### Scenario: Authoritative signal disables the sniff

- **WHEN** bit 11 is set, **or** the caller passed an explicit `encoding=`
- **THEN** the name is decoded as UTF-8 (flag) or with the caller's `encoding` respectively,
  with no sniff and no override diagnostic
