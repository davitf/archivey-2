## Context

The current adversarial-string PR mutates clean ZIP/TAR archives, but its first version
committed generated bases, mislabeled format semantics, and emitted RTL warnings only from
backends that call `normalize_member_name`. Directory and single-file members bypass that
helper, while moving the same warning into both normalization and registration would emit
duplicates.

## Goals / Non-Goals

**Goals:**

- Make every corpus label and expected outcome correspond to bytes actually stored.
- Generate clean bases in memory and keep committed fixtures for genuinely non-generable
  cases only.
- Warn exactly once when any backend presents a member name containing a bidi control.
- Reject NUL link targets before passing them to filesystem path APIs.
- Scope extraction evidence to returned paths and explicit sandbox escape candidates.

**Non-Goals:**

- Expose raw ZIP general-purpose flags as public or format-specific member metadata.
- Claim detection of arbitrary writes outside the test sandbox.
- Change decoding fallback rules or sanitize bidi controls out of names.

## Decisions

### Warn at member registration

`BaseArchiveReader` assigns identity to every member, whether the backend is ZIP, TAR, ISO,
directory, or single-file and whether traversal is materialized or progressive. The bidi
warning therefore runs beside that identity assignment. `normalize_member_name` remains
responsible only for warnings caused by normalization rewrites, avoiding duplicate warning
paths and covering backends whose names need no normalization.

### Keep ZIP flags as construction evidence

The corpus independently parses local and central headers and asserts bit 11 in each. Raw
general-purpose flags are not added to `ArchiveMember.extra`; the flag is a test-construction
detail and its user-visible behavior is the selected name decoder or a typed corruption
error.

### Scope extraction containment evidence

For successful extraction, every non-`None` returned `ExtractionResult.path` must resolve
inside the destination. The test also creates a nested sandbox and checks explicit sibling
and parent escape candidates relevant to the exercised mutations. A self-test places a
regular file at one of those candidate paths and proves the guard fails. This is narrower
and more accurate than claiming a recursive scan of `tmp_path` proves no arbitrary outside
write.

### Generate clean bases in memory

The stdlib writers can deterministically create the clean STORED ZIP and USTAR bases. Only
the hostile fields require byte mutation, so neither clean nor hostile generated output is
committed. `ARCHITECTURE.md` describes committed fixtures as the exception for inputs that
cannot be generated in the test environment and require sidecar rationale.

## Risks / Trade-offs

- **Repeated independent materializations can warn again** → Registration guarantees once
  per presented `ArchiveMember` object, not once per archive byte sequence across separate
  API calls.
- **Filesystem refusal differs by platform for surrogateescaped TAR names** → On
  UTF-8-enforcing filesystems the write-time `EILSEQ` is a typed `ExtractionError`;
  on byte-preserving filesystems the member extracts. Other exceptions still fail.
- **Containment checks cannot observe arbitrary external paths** → Claims are explicitly
  limited to returned result paths and named sandbox escape targets.
