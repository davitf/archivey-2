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
    on_error: OnError = OnError.STOP,
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
        on_error: OnError = OnError.STOP,
        on_progress: Callable[[ExtractionProgress], None] | None = None,
    ) -> list[ExtractionResult]: ...
```

`members` selects which members to extract (a collection of names/`ArchiveMember`s, or a
`Callable[[ArchiveMember], bool]` predicate); `None` extracts all. In the collection form a
`str` entry matches **every** member with that name (duplicates included), while an
`ArchiveMember` entry matches only that exact member, by object identity — so with duplicate
names the caller can select one specific occurrence (the same semantics the Phase 5
`MemberSelector` collection form specifies). `filter` runs **after**
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

The system SHALL enforce the following constraints on every member before extraction
regardless of the `ExtractionPolicy` in use, including `ExtractionPolicy.TRUSTED`. These
checks are applied by `check_universal()` in `filters.py` as the first step of the extraction
pipeline, before any policy transform.

Because `member.name` is now a **faithful** representation of the stored path (see
`archive-data-model` — read-time normalization no longer strips a leading `/` or collapses
`..`), these checks operate directly on `member.name`; there is no separate check against the
verbatim `raw_name` (the interim mechanism introduced while normalization still collapsed
traversal is removed).

This is the default (`RAISE`) path-safety behavior. A future opt-in `SANITIZE` policy (phase 5)
re-roots/collapses an unsafe name to a safe in-`dest` path instead of rejecting it; there is no
path-safety "trust" bypass. The constraints below describe `RAISE`.

Three independent enforcement layers provide defense in depth:

1. **String check on `member.name`** — purely string-based, before any I/O: reject an
   absolute path (leading `/`, a Windows drive letter, or a UNC `\\`), reject **any** `..`
   path component (split on both `/` and `\`), and reject a `\x00` null byte. A `..` is
   rejected whether it escapes the root or is internal (`foo/../bar`): a well-formed archive
   has no reason to carry one, so it is treated as almost-certainly-malicious.
2. **Pre-extraction path computation** — the destination's **parent directory**,
   `(dest / member.name).parent`, is resolved with `.resolve()` and verified to remain within
   `dest.resolve()`. With `..` already rejected in layer 1, this layer's remaining job is to
   catch a **symlinked intermediate component** (an earlier member's symlink that would
   redirect a later write outside `dest`). The parent — not the full path — is resolved so a
   pre-existing final-component symlink is handled by the `OverwritePolicy` (unlink-then-create)
   rather than followed.
3. **Post-symlink-creation check** — after `os.symlink()`, the created link's target is
   re-resolved with `Path.resolve()` to detect chained symlink attacks (see *Symlink Escape
   Re-Validated at Extraction Time*).

The individual universal constraints are:

| Constraint | Violation type | Condition |
|---|---|---|
| Path traversal | `PathTraversalError` | Any `..` path component in `member.name` (escaping or internal) |
| Absolute paths | `PathTraversalError` | `member.name` starts with `/`, a Windows drive letter (`C:\`), or `\\` |
| Null bytes | `PathTraversalError` | `member.name` contains `\x00` |
| Symlink escape | `SymlinkEscapeError` | SYMLINK member whose fully-resolved target escapes `dest` |
| Hardlink escape | `SymlinkEscapeError` | HARDLINK member whose target path resolves outside `dest` |
| Special files | `SpecialFileError` | `MemberType.OTHER` (device nodes, FIFOs, sockets) |

#### Scenario: escaping traversal in member name

- **WHEN** a member's `name` is `"../evil"` or `"../../etc/passwd"` (an escaping `..`)
- **THEN** `PathTraversalError` is raised and no file is written, regardless of policy

#### Scenario: internal traversal is also rejected

- **WHEN** a member's `name` is `"foo/../bar"` (a `..` that would resolve within the root)
- **THEN** `PathTraversalError` is raised under the default `RAISE`; extracting it requires the
  opt-in `SANITIZE` policy (phase 5)

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

The system SHALL support hardlinks (as found in TAR archives) through the
`ExtractionCoordinator` acting as a **pull-based sink** that uses `get_members_if_available()`
(for the optional optimization) and, only on an orphan, the source's re-readability, and
selects an extraction algorithm — rather than a push-model helper that buffers deferred
link-creation state. The source always precedes its hardlinks in archive order.

- **FILE / DIR / SYMLINK** members are written as they are reached; each written FILE's path is
  recorded under a per-source **list of on-disk paths**.
- **HARDLINK** whose source is already written: create it by trying `os.link()` against each
  recorded path of the source in turn; the first that succeeds wins. If every attempt fails
  cross-device (`EXDEV`), fall back to `shutil.copy2` and append the new path (so a later link
  on that device can `os.link()` to this copy instead of copying again).
- **HARDLINK whose source was excluded** by the `members` selector or `filter` (only possible
  when filtering): the implementation MUST NOT materialize the excluded source at its own
  destination path (that would leak a file the caller deliberately excluded). It SHALL instead
  make the source's **content** available only through the selected link(s) — write the source
  data to the **first selected link whose destination the `OverwritePolicy` allows writing**
  (under `SKIP`, links whose destinations already exist are recorded `SKIPPED` and the content
  moves on to the next selected link; if every link is skipped, nothing is written) and
  `os.link()` further selected links to it (an implementation MAY equivalently stage in a
  hidden temp inside `dest`, e.g. `dest/.archivey-tmp-<id>`). The materialized file carries the
  **transformed metadata of the link it is written as** (mode/timestamps per the "copy supplies
  the identity" rule below — hardlinks share one inode, so the metadata goes on the file that
  carries the content). If no link to the source is selected either, the source's data is
  not extracted at all.
  A hardlink that merely *precedes* its source in archive order (the source is selected and
  extracted later in the same pass) is NOT re-read: it SHALL be `os.link()`ed against the
  already-extracted source file, sharing its inode, with no second read of the source's bytes
  (and no double-counting against the bomb limits). **How** the excluded source's bytes are obtained is chosen so no pass
  is wasted (see `format-tar`): when a member list is available for free
  (`get_members_if_available()` — a true index or an already-materialized list) the source is
  staged during a single planned forward pass; otherwise (plain `.tar` or compressed tar, with
  no speculative scan) it is recovered from a seekable source in one conditional second pass;
  on a **forward-only** source its bytes are unrecoverable and the link is a per-member failure
  handled by the `OnError` policy (STOP raises, CONTINUE records `FAILED`).

#### Scenario: hardlink to already-extracted member

- **WHEN** a HARDLINK member is reached and its source has already been extracted in this pass
- **THEN** the coordinator tries `os.link()` against the source's recorded on-disk paths in turn (falling back to a sibling link or `shutil.copy2` on cross-device failure)

#### Scenario: unrecoverable orphaned hardlink follows OnError

- **WHEN** a selected HARDLINK's source was excluded and the source is on a forward-only stream (unrecoverable in one pass)
- **THEN** it is a per-member failure: `OnError.STOP` raises and `OnError.CONTINUE` records a `FAILED` `ExtractionResult` and proceeds

#### Scenario: selected hardlink whose source was excluded (recoverable)

- **WHEN** a selected HARDLINK points to a source the `members` selector / `filter` excluded, and the source is recoverable (a free member list, or a seekable stream via one second pass)
- **THEN** the source content is written to the first selected link's path with that link's transformed mode/timestamps (further selected links are `os.link`'d to it), and the excluded source is never created at its own destination path

#### Scenario: excluded source with the first link's destination skipped

- **WHEN** an excluded source's selected links are materialized under `OverwritePolicy.SKIP` and the first link's destination already exists
- **THEN** that link is recorded `SKIPPED`, the source content is written to the next selected link instead, and no error escapes; if every link's destination exists, all are `SKIPPED` and the content is not written anywhere

#### Scenario: hardlink preceding its source in archive order

- **WHEN** a selected HARDLINK appears before its (also selected) source member in archive order
- **THEN** after the pass the link is `os.link`'d against the extracted source file (same inode); the source's bytes are read once and counted once against the bomb limits

### Requirement: Policy-Specific Metadata Transforms

The system SHALL apply policy-specific permission and ownership transforms to a **transient copy** of the `ArchiveMember` (produced via `member.replace(...)`) after universal checks pass. The transform corresponding to the active `ExtractionPolicy` is selected from `POLICY_TRANSFORMS` in `filters.py` and applied before any I/O.

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

The system SHALL enforce the `OverwritePolicy` when a destination entry already exists at the
path a member would be written to.

```python
class OverwritePolicy(Enum):
    ERROR   = "error"   # raise ExtractionError if destination entry exists
    SKIP    = "skip"    # silently skip existing entries
    REPLACE = "replace" # replace unconditionally (atomically, never write-through)
```

`ERROR` raises an `ExtractionError` for the member, which is a per-member failure governed by
the `OnError` policy (`STOP` re-raises and halts; `CONTINUE` records a `FAILED`
`ExtractionResult` and proceeds). `SKIP` records a `SKIPPED` result regardless of `OnError`.

Two symlink-safety rules apply to all three policies:

- **Existence is checked with `lstat` semantics** (the entry itself, never its target). A
  *dangling* symlink at the destination path counts as an existing entry — a follow-the-link
  existence check would report "absent" and a subsequent open-for-writing would create the
  file at the symlink's **target**, an attacker-controllable location.
- **`REPLACE` is atomic and never writes through a symlink.** A FILE is streamed into a temp
  file in the destination directory, has its metadata applied, and is then moved onto the
  destination path with `os.replace()` — a single atomic operation that overwrites an existing
  file or **replaces an existing symlink with the fresh file** (it operates on the directory
  entry, so bytes never follow the link to its target). Because the move is atomic and the
  existing entry is untouched until the new file is fully written, a failure **mid-extraction**
  (a decompression error, a bomb-limit trip, a write error) leaves the previous destination
  intact and discards only the temp file — it never truncates or removes the old data. An
  existing **directory** being replaced by a file is removed first (`os.replace` cannot
  overwrite a directory with a file). DIR / SYMLINK / HARDLINK members, which carry no streamed
  data, are still created by removing any existing entry and creating fresh.

#### Scenario: ERROR raises on existing file

- **WHEN** a member would write to a path that already exists on disk and `OverwritePolicy.ERROR` is active
- **THEN** `ExtractionError` is raised and the existing file is not modified

#### Scenario: SKIP silently bypasses existing files

- **WHEN** a member would write to an existing path and `OverwritePolicy.SKIP` is active
- **THEN** the member is skipped; its `ExtractionResult` carries `ExtractionStatus.SKIPPED`

#### Scenario: REPLACE overwrites atomically

- **WHEN** a member would write to an existing path and `OverwritePolicy.REPLACE` is active
- **THEN** the existing entry is replaced with the member's data via a temp file + `os.replace`

#### Scenario: REPLACE of an existing symlink does not write through it

- **WHEN** the destination path is an existing symlink (e.g. planted by an earlier member) and `OverwritePolicy.REPLACE` is active
- **THEN** the symlink itself is replaced by the fresh file and no bytes are ever written through the old link to its target

#### Scenario: a failed REPLACE preserves the existing file

- **WHEN** a member is extracted under `OverwritePolicy.REPLACE` over an existing file but extraction fails mid-stream (e.g. a bomb-limit trip or a corrupt/truncated source)
- **THEN** the existing file is left unchanged and only the temp file is discarded; the old data is never truncated or removed

#### Scenario: dangling symlink at the destination counts as existing

- **WHEN** the destination path is a dangling symlink and `OverwritePolicy.ERROR` (or `SKIP`) is active
- **THEN** the entry is treated as existing — `ExtractionError` is raised (or the member is skipped); the extractor never opens the path for writing, which would create the file at the link's target

---

### Requirement: Extraction as a Composable Module

The system SHALL implement safe extraction in a dedicated `internal/extraction.py` module (`ExtractionCoordinator`) that is separate from the reader backends and format detection. Both `archivey.extract()` and `ArchiveReader.extract_all()` SHALL delegate to the same `ExtractionCoordinator`, which drives a single unified forward pass over the reader's `(member, stream)` pairs.

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
    path: Path | None            # the written path, or None if not written
    status: ExtractionStatus
    error: ArchiveyError | OSError | None = None   # the failure, for FAILED/REJECTED under
                                         # OnError.CONTINUE; an OSError when the failure is a
                                         # filesystem read/write error on this member

class ExtractionStatus(Enum):
    EXTRACTED = "extracted"
    SKIPPED   = "skipped"       # pre-existing destination, under OverwritePolicy.SKIP
    REJECTED  = "rejected"      # blocked by a safety filter (universal or policy check)
    FAILED    = "failed"        # error while extracting (corrupt/truncated/encrypted data,
                                # ratio bomb, write error) — recorded under OnError.CONTINUE
```

#### Scenario: successfully extracted member

- **WHEN** a member is written to disk without error
- **THEN** its `ExtractionResult` has `status=ExtractionStatus.EXTRACTED` and `path` pointing to the file on disk

#### Scenario: skipped member due to OverwritePolicy.SKIP

- **WHEN** a member's destination path already exists and `OverwritePolicy.SKIP` is active
- **THEN** the member's `ExtractionResult` has `status=ExtractionStatus.SKIPPED` and `path=None`

#### Scenario: rejected member under OnError.CONTINUE

- **WHEN** a member is blocked by a safety filter and `OnError.CONTINUE` is active
- **THEN** the member's `ExtractionResult` has `status=ExtractionStatus.REJECTED`, `path=None`, and `error` set to the `FilterRejectionError`, and extraction continues

#### Scenario: failed member under OnError.CONTINUE

- **WHEN** a member fails to extract (e.g. `CorruptionError`) and `OnError.CONTINUE` is active
- **THEN** any partial output for that member is removed, its `ExtractionResult` has `status=ExtractionStatus.FAILED` and `error` set, and extraction proceeds to the next member

---

### Requirement: Error Policy (OnError) for extraction failures

The system SHALL accept an `on_error` parameter on `archivey.extract()` and
`ArchiveReader.extract_all()` that governs what happens when an individual member cannot
be extracted — distinct from `OverwritePolicy`, which governs only pre-existing
destination files.

```python
class OnError(Enum):
    STOP     = "stop"      # default: raise the first failure and halt (no further members)
    CONTINUE = "continue"  # best-effort: record the failure, clean up, proceed to the next member
```

A per-member failure is an error raised while processing one member. It is usually an
`ArchiveyError` — a `FilterRejectionError` (universal/policy safety check), a data error
(`CorruptionError`/`TruncatedError`/`EncryptionError`), or the per-member ratio
`ExtractionError` — but it **also includes a plain filesystem `OSError`** raised while
reading this member's bytes out of the source archive or writing its output file to disk
(a permission error, a name the OS rejects, an I/O error on the destination, etc.). Those
filesystem errors are *not* translated into `ArchiveyError`s; they are caught at the
extraction-coordinator level and recorded (or, under `STOP`, re-raised) as-is — which is
why `ExtractionResult.error` is typed `ArchiveyError | OSError`.

- **`OnError.STOP` (default):** the first per-member failure is raised immediately;
  already-extracted members remain on disk, the failing member's partial output is
  removed, and no further members are processed. This is the safe default for untrusted
  input — you learn about the problem at once.
- **`OnError.CONTINUE`:** the failure is caught, the failing member's partial output (if
  any) is removed (a failed member never leaves a half-written file), an
  `ExtractionResult` with `status` `REJECTED` (filter) or `FAILED` (other) and `error`
  set is recorded, a `logging.WARNING` is emitted, and extraction proceeds to the next
  member. The returned `list[ExtractionResult]` is the report — it carries every member's
  outcome, including the `REJECTED`/`FAILED` entries; the library does **not** raise an
  aggregate at the end (the caller inspects `status`/`error`).

`on_error` replaces the earlier ad-hoc rejection flag: `STOP` is the old "raise on
rejection", `CONTINUE` is the old "warn and skip", now applied uniformly to all failure
kinds.

**Always-stop exceptions, regardless of `on_error`:**
- The cumulative `max_extracted_bytes` limit is a global resource guard — exceeding it
  raises `ExtractionError` and halts even under `CONTINUE` (continuing would defeat the
  guard). (The *per-member* ratio limit is a per-member failure and is skippable under
  `CONTINUE`.)
- `KeyboardInterrupt`, `MemoryError`, and any exception that is neither an `ArchiveyError`
  nor a per-member filesystem `OSError` (a programming error such as `TypeError`, a
  `SystemExit`, …) propagate unchanged and are never swallowed by `CONTINUE`. The carve-out
  is deliberate: a per-member filesystem `OSError` (above) *is* caught under `CONTINUE`,
  whereas an unexpected non-IO exception signals a bug and must surface.

#### Scenario: STOP halts on the first failure

- **WHEN** a member fails mid-extraction under the default `OnError.STOP`
- **THEN** the exception is raised immediately, the failing member's partial file is removed, and no later members are processed (earlier ones stay on disk)

#### Scenario: CONTINUE extracts the good members and reports the rest

- **WHEN** an archive with some corrupt members is extracted under `OnError.CONTINUE`
- **THEN** every extractable member is written, and the returned `list[ExtractionResult]` contains `EXTRACTED` entries plus `FAILED`/`REJECTED` entries (each with its `error`); no exception is raised for the per-member failures

#### Scenario: cumulative bomb limit still halts under CONTINUE

- **WHEN** the cumulative `max_extracted_bytes` limit is exceeded during a `CONTINUE` extraction
- **THEN** `ExtractionError` is raised and extraction halts regardless of `on_error`

#### Scenario: filesystem write error is a per-member failure

- **WHEN** writing a member's output file fails with an `OSError` (e.g. a permission error or an I/O error on the destination)
- **THEN** under `OnError.CONTINUE` the partial file is removed, the member's `ExtractionResult` has `status=FAILED` and `error` set to the `OSError`, and extraction proceeds; under `OnError.STOP` the `OSError` is raised directly

### Requirement: Archive-wide decompression ratio for solid containers

The system SHALL evaluate an archive-wide decompression ratio during `extract()` /
`extract_all()` when a member's `compressed_size` is unknown or zero but the reader exposes a
known outer `compressed_source_size` (the byte size of the archive's source, generalized
and **cheap-only** — it never reads or decompresses data to answer: a path source is
`stat`-ed; an integer `size` attribute is trusted (the fsspec convention, which archivey's
own member/codec stream wrappers also expose from archive metadata or a cheap
index/trailer scan — so a *nested* archive opened from a member stream gets its source
size for free); a `try_get_size()` method (archivey's decompressor streams) is the final
answer for streams whose end-seek would decompress; and a `SEEK_END`/restore probe runs
only for provably O(1) types (real files, `BytesIO`, `mmap`). Anything else — notably any
foreign decompressor stream like `gzip.GzipFile`, whose end-seek decodes the whole
payload — yields `None`. For zip/7z/rar/compressed-tar this *is* the compressed size; for
an uncompressed container the resulting ~1:1 ratio never trips the guard, which is why
reporting it for every source is safe), computed as:

```
cumulative_bytes_written / compressed_source_size
```

using the same `max_ratio` limit and `ratio_activation_threshold` (default 5 MiB) as the
per-member ratio check. The check SHALL run in `BombTracker.count()` alongside the
cumulative `max_extracted_bytes` guard. Unlike the per-member ratio (which activates on the
**current member's** output), the archive-wide ratio activates on the **cumulative** output
across the call: it is evaluated only once `_total_bytes` exceeds `ratio_activation_threshold`.
When `compressed_source_size` is `None` (a source whose size is not cheaply knowable),
the archive-wide ratio check is skipped.

The `compressed_source_size` is supplied to the `BombTracker` once per extraction call (the
coordinator reads it from the reader and passes it to the constructor):

```python
class BombTracker:
    def __init__(self, max_bytes: int, max_ratio: float,
                 ratio_activation_threshold: int = 5 * 2**20,  # 5 MiB
                 compressed_source_size: int | None = None,
                 max_entries: int = 1_048_576):   # entry-count guard (see below)
        ...
        self._compressed_source_size = compressed_source_size
        self._max_entries = max_entries
        self._entry_count = 0

    def start_member(self, member: ArchiveMember) -> None:
        # ... (records the ORIGINAL member as before), plus the entry-count guard:
        self._entry_count += 1
        if self._entry_count > self._max_entries:
            raise ExtractionError(
                f"Entry-count limit reached: {self._entry_count} > {self._max_entries}"
            )

    def count(self, chunk_bytes: int) -> None:
        self._total_bytes += chunk_bytes
        self._member_bytes += chunk_bytes
        if self._total_bytes > self._max_bytes:
            raise ExtractionError(...)                 # cumulative byte guard (unchanged)
        # Per-member ratio: activates on the current member's output (unchanged).
        cs = self._member.compressed_size if self._member else None
        if self._member_bytes > self._ratio_floor and cs and cs > 0:
            if self._member_bytes / cs > self._max_ratio:
                raise ExtractionError(...)
        # Archive-wide ratio: activates on the cumulative output; only when the outer
        # compressed size is known. Independent of the per-member guard above.
        css = self._compressed_source_size
        if self._total_bytes > self._ratio_floor and css and css > 0:
            if self._total_bytes / css > self._max_ratio:
                raise ExtractionError(...)
```

Per-member ratio (when `member.compressed_size` is known and greater than zero) and
archive-wide ratio are independent guards; either may trip first.

#### Scenario: compressed tar extract trips archive-wide ratio

- **WHEN** a small `.tar.gz` (known file size) is extracted and cumulative output exceeds
  `max_ratio` times the file size after crossing the activation threshold
- **THEN** `ExtractionError` is raised during extraction

#### Scenario: archive-wide ratio skipped when outer size unknown

- **WHEN** a compressed tar is extracted from a non-seekable pipe with unknown total size
- **THEN** the archive-wide ratio check is not applied
- **AND** the cumulative `max_extracted_bytes` limit still applies

#### Scenario: plain tar has no archive-wide ratio

- **WHEN** a plain `.tar` is extracted
- **THEN** the archive-wide ratio check is not applied (no compressed outer stream)

#### Scenario: ZIP keeps per-member ratio

- **WHEN** a ZIP member with known `compressed_size` is extracted
- **THEN** the per-member ratio check applies as today
- **AND** the archive-wide ratio is not used in place of per-member `compressed_size`

---

### Requirement: Enforce Maximum Entry Count

The system SHALL track the number of archive members processed during a single `extract()` /
`extract_all()` call and SHALL raise `ExtractionError` when it exceeds `max_entries`. This
guards against an **entry-count / inode-exhaustion bomb** — an archive packing an enormous
number of tiny (often zero-byte) files or directories that overwhelms the filesystem (inodes,
per-directory entries, per-file syscall overhead) *without* tripping `max_extracted_bytes`
(there is little data) or the decompression ratio (each entry compresses normally). Every
member counts (FILE, DIR, SYMLINK, HARDLINK); the counter is incremented in
`BombTracker.start_member()`.

Like the cumulative `max_extracted_bytes` limit, this is a global resource guard, so exceeding
it halts extraction **even under `OnError.CONTINUE`** (continuing would defeat the guard). The
caller MAY override `max_entries` on `extract()` / `extract_all()`. The default is
`1_048_576` (2²⁰) entries — generous enough for large legitimate archives (a Linux source
tarball or a `node_modules` bundle can hold hundreds of thousands of files) while still
bounding a pathological many-entries bomb. This limit is independent of the byte and ratio
guards; any of them may trip first.

#### Scenario: archive with too many entries is rejected

- **WHEN** an archive containing more than `max_entries` members is extracted
- **THEN** `ExtractionError` is raised once the count crosses the limit, and extraction halts even under `OnError.CONTINUE`

#### Scenario: caller overrides the entry-count limit

- **WHEN** `archivey.extract(..., max_entries=100)` is called on an archive with more than 100 members
- **THEN** `ExtractionError` is raised after the 100th member is started

#### Scenario: entry count is independent of byte and ratio limits

- **WHEN** an archive of many tiny files stays well under `max_extracted_bytes` and never trips the decompression ratio but exceeds `max_entries`
- **THEN** `ExtractionError` is still raised on the entry-count guard

---

### Requirement: Symlink extraction is target-independent and fails safe on unsupported filesystems

The system SHALL create SYMLINK members as symbolic references via `os.symlink()` without
requiring the link's target to exist or to be among the extracted members. Unlike a hardlink
(which needs a real inode and therefore its source materialized), a symlink is a stored path
string, so a symlink whose target was filtered out, appears later in the archive, or lies
outside the archive is created as-is and MAY dangle — the only constraint is the universal
symlink-escape check (the resolved target must remain within `dest`; see *Symlink Escape
Re-Validated at Extraction Time*). No copy of the target is made.

When the destination filesystem or platform cannot create a symlink (`os.symlink` raises
`OSError`/`NotImplementedError` — e.g. FAT, or Windows without the symlink privilege), the
member is a per-member failure handled by the `OnError` policy (STOP raises, CONTINUE records
`FAILED`). The system SHALL NOT silently fall back to copying the target's data.

**Deliberate deviation from `tarfile`.** Python's `tarfile`, on a symlink-unsupported
platform, silently copies the in-archive target's data into a regular file at the link path
(raising `ExtractError` only if the target isn't in the archive). Archivey does **not**: that
converts a symbolic reference into a materialized file and bypasses the symlink-escape
guarantees, so a failure is surfaced via `OnError` instead. A future opt-in policy may offer a
`tarfile`-style copy fallback (tracked in `IDEAS.md`), but the safe behavior is the default.

#### Scenario: symlink to a filtered-out member is created dangling

- **WHEN** a SYMLINK member whose target is another member excluded by the `members` selector / `filter` is extracted, and its resolved target stays within `dest`
- **THEN** the symlink is created pointing at the (absent) target and may dangle; no copy of the target is made and no error is raised

#### Scenario: symlink on a filesystem without symlink support follows OnError

- **WHEN** `os.symlink` raises `OSError`/`NotImplementedError` because the destination filesystem or platform cannot create symlinks
- **THEN** it is a per-member failure: `OnError.STOP` raises and `OnError.CONTINUE` records a `FAILED` `ExtractionResult` and proceeds; the target's data is not copied in its place

---

### Requirement: Live archive-wide decompression ratio for unknown-size streams

The system SHALL evaluate a **live** archive-wide decompression ratio during `extract()` /
`extract_all()` when neither a per-member `compressed_size` nor a cheap outer
`compressed_source_size` is available to serve as a denominator — a compressed archive (e.g. a
`.tar.gz`) read from a non-seekable pipe, **or from a seekable stream whose size is not cheaply
knowable** (not a whitelisted O(1)-seek type, no `size` attribute, no `try_get_size()`), where
otherwise only the cumulative `max_extracted_bytes` cap would apply. A compressed backend
therefore wraps any stream source whose `source_byte_size()` is `None` in the counting reader —
the exact complement of the static denominator, so one of the two is always available.

The live ratio is computed as:

```
cumulative_bytes_written / compressed_bytes_consumed
```

where `compressed_bytes_consumed` is the running count of compressed bytes pulled from the
archive's outer source (see the `compressed-streams` capability). `BombTracker` SHALL raise
`ExtractionError` once this ratio exceeds `max_ratio`, evaluated only after the cumulative
output (`_total_bytes`) passes `ratio_activation_threshold` (default 5 MiB) — the same limit and
floor as the static ratio checks.

Because compressed bytes cannot be attributed to a single member in a solid or streamed
container, the live ratio is a **cumulative / archive-wide** guard: it extends the existing
archive-wide ratio with a live denominator. It is a global resource guard, so like the
cumulative `max_extracted_bytes` and `max_entries` limits it halts extraction **even under
`OnError.CONTINUE`**.

This guard **complements** the static checks and does not replace them:

- When `member.compressed_size` is known (ZIP), the per-member ratio still applies.
- When `compressed_source_size` is known (a size-probeable compressed archive), the static
  archive-wide ratio applies and the live path is **not** used (no double-counting).
- The live path engages only when both static denominators are absent and a
  `compressed_bytes_consumed` count is available.

Whichever guard has a usable denominator may trip first.

#### Scenario: streaming tar.gz bomb from a pipe is caught by the live ratio

- **WHEN** a highly compressible `.tar.gz` is extracted from a non-seekable pipe (so
  `compressed_source_size` is `None` and TAR members have no `compressed_size`) and its output
  exceeds `max_ratio` times the compressed bytes consumed after crossing the activation threshold
- **THEN** `ExtractionError` is raised during extraction, before the absolute `max_extracted_bytes`
  cap is reached

#### Scenario: live ratio halts even under OnError.CONTINUE

- **WHEN** the live archive-wide ratio is exceeded during a `CONTINUE` extraction
- **THEN** `ExtractionError` is raised and extraction halts regardless of `on_error`

#### Scenario: uncompressed stream does not trip the live ratio

- **WHEN** a plain (uncompressed) `.tar` is extracted from a pipe, so consumed ≈ written (~1:1)
- **THEN** the live ratio never trips; the cumulative `max_extracted_bytes` limit still applies

#### Scenario: known outer size keeps the static archive-wide ratio

- **WHEN** a `.tar.gz` with a cheaply knowable `compressed_source_size` is extracted
- **THEN** the static archive-wide ratio is used and the live path is not engaged (the ratio is
  not counted twice)

#### Scenario: seekable stream with no cheap size still gets the live ratio

- **WHEN** a compressed archive is extracted from a *seekable* stream whose size is not cheaply
  knowable (an opaque stream type: not whitelisted for `SEEK_END`, no `size` attribute, no
  `try_get_size()`), so `compressed_source_size` is `None`
- **THEN** the source is wrapped in the counting reader and the live archive-wide ratio applies —
  the archive is not left with only the `max_extracted_bytes` cap. (Codec-layer seeks may re-read
  counted bytes; that only inflates the denominator, weakening the guard, never a false positive.)

