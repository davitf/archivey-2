# Safe Extraction — delta (phase-4-safe-extraction)

## ADDED Requirements

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

## MODIFIED Requirements

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
  data to the **first selected link's** path and `os.link()` further selected links to it (an
  implementation MAY equivalently stage in a hidden temp inside `dest`, e.g.
  `dest/.archivey-tmp-<id>`). If no link to the source is selected either, the source's data is
  not extracted at all. **How** the excluded source's bytes are obtained is chosen so no pass
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
- **THEN** the source content is written to the first selected link's path (further selected links are `os.link`'d to it), and the excluded source is never created at its own destination path
