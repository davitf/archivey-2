# Safe Extraction

## Purpose

Safe extraction writes archive members to a destination directory on disk while enforcing path-safety constraints and permission transforms that prevent untrusted archives from escaping the destination root or writing hostile filesystem objects. It also enforces decompression-bomb limits and reports per-member progress and outcomes. It is the primary interface for callers who want files on disk rather than in-memory data.

## Requirements

### Requirement: One-Shot Extraction API

The system SHALL expose a top-level `archivey.extract()` function that opens an archive, applies safety checks, and writes **all** members to a destination directory in a single call. It deliberately has **no** member-selection parameter: selecting a subset requires the member list, which would force the caller to open the archive first and reopen it here — an anti-pattern. Subset extraction is done through `ArchiveReader.extract_all(members=..., filter=...)` on an already-open reader.

```python
archivey.extract(
    source: str | Path | BinaryIO,
    dest: str | Path,
    *,
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

#### Scenario: subset extraction goes through an open reader

- **WHEN** a caller wants only some members
- **THEN** they open the archive and call `reader.extract_all(dest, members=...)` rather than passing members to the top-level function (which would require reopening)

---

### Requirement: Per-Reader Extract-All Helper

The system SHALL provide a single `extract_all()` instance method on `ArchiveReader` that delegates to the same extraction internals as `archivey.extract()`. There is **no** single-member `reader.extract()`: extracting one file is expressed as `extract_all(members=[name])`, which is also strictly better for solid archives (selecting a set of files costs one pass, whereas one-at-a-time extraction would re-decompress per file).

```python
class ArchiveReader:
    def extract_all(
        self,
        dest: str | Path,
        *,
        members: MemberSelector | None = None,  # names/members or predicate; None = all
        filter: MemberFilter | None = None,      # per-member sanitize/rename; None to skip a member
        policy: ExtractionPolicy = ExtractionPolicy.STRICT,
        overwrite: OverwritePolicy = OverwritePolicy.ERROR,
        on_progress: Callable[[ExtractionProgress], None] | None = None,
    ) -> list[ExtractionResult]: ...
```

`members` selects which members to extract (a collection of names/`ArchiveMember`s, or a
`Callable[[ArchiveMember], bool]` predicate); `None` extracts all. `filter` runs **after**
the universal safety checks and the `policy` transform, letting the caller rename or
further sanitize each member (returning a `.replace()`d copy) or skip it (returning
`None`). `policy` and `overwrite` carry the same meaning as on the top-level function.

#### Scenario: extract all via reader

- **WHEN** `reader.extract_all(dest)` is called
- **THEN** all members are extracted and a `list[ExtractionResult]` is returned, with the same safety guarantees as `archivey.extract()`

#### Scenario: extract a selected subset in one pass

- **WHEN** `reader.extract_all(dest, members=["a.txt", "b.txt"])` is called on a solid archive
- **THEN** only those members are extracted, in a single decompression pass over the archive

#### Scenario: single-file extraction via selector

- **WHEN** a caller wants just one file
- **THEN** they call `reader.extract_all(dest, members=[name])`; there is no separate single-member `extract()` method

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

The resolution itself can fail when the archive contains an adversarial symlink **loop**
(e.g. `a → b` and `b → a`): the OS rejects the cyclic resolution with `ELOOP`. The
implementation SHALL therefore wrap the resolution in a guard that catches `OSError`
(POSIX `ELOOP` and the Windows equivalent) and `RuntimeError` (raised by `Path.resolve()`
on some platforms/versions for loops), and on any such failure SHALL unlink the just-created
link and reject the member with `SymlinkEscapeError` — failing safe rather than crashing the
extractor.

```
os.symlink(link_target, dest_path)
try:
    resolved = (dest_path.parent / link_target).resolve()
    escaped = not resolved.is_relative_to(dest.resolve())
except (OSError, RuntimeError):       # ELOOP / symlink loop / too many levels
    escaped = True                    # treat an unresolvable link as an escape
if escaped:
    dest_path.unlink()
    raise SymlinkEscapeError(...)
```

This single-pass, post-creation check catches TOCTOU symlink attacks where earlier archive members create symlinks that could redirect later writes, and the loop guard keeps an adversarial cyclic-symlink archive from aborting extraction with an uncaught OS error.

#### Scenario: symlink resolves outside dest at extraction time

- **WHEN** a symlink member is written to disk and its resolved target escapes the `dest` root
- **THEN** the symlink is immediately unlinked and `SymlinkEscapeError` is raised
- **AND** no further data is written through that symlink

#### Scenario: chained symlink attack

- **WHEN** an earlier member creates a symlink that redirects a later member's write outside `dest`
- **THEN** the post-creation `Path.resolve()` check catches the escape and raises `SymlinkEscapeError`

#### Scenario: adversarial symlink loop fails safe

- **WHEN** the archive contains a cyclic symlink set (e.g. `a → b`, `b → a`) and the post-creation `Path.resolve()` raises `OSError` (`ELOOP`) or `RuntimeError`
- **THEN** the just-created link is unlinked and `SymlinkEscapeError` is raised; the extractor does not crash with an uncaught OS error

---

### Requirement: Hardlink Two-Pass Extraction

The system SHALL support hardlinks (as found in TAR archives) through a strategy that handles forward-only archive ordering. During the extraction pass:

- **FILE / DIR / SYMLINK** members are written immediately; the mapping from member identity to extracted path is recorded.
- **HARDLINK** members: if the source member's extracted path is already recorded, `os.link()` is called; if not yet extracted, the source is copied with `shutil.copy2()`.
- In streaming mode, TAR guarantees the hardlink target precedes the link in archive order; if the source was filtered out, an explicit error with a clear message is raised.
- In random-access mode, the source may be unselected by the `members` selector or `filter`. The implementation MUST NOT materialize an unselected source at its own final destination path (that would leak a file the caller deliberately excluded). Instead it SHALL make the source's **content** available only through the selected link(s): write the source data to the **first selected link's** path and `os.link()` any further selected links to it; the unselected source name itself is never created. (An implementation MAY equivalently stage the content in a hidden temp file inside `dest` — e.g. `dest/.archivey-tmp-<id>` — link the selected targets to it, then unlink the temp; both approaches satisfy the guarantee.) If **no** link to the source is selected either, the source's data is not extracted at all.
- If `os.link()` fails due to a cross-device error, the implementation SHALL fall back to copying.

#### Scenario: hardlink to already-extracted member

- **WHEN** a HARDLINK member is encountered and its target has already been extracted in the same pass
- **THEN** `os.link(source_path, hardlink_dest)` is called (or `shutil.copy2` on cross-device failure)

#### Scenario: hardlink target not yet extracted in streaming mode

- **WHEN** a HARDLINK member is encountered before its target in streaming mode and the target was filtered out
- **THEN** an explicit error is raised with a clear message

#### Scenario: selected hardlink whose source was excluded (random-access)

- **WHEN** a selected HARDLINK member points to a source that the `members` selector / `filter` excluded
- **THEN** the source content is written to the first selected link's path (further selected links are `os.link()`'d to it), and the excluded source is never created at its own destination path

---

### Requirement: Policy-Specific Metadata Transforms

The system SHALL apply policy-specific permission and ownership transforms to a **transient copy** of the `ArchiveMember` (produced via `member.replace(...)`) after universal checks pass. The transform corresponding to the active `ExtractionPolicy` is selected from `POLICY_TRANSFORMS` in `_filters.py` and applied before any I/O.

Per member the coordinator builds exactly one transient copy: universal checks run on the
original, then the policy transform and then the optional user `filter` are applied to the
copy (in that order). The copy supplies the on-disk **identity** — `name`, `mode`,
timestamps, and therefore the destination path. The **original** `ArchiveMember` is what the
coordinator feeds to `BombTracker.start_member()` and records in the `ExtractionResult`, so
the ratio check and reported metadata use the accurate source values — including any
late-bound fields (final `size`/CRC) the backend fills in place as the data streams, which a
copy taken before reading would not have received. The copy is discarded once the member's
file is written.

```python
class ExtractionPolicy(Enum):
    STRICT   = "strict"    # default; untrusted archives
    STANDARD = "standard"  # moderate trust; e.g. your own older archives
    TRUSTED  = "trusted"   # bypass permission/ownership checks; path safety still enforced
```

**Relationship to Python's `tarfile` filters.** These policies parallel the named
filters in `tarfile` (`data`, `tar`, `fully_trusted`) so callers can transfer that
mental model, but the names differ deliberately because Archivey applies them uniformly
across **all** formats, not just TAR, and the per-bit transforms are Archivey's own:

| `ExtractionPolicy` | Closest `tarfile` filter | Notable differences |
|---|---|---|
| `STRICT` (default) | `data` | Like `data` (blocks unsafe paths/links/special files), and additionally strips execute bits and normalizes permissions to 644/755. Archivey's default; `tarfile`'s default varies by Python version. |
| `STANDARD` | `tar` | Like `tar` (strips setuid/setgid/sticky and group/other-write intent), but Archivey still drops uid/gid and keeps the universal path-safety checks that `tarfile`'s `tar` filter does not all guarantee. |
| `TRUSTED` | `fully_trusted` | Applies stored mode and (as root) uid/gid. Unlike `fully_trusted`, Archivey **still enforces** the non-bypassable universal path/symlink/special-file constraints above. |

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

The system SHALL implement safe extraction in a dedicated `_extraction.py` module (`ExtractionCoordinator`) that is separate from the reader backends and format detection. Both `archivey.extract()` and `ArchiveReader.extract_all()` SHALL delegate to the same `ExtractionCoordinator`, which drives a single unified forward pass over the reader's `(member, stream)` pairs.

The coordinator — not the reader — is where member **transformation** lives. The reader's
streaming generator yields the **original, mutable** `ArchiveMember` objects (so the backend
can keep filling late-bound fields in place); the coordinator applies the `members` selector,
the policy transform, and the user `filter` to a transient copy (per the metadata-transform
requirement above), keeping the original for the `BombTracker` and `ExtractionResult`. This
localization is deliberate: applying a copy-producing `filter` inside the reader's generator
would detach the yielded copy from the backend's in-place late-bound updates.

Decompression-bomb enforcement (see the bomb-limit requirements below) is handled by a `BombTracker` instance passed through to each member extraction during this pass. Progress callbacks and `ExtractionResult` accumulation also happen inside this single pass.

#### Scenario: streaming and random-access modes use same coordinator

- **WHEN** `extract_all()` is called whether the reader is in streaming or random-access mode
- **THEN** the same `ExtractionCoordinator.run()` single forward pass is used for both paths

---

### Requirement: Enforce Cumulative Max-Extracted-Bytes Limit

The system SHALL track the total number of bytes written across all members during a single `extract()` or `extract_all()` call and SHALL raise `ExtractionError` when that cumulative total exceeds `max_extracted_bytes`. The default limit is 2 GiB (2 147 483 648 bytes). The caller MAY override this limit by passing `max_extracted_bytes` to `extract()` or `extract_all()`.

The limit is tracked by a `BombTracker` instance constructed once per extraction call. The cumulative byte total spans all members in the call; the per-member ratio (next requirement) is tracked separately against each member's own output.

```python
class BombTracker:
    def __init__(self, max_bytes: int, max_ratio: float,
                 ratio_activation_threshold: int = 5 * 2**20):  # 5 MiB
        self._max_bytes = max_bytes
        self._max_ratio = max_ratio
        self._ratio_floor = ratio_activation_threshold
        self._total_bytes = 0          # cumulative across all members
        self._member_bytes = 0         # output bytes for the current member
        self._member: ArchiveMember | None = None

    def start_member(self, member: ArchiveMember) -> None:
        # Called with the ORIGINAL member (not a filter copy), so compressed_size
        # and any late-bound fields are the accurate source values.
        self._member = member
        self._member_bytes = 0

    def count(self, chunk_bytes: int) -> None:
        self._total_bytes += chunk_bytes
        self._member_bytes += chunk_bytes
        if self._total_bytes > self._max_bytes:
            raise ExtractionError(
                f"Extraction limit reached: {self._total_bytes} bytes > {self._max_bytes}"
            )
        # The ratio is only evaluated once THIS member's output exceeds the
        # activation threshold, so a tiny but highly-compressible legitimate file
        # (e.g. 10 bytes -> 15 KiB = 1500:1) cannot trip a false positive.
        cs = self._member.compressed_size if self._member else None
        if self._member_bytes > self._ratio_floor and cs and cs > 0:
            ratio = self._member_bytes / cs
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

### Requirement: Enforce Per-ArchiveMember Max Decompression Ratio

The system SHALL raise `ExtractionError` when the decompression ratio for a single member exceeds `max_ratio` during extraction. The default ratio limit is 1000:1. The caller MAY override this by passing `max_ratio` to `extract()` or `extract_all()`.

The ratio for a member is computed as `bytes_written_for_member / member.compressed_size` (per-member output, **not** the cumulative total). The check is performed only when `member.compressed_size` is known and greater than zero, **and** only after that member's output has exceeded a `ratio_activation_threshold` (default 5 MiB, caller-configurable). The threshold prevents false positives on tiny but legitimately highly-compressible files: a 10-byte source expanding to 15 KiB is a 1500:1 ratio yet harmless, whereas a real bomb expands to hundreds of MiB or GiB and trips the ratio only after crossing the floor. The default of 1000:1 is otherwise deliberately generous — typical DEFLATE compresses at 3:1 to 10:1, and even pathological quine-style zip bombs produce outer-layer ratios around 391:1 — so the limit catches only pathological cases without triggering on legitimately highly-compressible data.

#### Scenario: single member exceeds ratio limit

- **WHEN** a single member decompresses to more than `max_ratio` times its compressed size **and** its output has passed the `ratio_activation_threshold`
- **THEN** `ExtractionError` is raised while processing that member

#### Scenario: tiny highly-compressible file below the activation threshold is not flagged

- **WHEN** a member's output exceeds `max_ratio` times its compressed size but stays under the `ratio_activation_threshold` (default 5 MiB) — e.g. a few bytes expanding to a few KiB
- **THEN** no `ExtractionError` is raised; the ratio check does not activate until the member's output crosses the threshold

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
    member: ArchiveMember
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

### Requirement: Per-ArchiveMember ExtractionResult with Status

The system SHALL return a `list[ExtractionResult]` from `archivey.extract()` and `ArchiveReader.extract_all()`, with one entry per member processed. Each result SHALL carry the member, the path it was written to (or `None` if not written), and an `ExtractionStatus`.

```python
@dataclass
class ExtractionResult:
    member: ArchiveMember
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
