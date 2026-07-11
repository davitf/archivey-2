# archive-reading — diagnostic-aware public API and lifecycles

## MODIFIED Requirements

### Requirement: Opening an archive for reading

The system SHALL expose:

```python
archivey.open_archive(
    source: str | Path | BinaryIO | Sequence[str | Path | BinaryIO],
    *,
    format: ArchiveFormat | None = None,
    streaming: bool = False,
    password: PasswordInput = None,
    encoding: str | None = None,
    config: ArchiveyConfig | None = None,
) -> ArchiveReader
```

`source`, multi-volume ordering, `streaming`, password candidates/providers, encoding,
configuration precedence, and backend selection retain their existing contracts.
`format=None` performs automatic detection; an explicit format bypasses detection.

The implementation SHALL create the prospective reader's one collector, budget, and
initial operation watermark before detection or backend open. Automatic detection SHALL
receive that collector. On successful open, the returned reader assumes ownership of the
same collector; opening SHALL NOT seed, merge, replay, or copy detection events into a
second collector. An occurrence retained during detection therefore consumes exactly one
aggregate budget slot and keeps one occurrence id/order position in the reader lifetime.
If detection/open raises, no reader is returned and the temporary collector is discarded
after the exception propagates.

#### Scenario: open with automatic detection transfers ownership

- **WHEN** `open_archive()` automatically detects a format and successfully builds its reader
- **THEN** the reader owns the exact collector used during detection, including its counters and retained entries, without duplicated references

#### Scenario: explicit format has no detection events

- **WHEN** `open_archive(source, format=ArchiveFormat.ZIP)` succeeds
- **THEN** one collector still covers open and later work, but detection is not run and no detection diagnostic is recorded

### Requirement: Reading member data

The public `ArchiveStream` SHALL implement the `BinaryIO` contract, remain caller-closed,
and additionally expose an immutable operation-filtered diagnostic snapshot:

```python
class ArchiveStream(BinaryIO):
    @property
    def diagnostics(self) -> DiagnosticSummary: ...

def read(self, member: str | ArchiveMember) -> bytes: ...
def open(self, member: str | ArchiveMember) -> ArchiveStream: ...
```

Both methods accept a name or an `ArchiveMember` yielded by this reader. An unknown name
raises `KeyError`; a foreign member raises `ValueError`. `read()` materializes the entire
payload without extraction bomb checks and is intended for small trusted members.
`open()` streams in bounded chunks. Full reads verify any supported member digest;
streaming verification raises `CorruptionError` on the terminal read only after all valid
chunks have been delivered, while `read()` raises without returning bytes.

A reader-owned stream SHALL use an operation token/watermark over the reader's collector.
It SHALL NOT own or retain a second copy of its diagnostics. A standalone
`ArchiveStream` not owned by a reader SHALL own one stream-lifetime collector.

#### Scenario: opening a member returns the diagnostic stream type

- **WHEN** `reader.open("data.bin")` succeeds
- **THEN** it returns an `ArchiveStream` usable as `BinaryIO`, and `stream.diagnostics` reports only that stream operation's events

#### Scenario: stream and reader do not duplicate retention

- **WHEN** a reader-owned stream emits a rewind diagnostic
- **THEN** stream and reader snapshots can both expose it while the shared collector retains and charges it only once

### Requirement: Bounded-memory sequential streaming via stream_members

The system SHALL provide:

```python
def stream_members(
    self,
    members: MemberSelector | None = None,
) -> Iterator[tuple[ArchiveMember, ArchiveStream | None]]: ...
```

It yields original caller-read-only members in archive order and an `ArchiveStream` for
file data (`None` for non-files), with peak memory bounded by decompressor state and one
in-flight chunk rather than a whole solid block. A yielded stream is valid only until the
iterator advances. Streams open lazily; selector-excluded and unread members are not
opened. `members` keeps the existing name/member collection or predicate semantics.
There is no transform `MemberFilter` on this pure generator because late-bound member
metadata must continue to update the original member in place.

Each yielded stream is an operation-filtered view over the reader's single collector and
budget; advancing the iterator does not create a new diagnostic collector or aggregate
copy.

#### Scenario: sequential stream has public diagnostics

- **WHEN** a yielded file stream encounters a diagnostic before the iterator advances
- **THEN** the stream snapshot and cumulative reader snapshot expose that occurrence from one retained aggregate entry

#### Scenario: skipped member data is never opened

- **WHEN** a selector excludes a member or the caller does not read its yielded stream
- **THEN** that member's data is not opened/decompressed and no data-path diagnostic is produced for it

### Requirement: Transparent link following

`open()` and `read()` SHALL transparently follow symlinks and hardlinks through the shared
reader implementation, and `open()` SHALL preserve its public `ArchiveStream` return type
after following:

```python
def open(self, member: str | ArchiveMember) -> ArchiveStream: ...
```

Hardlinks resolve to the most recent matching target strictly before the link, with the
existing random-access fallback for a malformed later-only source; streaming mode cannot
resolve that forward target. Random-access symlinks resolve to the last matching target
overall, while streaming symlinks can resolve only to a target already seen. Hardlink
targets are archive-root relative; symlink targets are resolved relative to the link's
directory, and absolute/root-escaping targets do not resolve. Bare and trailing-slash
directory forms are both considered.

The reader SHALL follow chains recursively, detect actual cycles by member id rather than
name, and impose no arbitrary depth limit. A missing/unresolvable target raises
`LinkTargetNotFoundError`; a cycle raises `ReadError`. A stream reached through a link
uses the same operation collector/token as the initiating `open()` call rather than
creating or retaining another diagnostic operation.

#### Scenario: linked open preserves stream diagnostics

- **WHEN** `reader.open(link_member)` follows a valid chain to file data
- **THEN** it returns one `ArchiveStream` whose operation-filtered diagnostics cover work performed while following and reading that open operation

### Requirement: Explicit configuration object

The system SHALL define these complete frozen configuration schemas:

```python
@dataclass(frozen=True)
class ExtractionLimits:
    max_extracted_bytes: int | None = 2 * 2**30
    max_ratio: float | None = 1000.0
    ratio_activation_threshold: int = 5 * 2**20
    max_entries: int | None = 1_048_576

    UNLIMITED: ClassVar["ExtractionLimits"]

@dataclass(frozen=True)
class ArchiveyConfig:
    use_rapidgzip: AcceleratorMode = AcceleratorMode.AUTO
    use_indexed_bzip2: AcceleratorMode = AcceleratorMode.AUTO
    strict_archive_eof: bool = False
    extraction_limits: ExtractionLimits = ExtractionLimits()
    diagnostic_policy: DiagnosticPolicy = DiagnosticPolicy()
    max_retained_diagnostic_references: int = 256
    on_diagnostic: Callable[[Diagnostic], None] | None = None
```

`max_retained_diagnostic_references` SHALL be non-negative. Policy/default/override
mappings and both config dataclasses SHALL be defensively immutable. `config=None`
selects the immutable library default. Configuration is explicit only; Archivey SHALL
read no mutable global/context-local diagnostic policy or callback.

A reader carries its open config. A later `extract_all(config=...)` MAY override policy,
callback, strictness, accelerators, and limits for new work, but its
`max_retained_diagnostic_references` field SHALL NOT replace, reset, lower, or enlarge the
existing reader collector's budget. Per-call `limits` still takes precedence over
`config.extraction_limits`, then the reader/library default. Existing per-call operational
arguments remain outside `ArchiveyConfig`.

`strict_archive_eof=False` follows ordinary diagnostic policy for a failed EOF check.
`True` forces `TruncatedError` after the ordered diagnostic counting/delivery rules
specified by `error-handling`.

Callbacks run synchronously after count/retention/logging updates and without any
collector, reader, stream, backend, or registry lock. Snapshot reads from a callback are
allowed. Starting another operation on the same currently emitting reader/stream SHALL
raise `UnsupportedOperationError`; operating on a different reader is allowed.

#### Scenario: complete default configuration

- **WHEN** `ArchiveyConfig()` is used
- **THEN** accelerators are AUTO, EOF strictness is false, extraction limits are the documented defaults, diagnostics default to COLLECT, the budget is 256, and no callback is installed

#### Scenario: extraction override cannot replace lifetime budget

- **WHEN** a reader opened with budget 10 calls `extract_all(config=ArchiveyConfig(max_retained_diagnostic_references=1000))`
- **THEN** new policy/callback settings may apply, but all reader-owned diagnostics remain subject to the original budget 10

## ADDED Requirements

### Requirement: Reader-lifetime cumulative diagnostic snapshots

Every successfully created `ArchiveReader` SHALL own a diagnostic collector for its
lifetime and expose:

```python
@property
def diagnostics(self) -> DiagnosticSummary: ...
```

Each access SHALL return a fresh immutable cumulative snapshot. Exact counts SHALL include
automatic-detection occurrences that led to the reader plus every open/list/read/stream/
extract occurrence subsequently owned by it, including events whose detail could not be
retained. Previously returned snapshots SHALL not change.

A stream returned by a reader SHALL expose an operation-filtered `diagnostics` snapshot
over the same collector. It SHALL not separately retain aggregate copies merely to serve
both stream and reader views.

#### Scenario: reader snapshot grows over its lifetime

- **WHEN** a reader is opened after a detection conflict, then listing emits a scan diagnostic and a member stream emits a rewind diagnostic
- **THEN** a later `reader.diagnostics` has exact cumulative counts for all three in emission order, while an earlier snapshot remains unchanged

#### Scenario: stream view is a filtered reader view

- **WHEN** two member streams emit different diagnostics
- **THEN** each stream's snapshot includes only its operation's events and the reader snapshot includes both, without separately retained aggregate copies

#### Scenario: callback may query but not re-enter

- **WHEN** `on_diagnostic` reads `reader.diagnostics` and then attempts `reader.read(...)` on the same emitting reader
- **THEN** the snapshot read succeeds and includes the current event, while the operational reentry raises `UnsupportedOperationError`
