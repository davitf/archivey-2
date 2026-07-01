# Safe Extraction — delta (phase-4-safe-extraction)

## ADDED Requirements

### Requirement: Archive-wide decompression ratio for solid containers

The system SHALL evaluate an archive-wide decompression ratio during `extract()` /
`extract_all()` when a member's `compressed_size` is unknown or zero but the reader exposes a
known outer `compressed_source_size` (the byte length of the compressed container stream —
e.g. a `.tar.gz` file's size on disk), computed as:

```
cumulative_bytes_written / compressed_source_size
```

using the same `max_ratio` limit and `ratio_activation_threshold` (default 5 MiB) as the
per-member ratio check. The check SHALL run in `BombTracker.count()` alongside the
cumulative `max_extracted_bytes` guard. Unlike the per-member ratio (which activates on the
**current member's** output), the archive-wide ratio activates on the **cumulative** output
across the call: it is evaluated only once `_total_bytes` exceeds `ratio_activation_threshold`.
When `compressed_source_size` is `None` (unknown source size, plain uncompressed container),
the archive-wide ratio check is skipped.

The `compressed_source_size` is supplied to the `BombTracker` once per extraction call (the
coordinator reads it from the reader and passes it to the constructor):

```python
class BombTracker:
    def __init__(self, max_bytes: int, max_ratio: float,
                 ratio_activation_threshold: int = 5 * 2**20,  # 5 MiB
                 compressed_source_size: int | None = None):
        ...
        self._compressed_source_size = compressed_source_size

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
`FAILED`). The system SHALL NOT silently fall back to copying the target's data the way
`tarfile` does, because that converts a symbolic reference into a materialized file and
bypasses the symlink-escape guarantees.

#### Scenario: symlink to a filtered-out member is created dangling

- **WHEN** a SYMLINK member whose target is another member excluded by the `members` selector / `filter` is extracted, and its resolved target stays within `dest`
- **THEN** the symlink is created pointing at the (absent) target and may dangle; no copy of the target is made and no error is raised

#### Scenario: symlink on a filesystem without symlink support follows OnError

- **WHEN** `os.symlink` raises `OSError`/`NotImplementedError` because the destination filesystem or platform cannot create symlinks
- **THEN** it is a per-member failure: `OnError.STOP` raises and `OnError.CONTINUE` records a `FAILED` `ExtractionResult` and proceeds; the target's data is not copied in its place

## MODIFIED Requirements

### Requirement: Hardlink Two-Pass Extraction

The system SHALL support hardlinks (as found in TAR archives) through the
`ExtractionCoordinator` acting as a **pull-based sink** that inspects the reader
(`cost`, `get_members_if_available()`) and selects an extraction algorithm, rather than a
push-model helper that buffers deferred link-creation state. The source always precedes its
hardlinks in archive order.

- **FILE / DIR / SYMLINK** members are written as they are reached; each written FILE is
  recorded per source in a `{device → on-disk path}` map.
- **HARDLINK** whose source is already written: create it with `os.link()` to a copy on the
  same filesystem device; if `os.link()` fails cross-device, fall back to `shutil.copy2` —
  but prefer `os.link()` to an already-created same-device sibling copy of the source before
  copying again.
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

- **WHEN** a HARDLINK member is reached and its source has already been extracted in this pass on the same device
- **THEN** `os.link(source_path, hardlink_dest)` is called (or `shutil.copy2`, or a same-device sibling `os.link`, on cross-device failure)

#### Scenario: unrecoverable orphaned hardlink follows OnError

- **WHEN** a selected HARDLINK's source was excluded and the source is on a forward-only stream (unrecoverable in one pass)
- **THEN** it is a per-member failure: `OnError.STOP` raises and `OnError.CONTINUE` records a `FAILED` `ExtractionResult` and proceeds

#### Scenario: selected hardlink whose source was excluded (recoverable)

- **WHEN** a selected HARDLINK points to a source the `members` selector / `filter` excluded, and the source is recoverable (a free member list, or a seekable stream via one second pass)
- **THEN** the source content is written to the first selected link's path (further selected links are `os.link`'d to it), and the excluded source is never created at its own destination path
