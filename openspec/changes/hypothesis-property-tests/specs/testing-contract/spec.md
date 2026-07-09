# Testing Contract — delta (hypothesis-property-tests)

## ADDED Requirements

### Requirement: Property-based tests for the pure safety logic

The test suite SHALL include property-based (Hypothesis) tests over the library's pure,
I/O-free safety functions — at minimum member-name normalization
(`normalize_member_name`), the universal extraction filter (`check_universal`), link-target
resolution (`resolve_link_target_name`), volume-name discovery, and format detection over an
arbitrary byte prefix. Each test SHALL assert a structural **invariant** of the function (not
a golden output value re-derived from a second implementation of the same logic), and each
function SHALL be **total** under the tested inputs: it returns a value or raises a typed
`ArchiveyError` / documented exception, never an untranslated raw exception and never a hang.

These tests SHALL run inline in the normal test suite with a bounded, deterministic example
budget so failures are reproducible, with a deeper example budget selectable via an
environment variable. A counterexample discovered by the strategy SHALL be recorded as an
explicit regression example so it remains covered independently of the strategy.

`hypothesis` is a test-only (`dev`-group) dependency; the runtime core remains
zero-dependency and no property test is required for a `[core-only]` install to pass.

#### Scenario: universal filter rejects every traversal name

- **WHEN** the property suite generates member names containing a `..` component, an
  absolute/drive/UNC prefix, or a null byte
- **THEN** `check_universal` raises a `FilterRejectionError` subclass for every such name and
  never returns normally

#### Scenario: name normalization is total and introduces no escape

- **WHEN** the property suite feeds arbitrary decoded names to `normalize_member_name`
- **THEN** it returns a `str` for every input, is idempotent, and never introduces a `..`
  component or leading `/` that the input did not already carry

#### Scenario: detection over arbitrary bytes never crashes or consumes the source

- **WHEN** the property suite runs format detection over arbitrary byte prefixes on a
  non-seekable peek source
- **THEN** detection returns a result or a typed error without raising a raw exception or
  hanging, and the peek source is left unadvanced (peek/replay invariant preserved)

#### Scenario: a shrunk counterexample is pinned as a regression

- **WHEN** the strategy discovers and shrinks a failing input
- **THEN** that input is added as an explicit example (or unit case) so the scenario stays
  covered even if the generating strategy later changes
