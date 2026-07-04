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

### Requirement: Transparent link following

The system SHALL transparently follow symlinks and hardlinks in `open()` and `read()`. If `member.type` is `SYMLINK` or `HARDLINK`, the call is redirected to the target member. This behavior is format-independent and is implemented once in the `ArchiveReader` ABC. The resolved target, when known, is also exposed as `member.link_target_member`.

**Hardlinks resolve positionally**: the target is the most recent occurrence of the
target name **strictly before** the link in archive order (this is the TAR model — every
real tar writer stores the data-bearing entry before the link entries that reference it,
because hardlinks are detected by inode during archiving; RAR5's redirect model is the
same). With duplicate names this matches what a sequential extraction would link
against on disk at the moment the link is written. An archive whose hardlink source
appears **only later** in archive order is malformed but tolerated: in random-access
mode resolution falls back to the later member (and extraction recovers it — see
`format-tar`'s orphan second pass); in streaming mode a forward-pointing hardlink
cannot be resolved in a single pass and fails per `OnError`.

**Symlinks resolve to the last occurrence overall** of the target name (random-access
mode): a symlink is a *name*, resolved at use time, and the final on-disk state of a
duplicated name after sequential extraction is its last occurrence. In streaming mode a
symlink can only resolve to the latest occurrence seen so far; a forward-pointing
symlink stays unresolved (`link_target_member` is `None`). The two modes SHALL agree on
hardlink resolution for the same archive; the symlink forward-visibility difference is
inherent to a single pass and is documented.

**Target-name resolution.** The stored target string is resolved to an archive-namespace
member name before lookup, because the two link kinds store targets in different
namespaces: a **hardlink** target is archive-relative from the root (the linkname is the
source member's own stored path) and is normalized as-is, while a **symlink** target is
a filesystem path relative to the link's *own directory* (`dir/link -> file` means
`dir/file`) and is joined to that directory first. An absolute symlink target, or one
that `..`-escapes the archive root, cannot name a member — it stays unresolved
(`link_target_member` is `None`; opening through it raises `LinkTargetNotFoundError`).
Directory members carry a trailing `/` in their normalized names, so target lookup tries
both the bare and the `/`-suffixed form.

If the link target is not present in the archive, `LinkTargetNotFoundError` (a
`ReadError`/member error) SHALL be raised. Chains SHALL be followed recursively with
**cycle detection** — the set of **member ids** already visited on the current chain is
tracked, and if a member is revisited the library raises a `ReadError` whose message
reports the cycle. Tracking is by member id, never by name: a chain passing through two
*distinct* members that share a name is not a cycle, and a name-based visited set would
falsely report one. There is no fixed depth limit; an acyclic chain of any length
resolves, and only an actual cycle (or a missing target) fails.

```python
# ABC implementation (ARCHITECTURE.md §2.3)
def open(self, member: str | ArchiveMember, _seen: frozenset[int] = frozenset()) -> BinaryIO:
    if isinstance(member, str):
        found = self.get(member)  # name lookup — there is no __getitem__ on the reader
        if found is None:
            raise KeyError(f"Member {member!r} not found")
        member = found
    if member.type in (MemberType.SYMLINK, MemberType.HARDLINK) and member.link_target:
        if member.member_id in _seen:
            raise ReadError(f"Link cycle detected at '{member.name}'")
        target = member.link_target_member or self.get(member.link_target)
        if target is None:
            raise LinkTargetNotFoundError(f"Link target '{member.link_target}' not in archive")
        return self.open(target, _seen=_seen | {member.member_id})
    return self._open_member(member)
```

This does not rely on format-level link resolution; format-level resolution (e.g. a RAR5 reader following hardlinks internally) happens at a lower level.

#### Scenario: reading via a symlink member

- **WHEN** `ar.read("data/latest")` is called and `"data/latest"` is a `SYMLINK` pointing to `"data/v1.0/report.txt"`
- **THEN** the content of `"data/v1.0/report.txt"` is returned transparently

#### Scenario: relative symlink target resolves against the link's directory

- **WHEN** member `"dir/link"` is a `SYMLINK` whose stored target is `"file"` and the archive contains both `"dir/file"` and a root-level `"file"`
- **THEN** `ar.read("dir/link")` returns the content of `"dir/file"` (not the root-level `"file"`), and `member.link_target_member` points at `"dir/file"`

#### Scenario: absolute symlink target stays unresolved

- **WHEN** a `SYMLINK` member's stored target is absolute (e.g. `"/etc/passwd"`)
- **THEN** `member.link_target_member` is `None` and `ar.open()` on it raises `LinkTargetNotFoundError`

#### Scenario: hardlink resolves to an earlier member

- **WHEN** a `HARDLINK` member is read and its target is an earlier member in archive order
- **THEN** the earlier member's content is returned, resolved in a single forward pass

#### Scenario: duplicate names — hardlink links to the latest earlier occurrence

- **WHEN** an archive contains `A.txt` (content1), then a `HARDLINK` `L → A.txt`, then a second `A.txt` (content2)
- **THEN** `ar.read("L")` returns content1 in **both** access modes, and extraction links `L` against the content1 inode — matching what a sequential extraction leaves on disk

#### Scenario: duplicate names — symlink resolves to the last occurrence

- **WHEN** an archive contains `A.txt` (content1), a `SYMLINK` `S → A.txt`, then a second `A.txt` (content2), opened in random-access mode
- **THEN** `S.link_target_member` points at the second `A.txt` (the final on-disk state of that name)

#### Scenario: hardlink source only appears later (malformed archive)

- **WHEN** a `HARDLINK` precedes its source in archive order and the archive is opened in random-access mode
- **THEN** the link resolves to the later member and `ar.read()` on it returns that content; in streaming mode the same link fails per `OnError` (a single pass cannot see forward)

#### Scenario: link target not in archive

- **WHEN** `ar.open(link_member)` is called and `link_member.link_target` is absent from the archive
- **THEN** `LinkTargetNotFoundError` is raised

#### Scenario: link cycle detected

- **WHEN** following a link chain revisits a member (by member id) already seen on that chain
- **THEN** `ReadError` is raised with a message reporting the cycle (no fixed depth limit is used; only genuine cycles fail)

#### Scenario: same-named members on one chain are not a false cycle

- **WHEN** a link chain passes through two distinct members that share a normalized name
- **THEN** the chain resolves normally — cycle detection tracks member ids, so the shared name does not trigger a spurious cycle error

## ADDED Requirements

### Requirement: Password candidates and provider

`password` SHALL accept, besides a single `str | bytes` value:

- an **ordered sequence** of candidate values, and/or
- a **provider callable** `PasswordProvider = Callable[[PasswordRequest], str | bytes | None]`, where

```python
@dataclass(frozen=True)
class PasswordRequest:
    member: ArchiveMember | None  # the member being decrypted; None for archive-level
                                  # (header) decryption, where no member exists yet
    attempt: int                  # 1 on the first ask for this unit; increments when a
                                  # previously returned password failed for it
```

For each encrypted unit (an encrypted member, a 7z folder, or an encrypted archive
header), the reader SHALL try, in order: the per-archive **known-good list** (passwords
that already succeeded during this open, most recent first), then the remaining sequence
candidates, then — when a provider is given — the provider, repeatedly, until it returns
`None`. The provider receives a `PasswordRequest` carrying the `ArchiveMember` being
decrypted (so an interactive caller can present which entry is being asked about, or
`None` for archive-level decryption — a header-encrypted 7z/RAR5, where no member exists
yet) and the `attempt` count for the unit (so a retry after a wrong password is
distinguishable from a first ask). The context object exists so future fields (e.g. the
prior error) can be added without breaking provider implementations — a bare callable
parameter could not be widened compatibly. Every password that succeeds SHALL be added
to the known-good list for the remainder of the operation, so a provider is consulted
once per *new* password rather than once per member, and a single forward streaming pass
stays viable on archives whose members use different passwords. When all candidates are
exhausted (or the provider returns `None`) for a unit that needs one, the reader SHALL
raise `EncryptionError`. There is no per-call password parameter on `open()`/`read()` —
the candidate model subsumes it.

#### Scenario: sequence of candidates across differently-encrypted members

- **WHEN** an archive whose members are encrypted with two different passwords is opened with `password=[pw_a, pw_b]` and iterated in one streaming pass
- **THEN** every member decrypts using whichever candidate matches its unit, and the pass completes without random access

#### Scenario: provider is consulted and its answer is reused

- **WHEN** a provider callable is given and a member needs a password not yet known
- **THEN** the provider is called with a `PasswordRequest` carrying that `ArchiveMember`; a returned password that succeeds is added to the known-good list and later members encrypted with it do not trigger further provider calls

#### Scenario: provider sees the retry count

- **WHEN** a provider's returned password fails to decrypt the unit and the provider is consulted again
- **THEN** the new `PasswordRequest` carries an incremented `attempt`, so an interactive caller can display "wrong password, try again"

#### Scenario: provider gives up

- **WHEN** the provider returns `None` for a unit no known candidate decrypts
- **THEN** `EncryptionError` is raised for that unit

#### Scenario: header decryption passes a memberless request

- **WHEN** a header-encrypted archive is opened with only a provider and the header must be decrypted to list members
- **THEN** the provider is called with a `PasswordRequest` whose `member` is `None` (no member exists yet)

### Requirement: Explicit configuration object

The system SHALL define a frozen `ArchiveyConfig` dataclass carrying the library's
tuning/policy knobs, passed explicitly as `config=` to `open_archive()` and
`extract()` (`None` selects the immutable library default):

```python
@dataclass(frozen=True)
class ExtractionLimits:
    # None disables that guard; the UNLIMITED preset is all-None.
    max_extracted_bytes: int | None = 2 * 2**30
    max_ratio: float | None = 1000.0
    ratio_activation_threshold: int = 5 * 2**20
    max_entries: int | None = 1_048_576

    UNLIMITED: ClassVar["ExtractionLimits"]  # every guard disabled (trusted archives)

@dataclass(frozen=True)
class ArchiveyConfig:
    use_rapidgzip: AcceleratorMode = AcceleratorMode.AUTO
    use_indexed_bzip2: AcceleratorMode = AcceleratorMode.AUTO
    strict_archive_eof: bool = False
    extraction_limits: ExtractionLimits = ExtractionLimits()
```

A reader carries the config it was opened with; `extract_all()` uses the reader's
config unless the call overrides it. The extraction limits are additionally overridable
per call via `limits: ExtractionLimits | None` on `extract()`/`extract_all()`
(precedence: per-call `limits` > `config.extraction_limits` > library default; the
`ExtractionLimits.UNLIMITED` preset disables all four guards — see `safe-extraction`).
Configuration is **explicit only**: the library
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
