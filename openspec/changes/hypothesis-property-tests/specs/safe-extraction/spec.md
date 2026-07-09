# Safe Extraction — delta (hypothesis-property-tests)

Counterexample fix (task 0.3): the property sweep's input domain exposed that
`check_universal` could raise a **raw** `UnicodeEncodeError` (a name or link target
containing a lone surrogate the platform filesystem encoding cannot represent, reaching
the parent/link-target `resolve()`), and a raw `ValueError` (an embedded NUL in a link
target). These become typed string-level rejections, extending the universal-constraint
table alongside the existing member-name NUL check.

## ADDED Requirements

### Requirement: Unrepresentable names and link targets are rejected as typed errors

The universal filter SHALL reject, with a typed `FilterRejectionError` subclass and before
any filesystem path computation, a member whose `name` cannot be encoded by the platform
filesystem encoding (`PathTraversalError`), and a SYMLINK/HARDLINK member whose
`link_target` contains a `\x00` byte or cannot be encoded by the platform filesystem
encoding (`SymlinkEscapeError`). Such values can never name a real path under `dest`; on
platforms whose filesystem encoding does represent them (e.g. Windows `surrogatepass`),
the string checks pass and the existing resolution-based constraints apply unchanged. The
filter SHALL NOT surface a raw `UnicodeEncodeError`/`ValueError` for these inputs.

#### Scenario: member name the filesystem encoding cannot represent

- **WHEN** `check_universal` is called on a POSIX system for a member whose name contains
  a lone surrogate outside the `surrogateescape` range in a non-final component (e.g.
  `"\ud800/x"`)
- **THEN** `PathTraversalError` is raised (never a raw `UnicodeEncodeError`)

#### Scenario: link target with a NUL or unrepresentable character

- **WHEN** `check_universal` is called for a SYMLINK or HARDLINK member whose
  `link_target` contains `\x00`, or (on POSIX) a lone surrogate outside the
  `surrogateescape` range
- **THEN** `SymlinkEscapeError` is raised (never a raw `ValueError`/`UnicodeEncodeError`)

#### Scenario: surrogateescape round-trip names still extract

- **WHEN** a member name contains only low surrogates in the `surrogateescape` range
  (`\udc80`–`\udcff`, the round-trip of undecodable filename bytes) and is otherwise safe
- **THEN** the filter accepts it (these names are representable on disk)
