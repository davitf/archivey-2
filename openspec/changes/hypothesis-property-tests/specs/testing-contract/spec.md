## ADDED Requirements

### Requirement: Property-based tests for pure safety and detection helpers

The test suite SHALL include Hypothesis property-based tests that exercise the
pure helpers shared by every backend. These tests complement the adversarial
corpus and the corpus mutation harness; they do not replace them. At minimum the
suite SHALL cover:

| Helper | Invariant (normative summary) |
|--------|-------------------------------|
| `normalize_member_name` | Never raises on arbitrary decoded strings; result is never empty; directory members end with `/` except the `"."` sentinel; `..` components are retained; backslash conversion honors `backslash_is_separator` |
| `resolve_link_target_name` | Never raises; absolute symlink targets and archive-root escapes yield `None` |
| `check_universal` | Names with null bytes, `..` components, absolute forms, or non-directory `""`/`"."` raise the documented `FilterRejectionError` subclass; ordinary relative file/dir names pass |
| `discover_volume_siblings` | For paths under a temporary directory, returns `None` or a naturally ordered sibling list that includes the probe; does not raise on ordinary filesystem paths |
| `detect_format` | Over arbitrary bounded byte prefixes: returns `FormatInfo` or raises only an `ArchiveyError` subclass — never an untranslated exception |

Property tests SHALL live in the `dev`-tooling environment (Hypothesis is a
`dev` dependency, not a runtime extra). They SHALL run in the everyday and
full-extra CI legs and SHALL remain absent from the zero-dep (`--no-dev`) leg.

#### Scenario: normalization properties hold over generated names

- **WHEN** Hypothesis generates decoded member-name strings and `MemberType` /
  `backslash_is_separator` combinations
- **THEN** `normalize_member_name` returns a non-empty string obeying the
  directory-suffix and backslash rules above without raising

#### Scenario: universal checks reject generated dangerous names

- **WHEN** Hypothesis generates member names that embed a null byte, a `..`
  path component, an absolute form, or a non-directory `"."` / empty name
- **THEN** `check_universal` raises a `FilterRejectionError` subclass

#### Scenario: detection over arbitrary prefixes never escapes the error hierarchy

- **WHEN** Hypothesis generates arbitrary byte strings up to the detection peek
  bound and `detect_format` is invoked on them (via a non-hanging in-memory
  stream)
- **THEN** the call either returns a `FormatInfo` or raises an `ArchiveyError`
  subclass — never a bare `Exception` / `BaseException` outside that hierarchy
