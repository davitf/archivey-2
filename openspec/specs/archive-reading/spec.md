# Archive Reading

## Purpose

Uniform interface for opening and reading archives. `ArchiveReader` presents ZIP,
TAR, RAR, 7z, ISO, directories, and single-file compressed streams with consistent
metadata, iteration, and data-access semantics.

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
) -> ArchiveReader
```

`member_streams` is also accepted as a keyword — see the capabilities requirement.

`source`, multi-volume ordering, `streaming`, password candidates/providers,
encoding, configuration precedence, and backend selection retain their existing
contracts. `format=None` auto-detects; an explicit format bypasses detection.

Before detection/backend open, the implementation SHALL create the prospective
reader's one collector, budget, and initial operation watermark. Automatic
detection SHALL receive that collector. On success the returned reader owns the
same collector — opening SHALL NOT seed, merge, replay, or copy detection events
into a second collector. A retained detection occurrence therefore consumes one
aggregate budget slot and one occurrence id/order for the reader lifetime. On
raise, no reader is returned and the temporary collector is discarded.

#### Scenario: open / collector matrix

| Case | Expected |
| --- | --- |
| Auto-detect succeeds | Reader owns the detection collector (counters + retained entries); no duplicate refs |
| `format=ArchiveFormat.ZIP` succeeds | One collector covers open/later work; no detection run or detection diagnostic |
| `password="secret"` | Returned reader uses that password for encrypted members |

### Requirement: Declared member-stream capabilities

`open_archive()` SHALL accept `member_streams: MemberStreams` (flags, default none):

- `MemberStreams.CONCURRENT` — any number of member streams may be open simultaneously
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
def scan_members(self) -> list[ArchiveMember]: ...      # fully-resolved, either mode
def get_members_if_available(self) -> list[ArchiveMember] | None: ...  # index peek
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
make `get_members_if_available()` return it. An abandoned pass (early `break`, no
`scan_members()`) SHALL NOT finalize.

No `__len__` / `__getitem__` (not a collection; protocols are probed implicitly —
`list(reader)` probes `__len__` for preallocation). `len(ar)` → Python `TypeError`
in every mode; use `len(ar.members())`, `ar.info.member_count`, or count while
iterating. `list(ar)` just iterates.

`get_members_if_available()` is index-only: returns the list only when available
without scanning or reading member data, else `None`. Never scans or starts the
forward pass. Returned members may have unresolved links when targets live in
member data (see `access-mode-and-cost`).

With `streaming=True`, `members()` / `get()` / `open()` / `read()` SHALL raise
`UnsupportedOperationError` uniformly. Only one forward pass
(`__iter__`/`stream_members` or one `extract_all`) is allowed, with
`scan_members()` to finish/return it and `get_members_if_available()` anytime.
See the access-mode × method table in `access-mode-and-cost`.

#### Scenario: iteration / access-mode matrix

| Method / action | `streaming=False` | `streaming=True` |
| --- | --- | --- |
| `__iter__` | Yields in order; after first materialization, from cache | Single-use forward pass; second `__iter__`/`stream_members`/`extract_all` → `UnsupportedOperationError` |
| `members()` | Full scan if needed; returns list | `UnsupportedOperationError` |
| `scan_members()` | Same fully-resolved list as `members()`; reader stays RA-usable | Finishes/drains pass; returns fully-resolved list (incl. forward symlinks); pass consumed |
| `scan_members()` after early `break` | n/a | Drains remainder; returns complete fully-resolved list |
| `get_members_if_available()` after completed pass | List if indexed/cached | Fully-resolved list (not `None`); forward-link finalization visible on yielded objects |
| `get_members_if_available()` after abandoned pass | — | `None` |
| `len(ar)` | `TypeError` | `TypeError` |
| `list(ar)` | Iterates | Iterates (consumes the single pass) |

### Requirement: Name lookup and member identity

No `__getitem__` (duplicates break mapping; dunders are probed implicitly).
`open()`/`read()` accept a name and raise `KeyError` when absent.

```python
def get(self, name: str, default=None) -> ArchiveMember | None: ...
def __contains__(self, member: ArchiveMember) -> bool: ...  # identity, O(1), any mode
```

`get()` looks up by normalized name; duplicates → **last** (sequential extraction
winner). On `streaming=True` SHALL raise `UnsupportedOperationError` regardless of
loaded index. For a no-scan peek use `get_members_if_available()`.

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

A reader-owned stream SHALL use an operation token/watermark over the reader's
collector — not a second diagnostic copy. A standalone `ArchiveStream` SHALL own
one stream-lifetime collector.

#### Scenario: read / open matrix

| Case | Expected |
| --- | --- |
| `open("data.bin")` succeeds | `ArchiveStream` as `BinaryIO`; `stream.diagnostics` = that operation only |
| Reader-owned stream emits rewind diagnostic | Stream + reader snapshots can show it; collector retains/charges once |
| `read("readme.txt")` | Full uncompressed `bytes` |
| `open(member)` from a different reader | `ValueError` |

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
streams may coexist.)

Each yielded stream is an operation-filtered view over the reader's single
collector/budget; advancing does not create a new collector.

#### Scenario: stream_members matrix

| Case | Expected |
| --- | --- |
| Yielded file stream emits diagnostic before advance | Stream + reader snapshots share one retained aggregate entry |
| Selector excludes member / stream unread | No open/decompress; no data-path diagnostic |
| Solid archive | Progressive decode; peak = decompressor state + one chunk |
| `stream_members(lambda m: m.name.endswith(".txt"))` | Only `.txt`; unselected never opened; original mutable members |
| Fully read stream, then inspect member | Late-bound fields (e.g. size/CRC) visible on same object |
| Advance after one yield | Prior stream closed/invalidated first |
| Random `open()` during active pass | `ArchiveyUsageError`; pass remains usable |
| Close/abandon partial generator | Current stream closed; child/root scopes released once |
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
depth limit. Missing target → `LinkTargetNotFoundError`; cycle → `ReadError`. A
stream reached through a link uses the initiating `open()`'s collector/token.
Terminal fully-dereferenced target (when known) is `member.link_target_member`
(see `archive-data-model`).

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

The reader SHALL implement `__enter__` / `__exit__` / `close()`. Lifecycle state
(`OPEN`, `READER_CLOSED`, `TEARDOWN_RUNNING`, `TEARDOWN_COMPLETE`) and lease count
SHALL be guarded independently from materialization. `close()` SHALL be idempotent;
without unsupported concurrent ops it atomically marks `READER_CLOSED`.

Each random-open member stream owns a backend-resource lease. Already-open streams
remain usable after reader close per their capabilities and keep required backend
resources alive. Backend/source teardown SHALL run exactly once after reader closed
**and** the final lease is released. Failed open releases its reserved lease (incl.
lazy init failure and closing a lazy stream before first use). Final releaser claims
teardown under the lifecycle lock, performs it after releasing that lock, records
completion without retry. Backend teardown and inner stream close run outside
lifecycle locks. Lazy-open failure raises the translated error from the triggering
op, permanently releases/closes that handle, later I/O → closed-stream `ValueError`,
repeated stream `close()` is a no-op.

If explicit close triggers final teardown and teardown fails: closer is irrevocably
closed, translated error propagates once; repeated closes SHALL NOT retry/re-raise.
Safety-net finalizer uses the same once-guards, never raises, MAY report via
`sys.unraisablehook` only outside Archivey locks. Native accelerator finalizers
retain close-before-free.

Member close SHALL release its lease in `finally` even if inner close fails. If
inner close and final backend teardown both fail → `ExceptionGroup` of both
translated errors. `__exit__` always calls `close()`; close failure propagates on
normal exit; during body-exception unwind the body exception remains via normal
chaining.

Archivey SHALL close path handles/wrappers it owns only after the final lease. It
SHALL never close a caller-supplied `BinaryIO`. Early caller close → later op raises
`ArchiveyUsageError` for the closed source; concurrent external close with I/O is
unsupported.

Exiting `with open_archive(...)` closes the reader; an escaped member stream
extends backend lifetime until that stream closes. Callers SHOULD close member
streams promptly. Under `MemberStreams.CONCURRENT`, `reader.close()` drains
in-flight worker `open()`/`read()` (blocks until return) before closed; escaped
idle streams keep leases. Without `CONCURRENT`, concurrent close with an actively
executing worker call is rejected. No close-vs-stream-I/O linearization beyond that
draining contract.

After reader close: repeated `close()`/`__exit__` are no-ops; already-open streams
continue. Every new reader op/property (incl. `__enter__`, iteration/listing/lookup,
metadata/cost/source counters, `open`/`read`, `stream_members`, extraction) SHALL
raise `ArchiveyUsageError`. Escaped streams use pre-close context for error
translation and MUST NOT call those properties; lease-bound short-lived worker
tokens prevent teardown racing each call.

#### Scenario: lifecycle matrix

| Case | Expected |
| --- | --- |
| Open stream, then close reader (no concurrent I/O) | New reader ops → `ArchiveyUsageError`; stream usable until its close; teardown once after final stream close |
| Idle random-open stream + `reader.close()` | Close succeeds; later stream I/O via lease-bound worker entry until stream close |
| `_open_member` raises after reserving lease | Reservation released; later reader close can tear down |
| Lazy first I/O → `_open_member` raises | Translated error; lease released; later I/O → `ValueError`; close no-op |
| Teardown raises on explicit/final-stream close | Closer irrevocably closed; error once; `TEARDOWN_COMPLETE`; no retry |
| Inner-close + teardown both fail on final member close | Lease/state released once; `ExceptionGroup` of both |
| Caller-supplied `BinaryIO`, all closed | Wrappers released; source `close()` never called |
| `open_archive()` context exits | Reader closed + lease released; backend released unless escaped stream still leased |

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
known-good for the rest of the operation. Exhaustion → `EncryptionError`. No
per-call password on `open()`/`read()`.

Safe under concurrent post-materialization opens: static candidates immutable/ordered;
known-good snapshots/promotions and per-unit tried/attempt state synchronized;
expensive KDF/decrypt without lifecycle/operation/materialization/password locks, but
MAY use a required backend/source lock around an atomic decode/handle op.

At most one provider-driven resolution turn per reader. Protocol:
claim/call/validate/publish — claim under a condition, release Archivey locks, call
provider, test candidates without lifecycle/materialization/password locks (backend
lock only for atomic validation), publish, release turn, wake waiters in `finally`.
Turn stays claimed through retries until success/`None`. Waiters recheck known-good
before claiming. Provider callbacks are serialized and lock-free. Reentrant
same-reader password ops from a provider SHALL raise `ArchiveyUsageError`. Attempt
counts remain per unit.

#### Scenario: password matrix

| Case | Expected |
| --- | --- |
| `password=[pw_a, pw_b]`, members use different passwords, one streaming pass | Each unit matches; pass completes without RA |
| Provider + unknown password needed | Called with that member's `PasswordRequest`; success → known-good; later same-pw members skip provider |
| Provider password fails, consulted again | New request has incremented `attempt` |
| Provider returns `None` | `EncryptionError` for that unit |
| Header-encrypted archive, provider only | Request with `member is None` |
| Concurrent post-materialization opens of different units | Independent candidate order; promotions race-free; attempt state not overwritten |
| Two workers need provider at once | One callback at a time, no Archivey lock; waiter resumes after publish |
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
mappings and both dataclasses SHALL be defensively immutable. `config=None` →
immutable library default. No mutable global/context-local diagnostic policy or
callback.

A reader carries its open config. Later `extract_all(config=...)` MAY override
policy/callback/strictness/accelerators/limits for new work, but
`max_retained_diagnostic_references` SHALL NOT replace/reset/lower/enlarge the
existing collector budget. Per-call `limits` still beat `config.extraction_limits`,
then reader/library default. Other per-call operational args stay outside
`ArchiveyConfig`.

`strict_archive_eof=False` follows ordinary diagnostic policy for failed EOF check;
`True` forces `TruncatedError` after ordered diagnostic rules in `error-handling`.

Callbacks run synchronously after count/retention/logging updates with no
collector/reader/stream/backend/registry lock. Snapshot reads from a callback are
allowed. Starting another op on the same emitting reader/stream SHALL raise
`UnsupportedOperationError`; other readers OK.

#### Scenario: config matrix

| Case | Expected |
| --- | --- |
| `ArchiveyConfig()` | AUTO accelerators; EOF strictness false; documented extraction defaults; COLLECT; budget 256; no callback |
| Reader budget 10, then `extract_all(config=…budget=1000)` | New policy/callback may apply; diagnostics still under budget 10 |
| `extract(..., extraction_limits=ExtractionLimits(max_ratio=100))` | 100:1 per-member ratio enforced (`safe-extraction`) |

### Requirement: Reader-lifetime cumulative diagnostic snapshots

Every successfully created `ArchiveReader` SHALL own a diagnostic collector for its
lifetime and expose:

```python
@property
def diagnostics(self) -> DiagnosticSummary: ...
```

Each access SHALL return a fresh immutable cumulative snapshot. Exact counts SHALL
include detection occurrences that led to the reader plus every subsequent
open/list/read/stream/extract occurrence it owns (incl. unreained detail). Prior
snapshots SHALL NOT change. A reader-returned stream SHALL expose an
operation-filtered `diagnostics` view over the same collector — no separately
retained aggregate copies.

#### Scenario: diagnostics matrix

| Case | Expected |
| --- | --- |
| Detection conflict + scan + rewind diagnostics | Later `reader.diagnostics` has exact cumulative counts in emission order; earlier snapshot unchanged |
| Two streams emit different diagnostics | Each stream sees only its op; reader sees both; one retained aggregate |
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

### Requirement: Multiple concurrently-open member streams

Every **random-access** reader with `MemberStreams.CONCURRENT` SHALL support any
number of simultaneously open member streams read interleaved without corruption.
Without the flag, the single-live-stream default applies. Cost never determines
legality.

**Post-materialization worker seam.** After one owner completed `members()` /
`scan_members()` and the list/index is published, concurrent `open()` from multiple
threads SHALL be supported. Different stream objects have independent positions —
workers MAY concurrently `read`/`readinto`/`close` (and `seek`/`tell` under
`SEEKABLE` where supported). Same stream object needs caller sync (like ordinary
files). MUST NOT rely on the GIL.

**Materialization boundary.** Build members/name index in private state, complete
(incl. link resolution), publish once as immutable internal containers. Public
`list` returns SHALL be copies that cannot structurally mutate those caches.
`ArchiveMember` keeps backend late-bound fields. Under `CONCURRENT`, overlapping
first-touch is coordinated (one builder, others wait; failed attempt →
`UNMATERIALIZED` + wake). Without `CONCURRENT`, second overlapping materialization
→ `ArchiveyUsageError`. Distinct reader-wide passes stay single-owner. Late-bound
random-open updates MUST be idempotent and synchronized; conflicting LWW
forbidden. Cache states: `UNMATERIALIZED` / `MATERIALIZING` / `MATERIALIZED` —
lifecycle MUST NOT add `CLOSED`. Failed build discards private state →
`UNMATERIALIZED`.

**Backend compliance.** Archivey byte-range backends MUST use views with per-view
positions and atomic shared-source handle ops. External-library backends MUST
coordinate equivalently: ZIP MAY use stdlib `_SharedFile` for seek/read and MUST
serialize `ZipFile.open` / member-stream close / `ZipFile.close` under
`CONCURRENT`; RA TAR/ISO MUST use the one-per-reader lock from
`tar-concurrent-open`. Solid formats give each stream independent logical
position/state (per-open decoders or synchronized bounded shared decode OK).

**Reader-wide operation ownership.** Distinct passes (`__iter__`,
`stream_members`, `extract_all`) and `scan_members` /
`get_members_if_available` init remain single-owner vs each other and the worker
seam. Under `CONCURRENT`, first-touch waits/shares; `close()` drains in-flight
workers. Ownership uses an explicit unforgeable root token (not thread identity).
Private helpers MAY receive that token for child scopes. Unrelated/reentrant
public calls without token are rejected even on the owner thread. Later conflict
→ `ArchiveyUsageError` before state change; earlier root/children remain usable.

Random `open()` and each stream op hold a short-lived worker token only while the
call executes. Idle open stream owns a lifecycle lease, not active ownership, plus
a private lease-bound entry so later I/O remains admissible after `reader.close()`.
Under `CONCURRENT`, `close()` waits for in-flight workers then closes; without it,
close is rejected while a worker executes. Closure enables no new reader API.

"Overlap" means concurrent method/I/O execution, not idle open-stream lifetime.

**`stream_members()` is separate.** Owns the one-pass data path; MUST NOT overlap
random `open()` or another forward/data pass. Advance closes prior yielded stream
(iterator-owned; does not apply to independent random `open()` streams). Yielded
stream carries a child scope. Exhaustion/exception/generator close/abandon SHALL
close current stream and release pass scope once. Simultaneous streams → materialize
+ random `open()`.

**Cost is informational.** `AccessCost.SOLID` / `solid_block_count` warn about
repeated decompression; they never disable the guarantee. `stream_members()` remains
the efficient one-decode solid path.

**Closed-source misuse / bounds.** Live lease prevents reader-owned backends from
closing under a stream. Externally closed caller-owned source → typed error, not
arbitrary/empty bytes. Shared-source views clamp past-end bounds (short view, not
construction failure).

**Callbacks / lock scope.** Password providers, selectors/filters, progress,
logging, diagnostic formatting/stamping, `sys.unraisablehook`, and user-visible
close/finalizer hooks MUST run with no Archivey lock. Decode/password validation is
not a callback: no lifecycle/operation/materialization/password locks, but MAY hold
the required backend/source lock around an atomic op. Nested lock order:
lifecycle/operation → materialization → password; backend/source locks are leaves.
Stream state uses claim/call/publish and MUST release before lazy `open_fn`, inner
I/O/close, backend ops, or lease release.

#### Scenario: concurrency matrix

| Case | Expected |
| --- | --- |
| Post-`members()`, two threads `open()` different members | Independent correct bytes/positions; no cache/reader/source race |
| `CONCURRENT` interleaved reads, any format | Both correct; `AccessCost` describes expense only |
| Concurrent first-touch on unmaterialized `CONCURRENT` reader | One builder; others wait; all proceed on published snapshot; no overlap `ArchiveyUsageError` |
| Materialization fails before publish | Private state discarded; `UNMATERIALIZED`; waiters re-elect/see error; lifecycle stays `OPEN` |
| Workers active + `__iter__`/`stream_members`/`extract_all` | Later op → `ArchiveyUsageError`; active streams OK |
| `close()` under `CONCURRENT` with in-flight workers | Blocks until return; closes; no raise merely for active workers; idle escaped streams stay leased |
| Two members in one solid block opened together | Correct independent bytes; may re-decode; no concurrency exception |
| Same on CPython `3.13t` free-threaded job | Data-race-free; same observables as regular build |

### Requirement: Coordinated first-touch materialization

Under `MemberStreams.CONCURRENT`, concurrent first-touch on an unmaterialized list
SHALL block all but one caller until the immutable snapshot is published (not
reject). Materialization runs exactly once. Non-concurrent and uncontended paths
SHALL be unchanged.

#### Scenario: first-touch matrix

| Case | Expected |
| --- | --- |
| Several threads first-touch via `open()`/`members()`/`get()` | One materializes; others wait; all proceed on snapshot; no overlap `ArchiveyUsageError` |
| Electing materialization fails (e.g. corrupt header) | Back to unmaterialized; no partial snapshot; waiters see error or re-elect |
| Default reader or uncontended path | No waiting; scan/link reads/callbacks with no reader-state lock held |

### Requirement: Draining reader close

Under `CONCURRENT`, `reader.close()` SHALL wait for in-flight worker
`open()`/`read()` then transition to closed (not raise for active workers). Escaped
streams keep the lifecycle-lease contract. Close idempotency, one-shot teardown, and
post-close rejection SHALL be preserved.

#### Scenario: draining close matrix

| Case | Expected |
| --- | --- |
| `close()` while workers execute | Blocks until return; then closed; no raise merely for active workers |
| Escaped stream still open as `close()` returns | Readable until its `close()`; teardown once after final lease |
| Two threads `close()` / `__exit__` | Teardown once; both return; dual failures → one `ExceptionGroup` |
| Op after `close()` returned | `ArchiveyUsageError` (unchanged) |

### Requirement: Distinct passes and shared streams remain single-owner

Overlapping *distinct* reader-wide passes or concurrent access to one stream object
SHALL remain rejected or caller-synchronized — coordination is bounded to
materialization and close.

#### Scenario: single-owner matrix

| Case | Expected |
| --- | --- |
| Active `extract_all()` / `stream_members()` + another pass | Later → `ArchiveyUsageError` |
| Concurrent ops on same `ArchiveStream` | Caller's responsibility; no per-stream locking added |
| `seekable() is False` + seek | `io.UnsupportedOperation` (no synthetic seek) |
| `extract_all()` drives `stream_members` + hardlink recovery | Token-bearing child scopes permitted; unrelated public op rejected |
| Caller-owned source closed externally while stream leased | Typed error; not arbitrary/empty bytes |

### Requirement: Random-access member-open is reentrant and reader-state-free

For every RA backend, `_open_member` SHALL derive the stream from the member plus
immutable/published archive state and coordinated backend resources. MUST NOT keep
unsynchronized per-open scratch on the reader another open can overwrite.
Synchronized shared bookkeeping (operation state, stream leases, password/key
caches, backend handle locks) is permitted/required where applicable.

Archivey-owned byte ranges MUST use shared-source views with per-view position. A
library-owned seek-before-read backend (RA TAR/ISO) MUST coordinate the full
shared-handle lifecycle through its per-reader lock. Immutable member/name
structures MAY be read concurrently after materialization.

Forward-only/streaming passes remain out of scope (one progressive decoder). No
RA TAR/ISO exemption — they satisfy the invariant via locked library streams.

#### Scenario: reentrant open matrix

| Case | Expected |
| --- | --- |
| Two post-materialization `open()` concurrent | No unsynchronized per-open reader scratch; both streams correct under interleaving |
| RA TAR/ISO multi-stream | Shared-handle/library decode ops serialized by one per-reader lock; callbacks/diagnostics run unlocked |
