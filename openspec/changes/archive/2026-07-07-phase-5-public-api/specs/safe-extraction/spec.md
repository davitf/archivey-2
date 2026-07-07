# safe-extraction — Phase 5 deltas

## MODIFIED Requirements

### Requirement: One-Shot Extraction API

The system SHALL expose a top-level `archivey.extract()` function that opens an archive, applies safety checks, and writes **all** members to a destination directory in a single call. It deliberately has **no** member-selection parameter: selecting a subset requires the member list, which would force the caller to open the archive first and reopen it here — an anti-pattern. Subset extraction is done through `ArchiveReader.extract_all(members=..., filter=...)` on an already-open reader.

```python
archivey.extract(
    source: str | Path | BinaryIO | Sequence[str | Path | BinaryIO],
    dest: str | Path,
    *,
    policy: ExtractionPolicy = ExtractionPolicy.STRICT,
    overwrite: OverwritePolicy = OverwritePolicy.ERROR,
    on_error: OnError = OnError.STOP,
    format: ArchiveFormat | None = None,
    password: str | bytes | Sequence[str | bytes] | PasswordProvider | None = None,
    encoding: str | None = None,
    on_progress: Callable[[ExtractionProgress], None] | None = None,
    config: ArchiveyConfig | None = None,
    limits: ExtractionLimits | None = None,
) -> list[ExtractionResult]
```

The function delegates to `open_archive()`, so its `source`, `password`, and `encoding`
parameters carry exactly the `open_archive()` semantics (see `archive-reading`): `source`
MAY be an ordered sequence forming a multi-volume archive, `password` accepts the
candidate/provider model, and `encoding` overrides member-name decoding for formats that
record none. The four loose bomb-limit keywords of the Phase 4b signature
(`max_extracted_bytes`, `max_ratio`, `ratio_activation_threshold`, `max_entries`) are
**removed**; the limits travel in `config.extraction_limits` or the per-call `limits`
override (see the configuration requirement below).

The default policy is `ExtractionPolicy.STRICT` and the default overwrite behaviour is `OverwritePolicy.ERROR`.

#### Scenario: extract all members from an untrusted archive

- **WHEN** `archivey.extract("untrusted.zip", "/safe/output/")` is called with no other arguments
- **THEN** all members are extracted to `/safe/output/` under `ExtractionPolicy.STRICT` and `OverwritePolicy.ERROR`
- **AND** a `list[ExtractionResult]` describing each member's outcome is returned

#### Scenario: subset extraction goes through an open reader

- **WHEN** a caller wants only some members
- **THEN** they open the archive and call `reader.extract_all(dest, members=...)` rather than passing members to the top-level function (which would require reopening)

#### Scenario: one-shot extraction of a non-UTF-8-named archive

- **WHEN** `archivey.extract(src, dest, encoding="cp932")` is called on a TAR whose member names are CP932-encoded
- **THEN** the members land on disk under their correctly decoded names, identical to `open_archive(src, encoding="cp932")` + `extract_all(dest)`

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
        config: ArchiveyConfig | None = None,    # None = the reader's own config
        limits: ExtractionLimits | None = None,  # per-call bomb-limit override
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
`config` defaults to the config the reader was opened with; `limits` overrides its
`extraction_limits` for this call only (see the configuration requirement below).

#### Scenario: extract all via reader

- **WHEN** `reader.extract_all(dest)` is called
- **THEN** all members are extracted and a `list[ExtractionResult]` is returned, with the same safety guarantees as `archivey.extract()`

#### Scenario: extract a selected subset in one pass

- **WHEN** `reader.extract_all(dest, members=["a.txt", "b.txt"])` is called on a solid archive
- **THEN** only those members are extracted, in a single decompression pass over the archive

#### Scenario: single-file extraction via selector

- **WHEN** a caller wants just one file
- **THEN** they call `reader.extract_all(dest, members=[name])`; there is no separate single-member `extract()` method

## ADDED Requirements

### Requirement: Extraction reads limits and strictness from the configuration object

`archivey.extract()` and `ArchiveReader.extract_all()` SHALL accept
`config: ArchiveyConfig | None` (see `archive-reading`), whose `extraction_limits`
field (an `ExtractionLimits` of `max_extracted_bytes`, `max_ratio`,
`ratio_activation_threshold`, `max_entries` — defaults unchanged from the individual
requirements) supplies the decompression-bomb limits, **and** a per-call
`limits: ExtractionLimits | None` override. Precedence: per-call `limits` >
`config.extraction_limits` > library default. `extract_all()` SHALL default to
the config the reader was opened with; `archivey.extract()` SHALL default to the
library default config. Per-call operational parameters (`members`, `filter`,
`policy`, `overwrite`, `on_error`, `on_progress`, `password`) remain keyword
arguments and are not part of the config object.

`ExtractionLimits.UNLIMITED` SHALL be provided as a preset that disables all four
guards, for archives the caller explicitly trusts. Presets are not named by trust and
are independent of `ExtractionPolicy`: policy governs metadata/permission semantics,
limits govern resource bounds, and neither implies the other (the documented
trusted-archive recipe is the explicit pair `policy=ExtractionPolicy.TRUSTED,
limits=ExtractionLimits.UNLIMITED`).

The returned `list[ExtractionResult]` is accumulated unconditionally in v1: a
no-tracking mode would not bound memory on its own (readers cache the member list
internally), so it is deferred until a no-member-cache reader mode exists (see the
phase-5 design document).

#### Scenario: limits taken from the config

- **WHEN** `archivey.extract(src, dest, config=ArchiveyConfig(extraction_limits=ExtractionLimits(max_extracted_bytes=10 * 2**30)))` is called
- **THEN** the cumulative byte limit enforced is 10 GiB

#### Scenario: per-call limits override the config

- **WHEN** a reader opened with a custom `config` runs `extract_all(dest, limits=ExtractionLimits(max_extracted_bytes=50 * 2**20))`
- **THEN** the 50 MiB per-call cap governs that run, overriding `config.extraction_limits`; a later `extract_all()` without `limits` reverts to the reader's config

#### Scenario: UNLIMITED preset disables the guards

- **WHEN** `archivey.extract(src, dest, limits=ExtractionLimits.UNLIMITED)` is called on an archive that would trip the default byte or ratio limits
- **THEN** extraction completes with no bomb-guard error

#### Scenario: extract_all inherits the reader's config

- **WHEN** a reader opened with a custom `ArchiveyConfig` runs `extract_all(dest)` with no `config` argument
- **THEN** the reader's config (including its extraction limits) governs the run

### Requirement: Enforce Maximum Entry Count

The system SHALL track the number of archive members **written to disk** during a single
`extract()` / `extract_all()` call and SHALL raise `ExtractionError` when it exceeds
`max_entries`. This guards against an **entry-count / inode-exhaustion bomb** — an archive
packing an enormous number of tiny (often zero-byte) files or directories that overwhelms
the filesystem (inodes, per-directory entries, per-file syscall overhead) *without*
tripping `max_extracted_bytes` (there is little data) or the decompression ratio (each
entry compresses normally).

Only members that will actually be written count toward the limit: a member excluded by
the `members` selector or skipped by the user `filter` (returning `None`) or dropped by
the policy transform creates nothing on disk and SHALL NOT increment the counter. The
extraction coordinator calls `BombTracker.start_member()` only after the selector and
filter have accepted a member and immediately before writing begins. Every written member
type counts (FILE, DIR, SYMLINK, HARDLINK).

Like the cumulative `max_extracted_bytes` limit, this is a global resource guard, so
exceeding it halts extraction **even under `OnError.CONTINUE`** (continuing would defeat
the guard). The caller supplies the limit via `config.extraction_limits.max_entries` or
the per-call `limits` override (see the configuration requirement above). The default is
`1_048_576` (2²⁰) entries — generous enough for large legitimate archives (a Linux
source tarball or a `node_modules` bundle can hold hundreds of thousands of files) while
still bounding a pathological many-entries bomb. This limit is independent of the byte
and ratio guards; any of them may trip first.

#### Scenario: archive with too many written entries is rejected

- **WHEN** an archive containing more than `max_entries` members is extracted in full
- **THEN** `ExtractionError` is raised once the count of written members crosses the limit, and extraction halts even under `OnError.CONTINUE`

#### Scenario: caller overrides the entry-count limit

- **WHEN** `archivey.extract(src, dest, limits=ExtractionLimits(max_entries=100))` is called on an archive with more than 100 members
- **THEN** `ExtractionError` is raised after the 100th member is written

#### Scenario: selector and filter skips do not count

- **WHEN** `reader.extract_all(dest, members=["one.txt"], limits=ExtractionLimits(max_entries=1))` is called on an archive with millions of members
- **THEN** extraction completes without tripping the entry-count guard, because only the selected member is written

#### Scenario: entry count is independent of byte and ratio limits

- **WHEN** an archive of many tiny files stays well under `max_extracted_bytes` and never trips the decompression ratio but more than `max_entries` members are written
- **THEN** `ExtractionError` is still raised on the entry-count guard
