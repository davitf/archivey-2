# Archive Reading

## Purpose

Uniform interface for opening and reading archives. `ArchiveReader` presents ZIP,
TAR, RAR, 7z, ISO, directories, and single-file compressed streams with consistent
metadata, iteration, and data-access semantics.

This spec is the **caller-facing** `ArchiveReader` surface. Cross-cutting
machinery lives elsewhere:

| Concern | Spec |
| --- | --- |
| `streaming` legality × method table, cost receipts | `access-mode-and-cost` |
| Diagnostic values, retention budget, watermarks | `diagnostics` |
| Detection → reader diagnostic handoff | `format-detection` |
| `MemberStreams.CONCURRENT`, ownership, free-threaded opens | `reader-concurrency` |
| Extraction filters / bomb limits | `safe-extraction` |

## Requirements

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
    member_streams: MemberStreams = MemberStreams(0),
) -> ArchiveReader
```

`source`, multi-volume ordering, `streaming`, password candidates/providers,
encoding, configuration precedence, and backend selection retain their existing
contracts. `format=None` auto-detects; an explicit format bypasses detection.

**Diagnostics at open (observable):** On success, advisory events from automatic
detection (if any) appear in this reader's cumulative `diagnostics` for its
lifetime and are not duplicated. Explicit `format=` skips detection, so open
adds no detection diagnostics. If open raises, no reader is returned.

Handoff mechanics (one shared collector/budget, no copy/re-seed): see
`format-detection` and `diagnostics`.

#### Scenario: open matrix

| Case | Expected |
| --- | --- |
| Auto-detect succeeds | Detection events visible on `reader.diagnostics`; not duplicated |
| `format=ArchiveFormat.ZIP` succeeds | No detection diagnostics from open |
| Open raises | No reader returned |
| `password="secret"` | Returned reader uses that password for encrypted members |

### Requirement: Declared member-stream capabilities

`open_archive()` SHALL accept `member_streams: MemberStreams` (flags, default none):

- `MemberStreams.CONCURRENT` — any number of member streams may be open simultaneously
  (full contract: `reader-concurrency`)
- `MemberStreams.SEEKABLE` — seekable where the backend can provide it

**Default (no flags), every format including directory:** at most one live member
data stream per reader; streams are forward-only. "Live" spans `open()` →
stream `close()`/context exit (not EOF, not GC). A second overlapping `open()`
SHALL raise `ConcurrentAccessError` at the later call and leave the first stream
untouched/readable — never silently close/invalidate a held stream. Every member
stream (random `open()` and `stream_members()` yields) SHALL report
`seekable() is False`; `seek()` SHALL raise `io.UnsupportedOperation`; `tell()`
SHALL work. Sequential `open → read → close → open next` is unaffected.

`open_archive()` SHALL capture the caller stack once; `ConcurrentAccessError`
SHALL include that `file:line`. Full stack is retained on the reader for
diagnostics (no config knob). Capabilities are per-archive intent only — no
`ArchiveyConfig` equivalent, no per-`open()` flag. Access cost never determines
legality; the cost receipt describes expense.

**Internal ops exempt:** `extract_all()` (incl. hardlink recovery), symlink-target
reads, password confirmation, and other library-internal opens run under internal
scopes and need no declared capability.

**Out of gate scope:** non-overlapping open *order* on solid archives (each
re-decode from block start) stays under `AccessCost` / `solid_block_count` /
`stream_members()` steer. Docs for `member_streams` SHALL state this.

#### Scenario: capability gate matrix

| Case | Expected |
| --- | --- |
| Overlapping second `open()` without `CONCURRENT` (ZIP/TAR/ISO/single-file/dir) | `ConcurrentAccessError` at later `open()` with open_archive `file:line`; first stream remains readable |
| Non-overlapping open/read/close loop, no flags | All opens succeed |
| Stream without `SEEKABLE` (incl. real directory file) | `seekable()` false; `seek()` → `io.UnsupportedOperation`; `tell()` + forward reads OK |
| Same member with `SEEKABLE` | Seekable where backend provides it; loud-slow-rewind rule for non-accelerated path |
| `extract_all()` with no flags | Completes; internal opens ungated |

### Requirement: Multi-volume and multi-source input

`open_archive()` SHALL accept a multi-volume archive either way and present one
logical `ArchiveReader`:

- **Single path in a volume set** (e.g. `name.7z.001`, `name.part1.rar`,
  `name.rar` + `name.r00`…): discover siblings in natural order
- **Explicit ordered `source` sequence**: use that order as volumes

Joining is format-specific (`format-7z` / `format-rar`): 7z concatenates a split
byte stream; RAR parses self-describing volumes in order and stitches
boundary-spanning members. Incomplete/out-of-order sets SHALL raise
`UnsupportedFeatureError` or a truncated/corrupt error — never a partial result.

#### Scenario: volume input matrix

| Case | Expected |
| --- | --- |
| `open_archive("disc.7z.001")` with siblings present | One reader for the whole set |
| `open_archive([vol1, vol2, vol3])` in order | One archive in that order |
| Missing volume | Raise at open or first dependent read; no partial member list |

### Requirement: Archive metadata access

The system SHALL expose read-only:

```python
@property
def info(self) -> ArchiveInfo: ...

@property
def cost(self) -> CostReceipt: ...

@property
def format(self) -> ArchiveFormat: ...
```

`info` is format/version/solid/member count/comment/encryption/multivolume/cost.
`cost` is listing/access/stream capability/solid block count. `format` is the
`(container, stream)` pair.

#### Scenario: metadata after open

| Case | Expected |
| --- | --- |
| Successful open | `ar.info`, `ar.cost`, `ar.format` available immediately without extra I/O |

### Requirement: Sequential in-order iteration

```python
def __iter__(self) -> Iterator[ArchiveMember]: ...     # sequential, in-order
def members(self) -> list[ArchiveMember]: ...          # materialize (RA only)
def members_report(self) -> MemberListReport: ...       # members + terminal listing error
def scan_members(self) -> list[ArchiveMember]: ...      # fully-resolved, either mode
def members_report_if_available(self) -> MemberListReport | None: ...  # index peek
```

`__iter__` MUST yield in archive order without loading all members. In
**random-access**, `members()` MAY scan formats without a central directory; after
materialization, later `__iter__` calls MUST use the cache. In **streaming**, no
cache-replay: `__iter__` is part of the single forward pass (see
`access-mode-and-cost`).

`scan_members()` SHALL return the fully-resolved list (`link_target_member` filled
where the target exists, incl. forward-pointing and last-wins symlinks). In RA it
equals `members()`. On `streaming=True` it returns the cache if the pass completed,
else **finishes that pass** (from start or draining an interrupted one), resolves
links, and returns the list. It is the only method permitted after an iteration
method has started; running it consumes/finishes the pass.

A live forward pass leaves forward-pointing symlinks unresolved at yield time.
Completing a pass via `__iter__`, `stream_members`, `extract_all`, or
`scan_members` SHALL finalize the cache in place on already-yielded objects and
make `members_report_if_available()` return it. An abandoned pass (early `break`, no
`scan_members()`) SHALL NOT finalize.

No `__len__` / `__getitem__` (not a collection; protocols are probed implicitly —
`list(reader)` probes `__len__` for preallocation). `len(ar)` → Python `TypeError`
in every mode; use `len(ar.members())`, `ar.info.member_count`, or count while
iterating. `list(ar)` just iterates.

`members_report_if_available()` is index-only: returns a `MemberListReport` only
when available without scanning or reading member data, else `None`. Never scans
or starts the forward pass. Returned members may have unresolved links when
targets live in member data (see `access-mode-and-cost`).

With `streaming=True`, `members()` / `get()` / `open()` / `read()` SHALL raise
`UnsupportedOperationError` uniformly. Only one forward pass
(`__iter__`/`stream_members` or one `extract_all`) is allowed, with
`scan_members()` to finish/return it and `members_report_if_available()` anytime.
Canonical access-mode × method table: `access-mode-and-cost`.

#### Scenario: iteration / access-mode matrix

| Method / action | `streaming=False` | `streaming=True` |
| --- | --- | --- |
| `__iter__` | Yields in order; after first materialization, from cache | Single-use forward pass; second `__iter__`/`stream_members`/`extract_all` → `UnsupportedOperationError` |
| `members()` | Full scan if needed; returns list | `UnsupportedOperationError` |
| `scan_members()` | Same fully-resolved list as `members()`; reader stays RA-usable | Finishes/drains pass; returns fully-resolved list (incl. forward symlinks); pass consumed |
| `scan_members()` after early `break` | n/a | Drains remainder; returns complete fully-resolved list |
| `members_report_if_available()` after completed pass | Report if indexed/cached | Fully-resolved report (not `None`); forward-link finalization visible on yielded objects |
| `members_report_if_available()` after abandoned pass | — | `None` |
| `len(ar)` | `TypeError` | `TypeError` |
| `list(ar)` | Iterates | Iterates (consumes the single pass) |

### Requirement: Listing resource limits

The system SHALL define frozen `ListingLimits` and apply them from the reader's
open `ArchiveyConfig.listing_limits` when registering members into a
materialized or resolved member list (`members()`, `scan_members()`, and any
path that materializes via `_get_members_registered` / equivalent). There is no
per-call listing-limits override.

```python
@dataclass(frozen=True)
class ListingLimits:
    max_members: int | None = 1_048_576
    max_metadata_bytes: int | None = 64 * 2**20  # 64 MiB
    UNLIMITED: ClassVar["ListingLimits"]
```

`None` on a field disables that guard. `ListingLimits.UNLIMITED` disables both.
Crossing either guard SHALL raise `ResourceLimitError` naming the knob and
limit. Format-local parser bounds (e.g. 7z header-size checks, RAR member-count
ceilings) MAY still raise at parse/open for nonsensical or hostile headers and
are complementary, not a substitute. Indexed formats that build a member table
during `open_archive()` MAY allocate up to those parser ceilings before spine
`ListingLimits` are evaluated on materialization (`members()` / extract-prep).

**Unguarded by design:** `stream_members()` / forward-only iteration MUST NOT
enforce `ListingLimits` (O(1) escape hatch). Callers that need a full resolved
list use `members()` / `scan_members()` and accept the caps.

#### Scenario: listing-limits matrix

| Case | Expected |
| --- | --- |
| Default config, archive with ≤1_048_576 members and metadata under 64 MiB | `members()` / `scan_members()` succeed |
| Registered member count would exceed `max_members` | `ResourceLimitError` before/at that registration; no full cache published |
| Cumulative retained metadata would exceed `max_metadata_bytes` | `ResourceLimitError` naming `max_metadata_bytes` |
| `ListingLimits.UNLIMITED` | Count and metadata guards disabled |
| `stream_members()` over an archive that would fail `members()` under defaults | Iteration proceeds without listing-limit errors |
| `extract_all` path that materializes members first | Same listing caps as `members()` before extraction bomb guards |

### Requirement: Listing metadata-byte accounting

The system SHALL measure `max_metadata_bytes` as a **safety-oriented weight** of
retained string/bytes fields accumulated as members are registered, plus
archive-level `ArchiveInfo.comment` once when known. Exact UTF-8 encoding of
every field is not required — the cap exists to bound metadata bombs, not to
mirror an allocator — but the weight MUST NOT under-count UTF-8 size:

- `str` fields `name`, `comment`, `link_target`, `uname`, `gname`: a cheap
  upper bound on UTF-8 length — `len(s)` when `s` is ASCII, otherwise
  `4 * len(s)` (UTF-8 is at most 4 bytes per code point). Implementations MAY
  use a stricter exact encode; they MUST NOT use a measure that can be smaller
  than UTF-8 (plain `len(s)` on non-ASCII would under-count a Unicode name bomb).
- `raw_name`: `len(raw_name)` when not `None` (stored archive bytes; already exact)
- `extra`: lengths of `str` / `bytes` values under the same rules; for a one-level
  `dict` value, nested `str` / `bytes` values only
- Exclude: `_raw`, `hashes`, diagnostics, Python object overhead

#### Scenario: metadata accounting matrix

| Case | Expected |
| --- | --- |
| Member with long `name` + `raw_name` | Both weights count |
| Huge `ArchiveInfo.comment` alone | Counts toward the budget once |
| `extra` holds opaque non-str/bytes object | Not counted |
| ASCII-only name | Weight equals `len(name)` (exact UTF-8) |
| Non-ASCII / surrogateescape name | Weight ≥ UTF-8-with-surrogateescape byte length (upper-bound OK) |

### Requirement: Name lookup and member identity

No `__getitem__` (duplicates break mapping; dunders are probed implicitly).
`open()`/`read()` accept a name and raise `KeyError` when absent.

```python
def get(self, name: str, default=None) -> ArchiveMember | None: ...
def __contains__(self, member: ArchiveMember) -> bool: ...  # identity, O(1), any mode
```

`get()` looks up by normalized name; duplicates → **last** (sequential extraction
winner). On `streaming=True` SHALL raise `UnsupportedOperationError` regardless of
loaded index. For a no-scan peek use `members_report_if_available()`.

`member in reader` is identity membership (yielded by this reader), O(1), any mode.
Non-`ArchiveMember` (notably a name string) SHALL raise `TypeError` pointing to
`get()`. `__contains__` MUST exist — without it, `in` falls back to `__iter__` and
would consume a streaming pass.

#### Scenario: lookup / membership matrix

| Case | Expected |
| --- | --- |
| `get` existing name | That `ArchiveMember` |
| `get` missing | `default` / `None`; `open`/`read` of missing name → `KeyError` |
| `get` on `streaming=True` | `UnsupportedOperationError` |
| `member in ar` (yielded by `ar`) | `True`; foreign member → `False`; no scan |
| `"file.txt" in ar` | `TypeError` → use `get()`; never iterate |

### Requirement: Reading member data

`ArchiveStream` SHALL implement `BinaryIO`, remain caller-closed, and expose an
immutable operation-filtered diagnostic snapshot:

```python
class ArchiveStream(BinaryIO):
    @property
    def diagnostics(self) -> DiagnosticSummary: ...

def read(self, member: str | ArchiveMember) -> bytes: ...
def open(self, member: str | ArchiveMember) -> ArchiveStream: ...
```

Unknown name → `KeyError`; foreign `ArchiveMember` → `ValueError`. `read()`
materializes the full payload without extraction bomb checks (small trusted
members). `open()` streams in bounded chunks. Full reads verify supported digests;
streaming verification raises `CorruptionError` only on the terminal read after
valid chunks; `read()` raises without returning bytes.

After symlink/hardlink following, if the **resolved** member is
`DIRECTORY`, `ANTI`, or `OTHER`, `open()` / `read()` SHALL raise
`ArchiveyUsageError`. They MUST NOT return empty bytes, and MUST NOT leak raw
`IsADirectoryError` or format `CorruptionError` for directory paths. A link whose
target is missing SHALL still raise `LinkTargetNotFoundError` (`ArchiveyError`).

**Diagnostics (observable):** A reader-owned stream's `diagnostics` shows only
that open operation's events; the same events also appear on the reader's
cumulative snapshot without being retained twice. A standalone `ArchiveStream`
(not owned by a reader) has its own lifetime summary. Retention/budget rules:
`diagnostics`.

#### Scenario: read / open matrix

| Case | Expected |
| --- | --- |
| `open("data.bin")` succeeds | `ArchiveStream` as `BinaryIO`; `stream.diagnostics` = that operation only |
| Reader-owned stream emits rewind diagnostic | Visible on stream and reader snapshots; retained once |
| `read("readme.txt")` | Full uncompressed `bytes` |
| `open(member)` from a different reader | `ValueError` |
| `open`/`read` directory (ZIP/TAR/ISO/directory/7z) | `ArchiveyUsageError` |
| `open`/`read` `MemberType.ANTI` or `OTHER` | `ArchiveyUsageError` |
| Symlink resolves to a file | Follow succeeds; returns file stream/bytes |
| Symlink target missing in archive | `LinkTargetNotFoundError` |

### Requirement: Non-file stream_members yield None

`stream_members` SHALL pair every non-file member (`DIRECTORY`, `SYMLINK`,
`HARDLINK`, `OTHER`, `ANTI`) with `stream is None` (no empty `ArchiveStream`).

#### Scenario: non-file stream matrix

| Case | Expected |
| --- | --- |
| Directory member | Stream `None` |
| `MemberType.ANTI` | Stream `None` |

### Requirement: Bounded-memory sequential streaming via stream_members

```python
def stream_members(
    self,
    members: MemberSelector | None = None,
) -> Iterator[tuple[ArchiveMember, ArchiveStream | None]]: ...
```

Yields `(member, stream)` in archive order with bounded memory. Solid blocks
decompress progressively (never buffered whole); peak = decoder working set + one
in-flight chunk. Non-file members yield `None`.

`members` is a selector (names/identities or predicate), not a transform. Streams
are lazy: unselected/unread members are not opened/decompressed and do not request
passwords. Yields the original mutable `ArchiveMember` so late-bound fields stay
visible.

Yielded streams are iterator-owned and valid only until advance: the iterator SHALL
close/invalidate the previous stream before the next yield. MUST NOT retain a
growing decompressed-block cache until reader close. On solid archives, random
`open()` may re-decode from block start and warn to prefer `stream_members()`.

A `stream_members()` invocation is an exclusive one-pass/data-path operation in
both modes. It SHALL NOT overlap random `open()`, materialization, another
iteration/data pass, unrelated extraction, or reader close. An `extract_all()`
owner MAY invoke it as a child pass and MAY read/close the yielded child stream.
Unrelated overlap SHALL raise `ArchiveyUsageError` at the later op and leave the
active pass/stream valid. (Unlike random `open()`, whose independently owned
streams may coexist when `CONCURRENT` is declared — see `reader-concurrency`.)

#### Scenario: stream_members matrix

| Case | Expected |
| --- | --- |
| Yielded file stream emits diagnostic before advance | Stream + reader snapshots share one retained occurrence |
| Selector excludes member / stream unread | No open/decompress; no data-path diagnostic |
| Solid archive | Progressive decode; peak = decompressor state + one chunk |
| `stream_members(lambda m: m.name.endswith(".txt"))` | Only `.txt`; unselected never opened; original mutable members |
| Fully read stream, then inspect member | Late-bound fields (e.g. size/CRC) visible on same object |
| Advance after one yield | Prior stream closed/invalidated first |
| Random `open()` during active pass | `ArchiveyUsageError`; pass remains usable |
| Close/abandon partial generator | Current stream closed; pass ownership released once |
| Random `open()` into solid block | Re-decode from block start + skip; warn to prefer `stream_members()` |

### Requirement: Transparent link following

`open()` / `read()` SHALL follow symlinks and hardlinks through shared reader
logic; `open()` SHALL keep returning `ArchiveStream` after following.

**Hardlinks (positional):** most recent matching target **strictly before** the
link (TAR/RAR5 model). Malformed later-only source: RA falls back to the later
member (extraction recovers — see `format-tar`); streaming cannot resolve forward
and fails per `OnError`. Modes SHALL agree on hardlink resolution for the same
archive.

**Symlinks:** RA → last matching target overall; streaming → latest seen so far
(forward stays `link_target_member is None`). Forward-visibility difference is
inherent to a single pass.

**Target-name resolution:** hardlink targets are archive-root relative
(normalized as-is); symlink targets join to the link's directory first.
Absolute/`..`-escaping symlink targets stay unresolved (`None`; open →
`LinkTargetNotFoundError`). Directory lookup tries bare and `/`-suffixed forms.

Follow chains recursively; detect cycles by **member id** (not name); no arbitrary
depth limit. Missing target → `LinkTargetNotFoundError`; cycle → `ReadError`.
Terminal fully-dereferenced target (when known) is `member.link_target_member`
(see `archive-data-model`). Diagnostics for a linked open cover follow + read of
that one `open()` operation.

#### Scenario: link resolution matrix

| Case | Expected |
| --- | --- |
| Valid chain to file data | One `ArchiveStream`; diagnostics cover follow + read of that open |
| Hardlink → earlier file | Stream yields that file's data |
| Missing target | `LinkTargetNotFoundError` |
| Chain revisits member id | `ReadError` (cycle); no infinite recursion |
| Symlink → file in archive | Stream yields target file data |
| Symlink `dir/link` → `file` / `./file` | Lookup `dir/file`, not root-relative `file` |
| Absolute / `..`-escaping symlink | `link_target_member is None`; open → `LinkTargetNotFoundError` |
| Duplicate names, hardlink | Most recent occurrence strictly before the link |
| Duplicate names, symlink (RA) | Last occurrence overall |
| Hardlink source only later | RA falls back to later member; streaming cannot resolve |
| Two distinct same-named members on one chain | Not a cycle (id-based tracking) |

### Requirement: Context-manager and close lifecycle

The reader SHALL implement `__enter__` / `__exit__` / `close()`. `close()` SHALL
be idempotent.

**Caller observables:**

- Exiting `with open_archive(...)` closes the reader.
- After reader close, every new reader operation or property (including
  `__enter__`, iteration/listing/lookup, metadata/cost, `open`/`read`,
  `stream_members`, extraction) SHALL raise `ArchiveyUsageError`. Repeated
  `close()` / `__exit__` are no-ops.
- A member stream opened before close MAY remain usable until that stream is
  closed (escaped stream). Backend resources stay alive until the last such
  stream closes. Callers SHOULD close member streams promptly.
- Archivey SHALL never close a caller-supplied `BinaryIO`. If the caller closes
  it early, a later operation raises `ArchiveyUsageError` for the closed source;
  concurrent external close with I/O is unsupported.
- `__exit__` always calls `close()`. Close failure propagates on normal exit;
  during body-exception unwind the body exception remains via normal chaining.

**Under `MemberStreams.CONCURRENT`:** `reader.close()` drains in-flight worker
`open()`/`read()` before transitioning to closed (see `reader-concurrency`).
Without `CONCURRENT`, concurrent close with an actively executing worker call is
rejected.

Lease/token/teardown once-guards and dual-failure `ExceptionGroup` rules:
`reader-concurrency`.

#### Scenario: lifecycle matrix

| Case | Expected |
| --- | --- |
| Open stream, then close reader (no concurrent I/O) | New reader ops → `ArchiveyUsageError`; stream usable until its close; backend released after final stream close |
| Idle open stream + `reader.close()` | Close succeeds; stream remains usable until stream close |
| Caller-supplied `BinaryIO`, all closed | Library does not call `close()` on that source |
| `open_archive()` context exits | Reader closed; backend released unless an escaped stream remains open |
| Op after reader close | `ArchiveyUsageError` |

### Requirement: Password candidates and provider

`password` SHALL accept a single `str | bytes`, an **ordered sequence**, and/or a
**provider** `PasswordProvider = Callable[[PasswordRequest], str | bytes | None]`:

```python
@dataclass(frozen=True)
class PasswordRequest:
    member: ArchiveMember | None  # None for archive-level (header) decryption
    attempt: int                  # 1 on first ask for this unit; increments on failure
```

Per encrypted unit (member / 7z folder / archive header), try in order: per-archive
**known-good** list (successes this open, most recent first), then remaining sequence
candidates, then provider repeatedly until `None`. Successful passwords SHALL join
known-good for the rest of the operation so a provider is consulted once per *new*
password rather than once per member. Exhaustion (or provider `None`) →
`EncryptionError`. No per-call password on `open()`/`read()`.

**Concurrent use (observable):** After materialization, workers MAY open
differently encrypted members concurrently; known-good promotions are shared;
provider callbacks are serialized; same-reader reentry from a provider raises
`ArchiveyUsageError`. Protocol/lock details: `reader-concurrency`.

#### Scenario: password matrix

| Case | Expected |
| --- | --- |
| `password=[pw_a, pw_b]`, members use different passwords, one streaming pass | Each unit matches; pass completes without RA |
| Provider + unknown password needed | Called with that member's `PasswordRequest`; success → known-good; later same-pw members skip provider |
| Provider password fails, consulted again | New request has incremented `attempt` |
| Provider returns `None` | `EncryptionError` for that unit |
| Header-encrypted archive, provider only | Request with `member is None` |
| Concurrent opens of different encrypted units (post-materialization) | Each decrypts correctly; promotions shared without races |
| Provider starts another password op on same reader | Nested op → `ArchiveyUsageError` |

### Requirement: Confirm candidates when a weak check permits retries

When a format's password check can admit wrong values, a candidate SHALL NOT be
accepted or added to known-good on that weak check alone if another distinct
candidate may be tried. Confirm with the strongest available bounded signal
(bounded decompression prefix, shared-pass per-candidate checksum, or full
validation when small). Confirmation SHALL obey "Bounded implicit temporary
storage" — no plaintext buffering proportional to unit size.

After confirmation, backend MAY re-open/re-decode the accepted candidate for the
caller's stream. Returned stream SHALL keep ordinary read-time integrity checking.

"Another candidate may be tried" includes ≥2 distinct known-good/static values and
a provider that can return another answer. Provider stays lazy (no advance
enumeration). Duplicates are not distinct. Provider-raised `EncryptionError` is
provider failure — propagate unchanged, not as candidate exhaustion.

If confirmation fails and candidates are exhausted, report the irreducible
ambiguity (wrong password **or** corrupt unit). MAY use `EncryptionError`. SHALL
NOT return an unvalidated candidate. A single distinct static candidate MAY keep
the format's normal lazy streaming path.

#### Scenario: weak-check confirmation matrix

| Case | Expected |
| --- | --- |
| Wrong candidate passes weak check first of two | Reject via confirmation; stream from correct candidate |
| Large member, many candidates | Confirmation bounded — not proportional to member size |
| Provider answer fails confirmation | Request next answer without pre-enumerating; accept only after confirm |
| Confirmation fails, then provider raises `EncryptionError` | Provider exception propagates unchanged |
| All candidates fail confirmation | Ambiguity message; no candidate bytes returned |
| One distinct static value (incl. duplicates) | No eager consume for disambiguation; ordinary read-time errors |

### Requirement: Bounded implicit temporary storage

Reader ops SHALL NOT consume memory or temp storage proportional to member/archive
size as an implicit side effect of open/read/validate/password-confirm. Silently
spooling plaintext to a temp file is forbidden. A per-format strategy that
inherently needs proportional temp storage (e.g. `format-rar`'s documented
`unrar x`-to-tempdir) is allowed only when declared in that format's capability
spec. Caller's own buffering of a returned stream is unrestricted.

#### Scenario: bounded storage matrix

| Case | Expected |
| --- | --- |
| Encrypted member, many candidates | Confirmation temp use bounded by a constant |
| Backend can only serve via materialization | Strategy declared in format spec, not adopted silently |

### Requirement: Explicit configuration object

The system SHALL define these complete frozen schemas:

```python
@dataclass(frozen=True)
class ExtractionLimits:
    max_extracted_bytes: int | None = 2 * 2**30
    max_ratio: float | None = 1000.0
    ratio_activation_threshold: int = 5 * 2**20
    max_entries: int | None = 1_048_576
    UNLIMITED: ClassVar["ExtractionLimits"]

@dataclass(frozen=True)
class ListingLimits:
    max_members: int | None = 1_048_576
    max_metadata_bytes: int | None = 64 * 2**20
    UNLIMITED: ClassVar["ListingLimits"]

@dataclass(frozen=True)
class ArchiveyConfig:
    use_rapidgzip: AcceleratorMode = AcceleratorMode.AUTO
    use_indexed_bzip2: AcceleratorMode = AcceleratorMode.AUTO
    strict_archive_eof: bool = False
    extraction_limits: ExtractionLimits = ExtractionLimits()
    listing_limits: ListingLimits = ListingLimits()
    diagnostic_policy: DiagnosticPolicy = DiagnosticPolicy()
    max_retained_diagnostic_references: int = 256
    on_diagnostic: Callable[[Diagnostic], None] | None = None
```

`max_retained_diagnostic_references` SHALL be non-negative. Policy/default/override
mappings and the dataclasses SHALL be defensively immutable. `config=None` →
immutable library default. No mutable global/context-local diagnostic policy or
callback.

A reader carries its open config, including `listing_limits` for its lifetime.
Later `extract_all(config=...)` MAY override policy/callback/strictness/
accelerators/`extraction_limits` for new work, but SHALL NOT change the
reader's effective `listing_limits` or
`max_retained_diagnostic_references` (see `diagnostics`). Per-call `limits`
still beat `config.extraction_limits`, then reader/library default. Other
per-call operational args stay outside `ArchiveyConfig`.

`strict_archive_eof=False` follows ordinary diagnostic policy for failed EOF check;
`True` forces `TruncatedError` after ordered diagnostic rules in `error-handling`.

`on_diagnostic` runs synchronously after count/retention/logging updates. Snapshot
reads from a callback are allowed. Starting another operation on the same
emitting reader/stream SHALL raise `UnsupportedOperationError`; other readers OK.
Callbacks hold no Archivey collector/reader/stream/backend/registry lock
(`diagnostics` / `reader-concurrency`).

#### Scenario: config matrix

| Case | Expected |
| --- | --- |
| `ArchiveyConfig()` | AUTO accelerators; EOF strictness false; documented extraction and listing defaults; COLLECT; budget 256; no callback |
| Reader budget 10, then `extract_all(config=…budget=1000)` | New policy/callback may apply; diagnostics still under budget 10 |
| `extract(..., extraction_limits=ExtractionLimits(max_ratio=100))` | 100:1 per-member ratio enforced (`safe-extraction`) |
| Reader opened with `listing_limits=ListingLimits(max_members=10)` | Listing caps stay at 10 for the reader lifetime even if later `extract_all(config=...)` omits listing_limits |

### Requirement: Reader-lifetime cumulative diagnostic snapshots

Every successfully created `ArchiveReader` SHALL expose:

```python
@property
def diagnostics(self) -> DiagnosticSummary: ...
```

Each access SHALL return a fresh immutable cumulative snapshot. Counts SHALL
include automatic-detection events that led to this reader (if any) plus every
subsequent open/list/read/stream/extract event it owns. Previously returned
snapshots SHALL not change. A stream returned by the reader SHALL expose an
operation-filtered `diagnostics` view of the same lifetime — not a separately
retained copy of the aggregate.

Value shape, retention budget, watermarks, and attachment rules: `diagnostics`.

#### Scenario: diagnostics matrix

| Case | Expected |
| --- | --- |
| Detection conflict + scan + rewind diagnostics | Later `reader.diagnostics` has exact cumulative counts in emission order; earlier snapshot unchanged |
| Two streams emit different diagnostics | Each stream sees only its op; reader sees both |
| Callback reads `diagnostics` then `reader.read(...)` | Snapshot OK (incl. current event); reentry → `UnsupportedOperationError` |

### Requirement: Collection form of MemberSelector

`MemberSelector` SHALL accept a predicate or `Collection[str | ArchiveMember]`,
normalized to a predicate at the API boundary:

- `str` matches **every** member with that normalized name (duplicates all match;
  extraction keeps sequential last-wins-on-disk)
- `ArchiveMember` matches by **identity** (`archive_id` + `member_id`; members are
  unhashable → id set, never member set)
- String and member entries MAY mix

#### Scenario: selector matrix

| Case | Expected |
| --- | --- |
| `stream_members(members=["a.txt"])` with two `a.txt` | Both yielded, archive order |
| Specific `ArchiveMember` among duplicates | Only that identity |
