# Safe Extraction

## Purpose

Safe extraction writes archive members to a destination directory on disk while enforcing path-safety constraints and permission transforms that prevent untrusted archives from escaping the destination root or writing hostile filesystem objects. It also enforces decompression-bomb limits and reports per-member progress and outcomes. It is the primary interface for callers who want files on disk rather than in-memory data.

## Requirements

### Requirement: One-Shot Extraction API

The system SHALL expose a top-level `archivey.extract()` function that opens an archive, applies safety checks, and writes all (or a selected subset of) members to a destination directory in a single call.

```python
archivey.extract(
    source: str | Path | BinaryIO,
    dest: str | Path,
    *,
    members: Iterable[str | Member] | None = None,  # None = all
    policy: ExtractionPolicy = ExtractionPolicy.STRICT,
    overwrite: OverwritePolicy = OverwritePolicy.ERROR,
    format: ArchiveFormat | None = None,
    password: str | bytes | None = None,
    on_progress: Callable[[ExtractionProgress], None] | None = None,
) -> list[ExtractionResult]
```

The default policy is `ExtractionPolicy.STRICT` and the default overwrite behaviour is `OverwritePolicy.ERROR`.

#### Scenario: extract all members from an untrusted archive

- **WHEN** `archivey.extract("untrusted.zip", "/safe/output/")` is called with no other arguments
- **THEN** all members are extracted to `/safe/output/` under `ExtractionPolicy.STRICT` and `OverwritePolicy.ERROR`
- **AND** a `list[ExtractionResult]` describing each member's outcome is returned

#### Scenario: extract a named subset of members

- **WHEN** `members` is provided as a non-`None` iterable of names or `Member` objects
- **THEN** only those members are extracted; all others are skipped

---

### Requirement: Per-Reader Extract and Extract-All Helpers

The system SHALL provide `extract()` and `extract_all()` instance methods on `ArchiveReader` that delegate to the same extraction internals as `archivey.extract()`.

```python
class ArchiveReader:
    def extract(
        self,
        member: str | Member,
        dest: str | Path,
        *,
        policy: ExtractionPolicy = ExtractionPolicy.STRICT,
        overwrite: OverwritePolicy = OverwritePolicy.ERROR,
    ) -> Path: ...

    def extract_all(
        self,
        dest: str | Path,
        *,
        policy: ExtractionPolicy = ExtractionPolicy.STRICT,
        overwrite: OverwritePolicy = OverwritePolicy.ERROR,
        on_progress: Callable[[ExtractionProgress], None] | None = None,
    ) -> list[ExtractionResult]: ...
```

Both methods accept the same `ExtractionPolicy` and `OverwritePolicy` options as the top-level function.

#### Scenario: per-member extraction from an open reader

- **WHEN** `reader.extract(member, dest)` is called
- **THEN** exactly that one member is written to `dest` after passing universal and policy checks
- **AND** the returned `Path` points to the extracted file on disk

#### Scenario: extract all via reader

- **WHEN** `reader.extract_all(dest)` is called
- **THEN** all members are extracted and a `list[ExtractionResult]` is returned, with the same safety guarantees as `archivey.extract()`

---

### Requirement: Non-Bypassable Universal Path-Safety Constraints

The system SHALL enforce the following constraints on every member before extraction regardless of the `ExtractionPolicy` in use, including `ExtractionPolicy.TRUSTED`. These checks are applied by `check_universal()` in `_filters.py` as the first step of the extraction pipeline, before any policy transform.

Three independent enforcement layers provide defense in depth:

1. **String check on `member.name`** — purely string-based, before any I/O.
2. **Pre-extraction path computation** — `dest / member.name` is resolved with `.resolve()` and verified to remain within `dest.resolve()`.
3. **Post-symlink-creation check** — after `os.symlink()`, the created link's target is re-resolved with `Path.resolve()` to detect chained symlink attacks.

The individual universal constraints are:

| Constraint | Violation type | Condition |
|---|---|---|
| Path traversal | `PathTraversalError` | Any `name` component equals `..` after splitting on `/` |
| Absolute paths | `PathTraversalError` | `name` starts with `/` or a Windows drive letter (`C:\`, `\\`) |
| Null bytes | `PathTraversalError` | `name` contains `\x00` |
| Symlink escape | `SymlinkEscapeError` | SYMLINK member whose fully-resolved target escapes `dest` root |
| Hardlink escape | `SymlinkEscapeError` | HARDLINK member whose target path resolves outside `dest` |
| Special files | `SpecialFileError` | `MemberType.OTHER` (device nodes, FIFOs, sockets) |

#### Scenario: path traversal attempt in member name

- **WHEN** a member's `name` contains a `..` component (e.g. `../evil`)
- **THEN** `PathTraversalError` is raised and no file is written, regardless of policy

#### Scenario: absolute path in member name

- **WHEN** a member's `name` starts with `/` or a Windows drive letter
- **THEN** `PathTraversalError` is raised and no file is written, regardless of policy

#### Scenario: null byte in member name

- **WHEN** a member's `name` contains a `\x00` byte
- **THEN** `PathTraversalError` is raised

#### Scenario: special file rejected under all policies

- **WHEN** a member's type is `MemberType.OTHER` (device node, FIFO, socket)
- **THEN** `SpecialFileError` is raised regardless of `ExtractionPolicy`

---

### Requirement: Symlink Escape Re-Validated at Extraction Time

The system SHALL re-validate symlink targets at the moment the symlink is created on disk, not only at planning time. After `os.symlink(link_target, dest_path)` is called, the implementation SHALL resolve the created link's target with `Path.resolve()` and immediately unlink and raise `SymlinkEscapeError` if the resolved path escapes `dest`.

```
os.symlink(link_target, dest_path)
resolved = (dest_path.parent / link_target).resolve()
if not resolved.is_relative_to(dest.resolve()):
    dest_path.unlink()
    raise FilterRejectionError(...)
```

This single-pass, post-creation check catches TOCTOU symlink attacks where earlier archive members create symlinks that could redirect later writes.

#### Scenario: symlink resolves outside dest at extraction time

- **WHEN** a symlink member is written to disk and its resolved target escapes the `dest` root
- **THEN** the symlink is immediately unlinked and `SymlinkEscapeError` is raised
- **AND** no further data is written through that symlink

#### Scenario: chained symlink attack

- **WHEN** an earlier member creates a symlink that redirects a later member's write outside `dest`
- **THEN** the post-creation `Path.resolve()` check catches the escape and raises `SymlinkEscapeError`

---

### Requirement: Hardlink Two-Pass Extraction

The system SHALL support hardlinks (as found in TAR archives) through a strategy that handles forward-only archive ordering. During the extraction pass:

- **FILE / DIR / SYMLINK** members are written immediately; the mapping from member identity to extracted path is recorded.
- **HARDLINK** members: if the source member's extracted path is already recorded, `os.link()` is called; if not yet extracted, the source is copied with `shutil.copy2()`.
- In streaming mode, TAR guarantees the hardlink target precedes the link in archive order; if the source was filtered out, an explicit error with a clear message is raised.
- In random-access mode, if the source was not selected by the filter it is added to the extraction set implicitly and discarded after the link is created.
- If `os.link()` fails due to a cross-device error, the implementation SHALL fall back to copying.

#### Scenario: hardlink to already-extracted member

- **WHEN** a HARDLINK member is encountered and its target has already been extracted in the same pass
- **THEN** `os.link(source_path, hardlink_dest)` is called (or `shutil.copy2` on cross-device failure)

#### Scenario: hardlink target not yet extracted in streaming mode

- **WHEN** a HARDLINK member is encountered before its target in streaming mode and the target was filtered out
- **THEN** an explicit error is raised with a clear message

---

### Requirement: Policy-Specific Metadata Transforms

The system SHALL apply policy-specific permission and ownership transforms to a new (immutable) copy of the `Member` after universal checks pass. The transform corresponding to the active `ExtractionPolicy` is selected from `POLICY_TRANSFORMS` in `_filters.py` and applied before any I/O.

```python
class ExtractionPolicy(Enum):
    STRICT   = "strict"    # default; untrusted archives
    STANDARD = "standard"  # moderate trust; e.g. your own older archives
    TRUSTED  = "trusted"   # bypass permission/ownership checks; path safety still enforced
```

The transforms per policy are:

| Check | STRICT | STANDARD | TRUSTED |
|-------|--------|----------|---------|
| Path traversal reject | **always** | **always** | **always** |
| Absolute path reject | **always** | **always** | **always** |
| Symlink outside-root reject | **always** | **always** | **always** |
| Special file (device/FIFO) reject | yes | yes | yes |
| Executable bit strip | yes | no | no |
| Setuid/setgid/sticky strip | yes | yes | no |
| Ownership (uid/gid) strip | yes | no | no |
| Permission normalize to 644/755 | yes | no | no |

**STRICT** transforms:
- Remove uid/gid (extract as current user).
- Strip all setuid (`0o4000`), setgid (`0o2000`), sticky (`0o1000`) bits.
- Strip execute bits on files: `mode & ~0o111`.
- Normalize remaining permissions: files → `min(mode & 0o666, 0o644)`, dirs → `0o755`.
- If `mode` is `None`, use `0o644` for files and `0o755` for directories.

**STANDARD** transforms:
- Remove uid/gid.
- Strip setuid and setgid bits.
- If `mode` is `None`, use `0o644`/`0o755`.
- Execute bits preserved.

**TRUSTED** transforms:
- Apply `mode` as-is.
- Apply uid/gid if running as root; otherwise skip silently.

#### Scenario: STRICT strips execute and normalizes permissions

- **WHEN** a FILE member with `mode=0o755` is extracted under `ExtractionPolicy.STRICT`
- **THEN** the extracted file is written with mode `0o644` (execute stripped, normalized)

#### Scenario: STANDARD preserves execute bits

- **WHEN** a FILE member with `mode=0o755` is extracted under `ExtractionPolicy.STANDARD`
- **THEN** the extracted file retains its execute bits; only setuid/setgid are stripped

#### Scenario: TRUSTED applies uid/gid as root

- **WHEN** a FILE member with explicit uid/gid is extracted under `ExtractionPolicy.TRUSTED` and the process runs as root
- **THEN** uid/gid are applied to the extracted file

---

### Requirement: Overwrite Policy

The system SHALL enforce the `OverwritePolicy` when a destination file already exists at the path a member would be written to.

```python
class OverwritePolicy(Enum):
    ERROR   = "error"   # raise ExtractionError if destination file exists
    SKIP    = "skip"    # silently skip existing files
    REPLACE = "replace" # overwrite unconditionally
```

#### Scenario: ERROR raises on existing file

- **WHEN** a member would write to a path that already exists on disk and `OverwritePolicy.ERROR` is active
- **THEN** `ExtractionError` is raised and the existing file is not modified

#### Scenario: SKIP silently bypasses existing files

- **WHEN** a member would write to an existing path and `OverwritePolicy.SKIP` is active
- **THEN** the member is skipped; its `ExtractionResult` carries `ExtractionStatus.SKIPPED`

#### Scenario: REPLACE overwrites unconditionally

- **WHEN** a member would write to an existing path and `OverwritePolicy.REPLACE` is active
- **THEN** the existing file is overwritten with the member's data

---

### Requirement: Extraction as a Composable Module

The system SHALL implement safe extraction in a dedicated `_extraction.py` module (`ExtractionCoordinator`) that is separate from the reader backends and format detection. Both `archivey.extract()` and `ArchiveReader.extract_all()` SHALL delegate to the same `ExtractionCoordinator`, which drives a single unified forward pass via `_iter_with_data()`.

Decompression-bomb enforcement (see the bomb-limit requirements below) is handled by a `BombTracker` instance passed through to each member extraction during this pass. Progress callbacks and `ExtractionResult` accumulation also happen inside this single pass.

#### Scenario: streaming and random-access modes use same coordinator

- **WHEN** `extract_all()` is called whether the reader is in streaming or random-access mode
- **THEN** the same `ExtractionCoordinator.run()` single forward pass is used for both paths

---

### Requirement: Enforce Cumulative Max-Extracted-Bytes Limit

The system SHALL track the total number of bytes written across all members during a single `extract()` or `extract_all()` call and SHALL raise `ExtractionError` when that cumulative total exceeds `max_extracted_bytes`. The default limit is 2 GiB (2 147 483 648 bytes). The caller MAY override this limit by passing `max_extracted_bytes` to `extract()` or `extract_all()`.

The limit is tracked by a `BombTracker` instance constructed once per extraction call. Byte counts are cumulative across all members in the call, not per-member.

```python
class BombTracker:
    def __init__(self, max_bytes: int, max_ratio: float):
        self._max_bytes = max_bytes
        self._max_ratio = max_ratio
        self._total_bytes = 0

    def count(self, member: Member, chunk_bytes: int) -> None:
        self._total_bytes += chunk_bytes
        if self._total_bytes > self._max_bytes:
            raise ExtractionError(
                f"Extraction limit reached: {self._total_bytes} bytes > {self._max_bytes}"
            )
        if member.compressed_size and member.compressed_size > 0:
            ratio = self._total_bytes / member.compressed_size
            if ratio > self._max_ratio:
                raise ExtractionError(
                    f"Decompression ratio {ratio:.0f}:1 exceeds limit {self._max_ratio:.0f}:1"
                )
```

The default of 2 GiB is sufficient for most legitimate use cases and prevents gigabyte-class bombs.

#### Scenario: cumulative limit exceeded mid-extraction

- **WHEN** the running total of bytes written across all extracted members exceeds `max_extracted_bytes`
- **THEN** `ExtractionError` is raised immediately at the chunk boundary where the limit is crossed
- **AND** extraction halts; no further members are processed

#### Scenario: caller raises the default limit

- **WHEN** `archivey.extract(..., max_extracted_bytes=10 * 2**30)` is called
- **THEN** the enforced cumulative limit is 10 GiB rather than the default 2 GiB

---

### Requirement: Enforce Per-Member Max Decompression Ratio

The system SHALL raise `ExtractionError` when the decompression ratio for a single member exceeds `max_ratio` during extraction. The default ratio limit is 1000:1. The caller MAY override this by passing `max_ratio` to `extract()` or `extract_all()`.

The ratio for a member is computed as `bytes_written_for_member / member.compressed_size`. The check is only performed when `member.compressed_size` is known and greater than zero. The default of 1000:1 is deliberately generous — typical DEFLATE compresses at 3:1 to 10:1, and even pathological quine-style zip bombs produce outer-layer ratios around 391:1 — so the limit catches only pathological cases without triggering on legitimately highly-compressible data.

#### Scenario: single member exceeds ratio limit

- **WHEN** a single member decompresses to more than `max_ratio` times its compressed size
- **THEN** `ExtractionError` is raised while processing that member

#### Scenario: ratio check skipped when compressed size is unknown

- **WHEN** `member.compressed_size` is `None` or `0`
- **THEN** the per-member ratio check is skipped; the cumulative byte limit still applies

#### Scenario: caller lowers the ratio limit

- **WHEN** `archivey.extract(..., max_ratio=100)` is called
- **THEN** any member decompressing at more than 100:1 raises `ExtractionError`

---

### Requirement: Bomb Protection Scope Limited to Extraction Paths

The system SHALL apply decompression bomb limits only during `extract()` and `extract_all()`. The `read()` and `open()` methods on `ArchiveReader` return raw decompressed data without enforcing any byte or ratio limits, leaving bomb detection entirely to the caller.

#### Scenario: read() returns data without bomb check

- **WHEN** `reader.read(member)` is called on a member with an extreme decompression ratio
- **THEN** the raw decompressed bytes are returned to the caller with no `ExtractionError` raised by the library

#### Scenario: open() returns a stream without bomb check

- **WHEN** `reader.open(member)` is called
- **THEN** the returned `BinaryIO` stream delivers decompressed data without enforcing any limit; the caller is responsible for guarding against excessive reads

---

### Requirement: Progress Reporting via on_progress Callback

The system SHALL accept an optional `on_progress` callback on `archivey.extract()` and `ArchiveReader.extract_all()`. The callback, if provided, SHALL be called once per member as that member is processed, receiving an `ExtractionProgress` instance.

```python
@dataclass
class ExtractionProgress:
    member: Member
    bytes_written: int
    total_bytes_estimated: int | None   # None if archive has no size info
    members_done: int
    members_total: int | None
```

`total_bytes_estimated` is `None` when the archive format does not provide uncompressed size information. `members_total` is `None` when the total member count cannot be known without a full scan.

#### Scenario: callback invoked per member

- **WHEN** `archivey.extract("archive.zip", "/dest/", on_progress=cb)` is called
- **THEN** `cb` is invoked once for each member processed, with an `ExtractionProgress` carrying that member, cumulative `bytes_written`, and counters for members completed and total

#### Scenario: total_bytes_estimated is None for formats without size info

- **WHEN** the archive format cannot provide uncompressed sizes (e.g. a GZ stream)
- **THEN** `ExtractionProgress.total_bytes_estimated` is `None` for every callback invocation

---

### Requirement: Per-Member ExtractionResult with Status

The system SHALL return a `list[ExtractionResult]` from `archivey.extract()` and `ArchiveReader.extract_all()`, with one entry per member processed. Each result SHALL carry the member, the path it was written to (or `None` if not written), and an `ExtractionStatus`.

```python
@dataclass
class ExtractionResult:
    member: Member
    path: Path | None           # None if skipped
    status: ExtractionStatus    # EXTRACTED, SKIPPED, REJECTED

class ExtractionStatus(Enum):
    EXTRACTED = "extracted"
    SKIPPED   = "skipped"       # due to OverwritePolicy.SKIP
    REJECTED  = "rejected"      # due to filter rejection; no exception raised if
                                # on_rejection=OnRejection.WARN (default: RAISE)
```

#### Scenario: successfully extracted member

- **WHEN** a member is written to disk without error
- **THEN** its `ExtractionResult` has `status=ExtractionStatus.EXTRACTED` and `path` pointing to the file on disk

#### Scenario: skipped member due to OverwritePolicy.SKIP

- **WHEN** a member's destination path already exists and `OverwritePolicy.SKIP` is active
- **THEN** the member's `ExtractionResult` has `status=ExtractionStatus.SKIPPED` and `path=None`

#### Scenario: rejected member due to filter

- **WHEN** a member is blocked by a safety filter and the rejection policy is WARN rather than RAISE
- **THEN** the member's `ExtractionResult` has `status=ExtractionStatus.REJECTED` and `path=None`, and no exception is raised
