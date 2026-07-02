# archive-reading — Phase 5 deltas

## MODIFIED Requirements

### Requirement: Opening an archive for reading

The system SHALL expose a top-level `archivey.open_archive()` function that accepts a file path, `Path`, or binary stream and returns an `ArchiveReader`.

```python
archivey.open_archive(
    source: str | Path | BinaryIO | Sequence[str | Path | BinaryIO],
    *,
    format: ArchiveFormat | None = None,  # override detection
    streaming: bool = False,             # False = random access; True = forward-only one pass
    password: str | bytes | Sequence[str | bytes] | PasswordProvider | None = None,
    encoding: str | None = None,         # None = auto-detect member-name encoding
    config: ArchiveyConfig | None = None,  # None = the library default configuration
) -> ArchiveReader
```

The `format` parameter MAY be omitted; when omitted the library performs automatic
format detection. `encoding` defaults to `None`, meaning the library auto-detects the
encoding of member-name fields: it uses the **format's internal signal** when present
(e.g. the ZIP UTF-8 general-purpose-bit, RAR5 UTF-8 names, tar PAX UTF-8 records), and
otherwise detects from the raw name bytes. A caller MAY pass an explicit `encoding` as a
last-resort override when the format records none and detection is unreliable; the
verbatim bytes are always preserved in `ArchiveMember.raw_name` so names can be
re-decoded losslessly. `source` MAY also be an ordered sequence of files/streams that
together form a single multi-volume archive (see the multi-volume requirement below).
`password` accepts a single value, an ordered sequence of candidate values, or a
provider callable (see the password requirement below). `config` carries the library's
tuning/policy knobs (see the configuration requirement below); per-call operational
arguments remain keyword parameters and MUST NOT move into the config object. The
Phase 4 `strict_eof` keyword is removed — end-of-archive strictness lives at
`config.strict_archive_eof`.

#### Scenario: open with auto-detected format

- **WHEN** `archivey.open_archive("archive.tar.gz")` is called with no `format` override
- **THEN** the library detects the format from magic bytes and returns an `ArchiveReader` wrapping the appropriate backend

#### Scenario: open with explicit format override

- **WHEN** `archivey.open_archive(source, format=ArchiveFormat.ZIP)` is called
- **THEN** the library uses the specified format backend without running detection

#### Scenario: open with password

- **WHEN** `archivey.open_archive(source, password="secret")` is called
- **THEN** the returned `ArchiveReader` uses the provided password for encrypted members

## ADDED Requirements

### Requirement: Password candidates and provider

`password` SHALL accept, besides a single `str | bytes` value:

- an **ordered sequence** of candidate values, and/or
- a **provider callable** `PasswordProvider = Callable[[ArchiveMember | None], str | bytes | None]`.

For each encrypted unit (an encrypted member, a 7z folder, or an encrypted archive
header), the reader SHALL try, in order: the per-archive **known-good list** (passwords
that already succeeded during this open, most recent first), then the remaining sequence
candidates, then — when a provider is given — the provider, repeatedly, until it returns
`None`. The provider receives the `ArchiveMember` being decrypted so an interactive
caller can present which entry is being asked about, or `None` when decryption is
archive-level (a header-encrypted 7z/RAR5, where no member exists yet). Every password
that succeeds SHALL be added to the known-good list for the remainder of the operation,
so a provider is consulted once per *new* password rather than once per member, and a
single forward streaming pass stays viable on archives whose members use different
passwords. When all candidates are exhausted (or the provider returns `None`) for a unit
that needs one, the reader SHALL raise `EncryptionError`. There is no per-call password
parameter on `open()`/`read()` — the candidate model subsumes it.

#### Scenario: sequence of candidates across differently-encrypted members

- **WHEN** an archive whose members are encrypted with two different passwords is opened with `password=[pw_a, pw_b]` and iterated in one streaming pass
- **THEN** every member decrypts using whichever candidate matches its unit, and the pass completes without random access

#### Scenario: provider is consulted and its answer is reused

- **WHEN** a provider callable is given and a member needs a password not yet known
- **THEN** the provider is called with that `ArchiveMember`; a returned password that succeeds is added to the known-good list and later members encrypted with it do not trigger further provider calls

#### Scenario: provider gives up

- **WHEN** the provider returns `None` for a unit no known candidate decrypts
- **THEN** `EncryptionError` is raised for that unit

#### Scenario: header decryption passes None to the provider

- **WHEN** a header-encrypted archive is opened with only a provider and the header must be decrypted to list members
- **THEN** the provider is called with `None` (no member exists yet)

### Requirement: Explicit configuration object

The system SHALL define a frozen `ArchiveyConfig` dataclass carrying the library's
tuning/policy knobs, passed explicitly as `config=` to `open_archive()` and
`extract()` (`None` selects the immutable library default):

```python
@dataclass(frozen=True)
class ExtractionLimits:
    max_extracted_bytes: int = 2 * 2**30
    max_ratio: float = 1000.0
    ratio_activation_threshold: int = 5 * 2**20
    max_entries: int = 1_048_576

@dataclass(frozen=True)
class ArchiveyConfig:
    use_rapidgzip: AcceleratorMode = AcceleratorMode.AUTO
    use_indexed_bzip2: AcceleratorMode = AcceleratorMode.AUTO
    strict_archive_eof: bool = False
    extraction_limits: ExtractionLimits = ExtractionLimits()
```

A reader carries the config it was opened with; `extract_all()` uses the reader's
config unless the call overrides it. Configuration is **explicit only**: the library
SHALL NOT read ambient state (no context variables, no mutable global default) to
resolve configuration. Per-call operational arguments — `format`, `streaming`,
`password`, `encoding`, and extraction's `members`/`filter`/`policy`/`overwrite`/
`on_error`/`on_progress` — are keyword parameters and MUST NOT be absorbed into the
config object.

`strict_archive_eof` governs archive-level end-of-data verification (today: the TAR
two-block trailer check; extensible to other formats): `False` (default) emits a
`logging.WARNING` on a failed check, `True` raises `TruncatedError`. The check
necessarily runs only after a full pass reaches the archive's end.

#### Scenario: default configuration without a config argument

- **WHEN** `archivey.open_archive(source)` is called with no `config`
- **THEN** the library default `ArchiveyConfig()` applies (accelerators AUTO, `strict_archive_eof=False`, default limits)

#### Scenario: strict end-of-archive via config

- **WHEN** a truncated TAR (missing trailer) is fully read under `config=ArchiveyConfig(strict_archive_eof=True)`
- **THEN** `TruncatedError` is raised at the end of the pass; with the default config the same condition only logs a warning

#### Scenario: extraction limits travel in the config

- **WHEN** `archivey.extract(src, dest, config=ArchiveyConfig(extraction_limits=ExtractionLimits(max_ratio=100)))` runs
- **THEN** the 100:1 per-member ratio limit is enforced (see `safe-extraction`)

### Requirement: Collection form of MemberSelector

`MemberSelector` SHALL accept, besides a predicate, a `Collection[str | ArchiveMember]`,
normalized to a predicate at the API boundary:

- a `str` entry matches **every** member whose normalized name equals it — duplicate
  names all match (extraction of duplicate selected names keeps sequential
  last-wins-on-disk semantics);
- an `ArchiveMember` entry matches by **identity** (`archive_id` + `member_id`;
  members are unhashable, so normalization builds an id set, never a member set);
- string and member entries MAY be mixed in one collection.

#### Scenario: name entry selects all duplicates

- **WHEN** `stream_members(members=["a.txt"])` runs on an archive containing two members named `a.txt`
- **THEN** both are yielded, in archive order

#### Scenario: member entry selects by identity

- **WHEN** a specific `ArchiveMember` (one of two duplicates) is passed in the collection
- **THEN** only that member is selected, not its same-named sibling
