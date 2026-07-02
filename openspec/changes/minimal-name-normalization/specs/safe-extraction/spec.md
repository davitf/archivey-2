# Safe Extraction — delta (minimal-name-normalization)

## MODIFIED Requirements

### Requirement: Non-Bypassable Universal Path-Safety Constraints

The system SHALL enforce the following constraints on every member before extraction
regardless of the `ExtractionPolicy` in use, including `ExtractionPolicy.TRUSTED`. These
checks are applied by `check_universal()` in `filters.py` as the first step of the extraction
pipeline, before any policy transform.

Because `member.name` is now a **faithful** representation of the stored path (see
`archive-data-model` — read-time normalization no longer strips a leading `/` or collapses
`..`), these checks operate directly on `member.name`; there is no separate check against the
verbatim `raw_name` (the interim mechanism introduced while normalization still collapsed
traversal is removed).

Three independent enforcement layers provide defense in depth:

1. **String check on `member.name`** — purely string-based, before any I/O: reject an
   absolute path (leading `/`, a Windows drive letter, or a UNC `\\`), reject a `..` component
   that **escapes** the destination root, and reject a `\x00` null byte.
2. **Pre-extraction path computation** — the destination's **parent directory**,
   `(dest / member.name).parent`, is resolved with `.resolve()` and verified to remain within
   `dest.resolve()`. This is the guarantor: it rejects an escaping `..`, an absolute path, and
   a **symlinked intermediate component** (an earlier member's symlink that would redirect a
   later write outside `dest`). The parent — not the full path — is resolved so a pre-existing
   final-component symlink is handled by the `OverwritePolicy` (unlink-then-create) rather than
   followed.
3. **Post-symlink-creation check** — after `os.symlink()`, the created link's target is
   re-resolved with `Path.resolve()` to detect chained symlink attacks (see *Symlink Escape
   Re-Validated at Extraction Time*).

An **internal, non-escaping** `..` (e.g. `foo/../bar`, which resolves within the root) is
**allowed**: it resolves in-root at write time and the parent-resolution layer guarantees it
cannot escape. Only a `..` that escapes the destination root is rejected.

The individual universal constraints are:

| Constraint | Violation type | Condition |
|---|---|---|
| Path traversal | `PathTraversalError` | A `..` component in `member.name` that escapes the destination root |
| Absolute paths | `PathTraversalError` | `member.name` starts with `/`, a Windows drive letter (`C:\`), or `\\` |
| Null bytes | `PathTraversalError` | `member.name` contains `\x00` |
| Symlink escape | `SymlinkEscapeError` | SYMLINK member whose fully-resolved target escapes `dest` |
| Hardlink escape | `SymlinkEscapeError` | HARDLINK member whose target path resolves outside `dest` |
| Special files | `SpecialFileError` | `MemberType.OTHER` (device nodes, FIFOs, sockets) |

#### Scenario: escaping traversal in member name

- **WHEN** a member's `name` is `"../evil"` or `"../../etc/passwd"` (an escaping `..`)
- **THEN** `PathTraversalError` is raised and no file is written, regardless of policy

#### Scenario: internal traversal is allowed

- **WHEN** a member's `name` is `"foo/../bar"` (a `..` that resolves within the root, `foo`
  being a normal directory)
- **THEN** the member is extracted to the in-root location and no error is raised

#### Scenario: absolute path in member name

- **WHEN** a member's `name` starts with `/` or a Windows drive letter
- **THEN** `PathTraversalError` is raised and no file is written, regardless of policy

#### Scenario: symlinked intermediate component is rejected

- **WHEN** an earlier member created a symlink at `foo` pointing outside `dest`, and a later
  member `foo/x` would resolve outside `dest`
- **THEN** the pre-extraction parent resolution detects the escape and `PathTraversalError` is
  raised for `foo/x`

#### Scenario: special file rejected under all policies

- **WHEN** a member's type is `MemberType.OTHER` (device node, FIFO, socket)
- **THEN** `SpecialFileError` is raised regardless of `ExtractionPolicy`
