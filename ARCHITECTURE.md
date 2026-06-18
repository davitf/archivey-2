# Archivey — Architecture and Design Decisions

> This document explains the architecture of the library, the key design decisions, and the trade-offs considered. Read SPEC.md first for the external contract; this document explains the internals.

---

## 1. Module Layout

```
src/archivey/
├── __init__.py            # Public API re-exports: open_archive(), create(), extract(),
│                          #   detect_format(), and the public types
├── py.typed               # PEP 561 marker
│
├── core.py                # open_archive() / create() / extract() / detect_format() entry points
├── types.py               # Public types: ArchiveMember, ArchiveInfo, ArchiveFormat
│                          #   (+ ContainerFormat/StreamFormat), MemberType, CompressionAlgorithm/Method,
│                          #   CostReceipt (+ Listing/Access/StreamCapability),
│                          #   ExtractionPolicy/OverwritePolicy, MemberSelector/MemberFilter aliases
├── exceptions.py          # ArchiveyError hierarchy
├── filters.py             # ExtractionPolicy transforms + path sanitizer
│
├── internal/              # private spine + helpers (not part of the public import surface)
│   ├── base_reader.py     # BaseArchiveReader ABC + default impls (link-follow, context stamping)
│   ├── base_writer.py     # ArchiveWriter ABC
│   ├── registry.py        # BackendRegistry + ReadBackend / WriteBackend ABCs
│   ├── detection.py       # Format detection engine + PeekableStream         (Phase 3)
│   ├── extraction.py      # ExtractionCoordinator + BombTracker (uses filters) (Phase 4)
│   ├── progress.py        # ExtractionProgress / ExtractionResult              (Phase 4)
│   ├── io_helpers.py      # is_seekable, ensure_binaryio, simplified BinaryIOWrapper, …
│   └── streams/           # compressed + seekable stream layer                 (Phase 2)
│       └── compat.py, detect.py, slice.py, decompress.py, xz.py, lzip.py
│
└── formats/               # one module per format backend
    ├── __init__.py            # registers backends at import time
    ├── directory_reader.py    # Directory pseudo-backend                       (Phase 1)
    ├── zip_reader.py          # ZIP (zipfile stdlib)
    ├── tar_reader.py          # TAR all variants (tarfile stdlib)
    ├── single_file_reader.py  # GZ, BZ2, XZ, ZST single-file compressors
    ├── sevenzip_reader.py     # 7-Zip — native reader (stdlib lzma/bz2/zlib); py7zr only for writing
    ├── rar_reader.py          # RAR — native metadata parser + system `unrar` for data (read-only)
    └── iso_reader.py          # ISO 9660 (pycdlib, optional)

tests/
├── fixtures/              # Committed binary archives — only what can't be generated
│   ├── adversarial/       # Hand-crafted: path traversal, zip bombs, corrupt headers
│   ├── external/          # Archives requiring specific tools/OS (Windows junctions, etc.)
│   └── *.json             # Sidecar per committed archive (expected member list)
├── create_adversarial.py  # Script that (re)generates adversarial fixtures
├── sample_archives.py     # Declarative specs: ArchiveContents, FileInfo, ArchiveCreationInfo
├── create_archives.py     # Generates archives from specs into tmp_path / cache dir
├── conftest.py            # pytest_generate_tests, sample_archive_path fixture
├── test_detection.py
├── test_types.py
├── test_zip.py
├── test_tar.py
├── test_single.py
├── test_7z.py
├── test_rar.py
├── test_iso.py
├── test_extraction.py     # filter/security tests; uses adversarial fixtures
├── test_writing.py
├── test_conversion.py
├── test_equivalence.py    # equivalence matrix across formats
└── test_patterns.py       # sample usage patterns (hashing, link-following, conversion)
```

---

## 2. Key Design Decisions

### 2.1 Mutable ArchiveMember filled in place while streaming

→ see SPEC.md §4.4 / openspec `archive-data-model`

`ArchiveMember` is a **mutable** stdlib `@dataclass` (deliberately *not* frozen). The
reason is that several fields are genuinely unknown when a member is first yielded and
only become known once its data has been read:

- the final `size`/CRC of a gzip stream or a ZIP data-descriptor entry, and
- a `link_target` that is stored in (or encrypted within) the member's **data** rather
  than its header.

The library fills these fields **in place** on the same object the caller already holds,
so late values appear without a re-fetch. This is required under `streaming=True`, where
the member list cannot be materialized and re-read — the library cannot hand back a fresh
object, so it must complete the one in flight.

**Contract — callers treat members as read-only.** The library is the *only* writer. A
caller (or an extraction/iteration filter) that needs an altered member calls
`member.replace(**kwargs)`, which returns a **copy** with the changes applied and never
mutates the original.

**Consequence — `ArchiveMember` is unhashable.** A mutable value object must not be a
`set` element or dict key, so callers key by `member.name` or `member.member_id` instead.
Equivalence tests compare members by `==` over name-keyed lists (the `hashes` and `extra`
fields are excluded from `__eq__`). The old "frozen ⇒ hashability / thread-safety"
rationale no longer applies: thread-safety is moot because readers are one-per-thread, and
hashability is replaced by name/`member_id` keying.

For large archives, the backend yields `ArchiveMember` objects one at a time via a
generator — we never build a `list[ArchiveMember]` unless the caller calls `.members()`.
This keeps peak memory O(1) during sequential iteration.

### 2.2 Backends as pure factories: ReadBackend / WriteBackend split

→ see SPEC.md §9 / openspec `backend-registry`

Reading and writing are different concerns with different state, lifecycles, and even
availability (7z reading is native while writing needs `py7zr`; RAR has no writer at all),
so there are **two** abstract base classes — `ReadBackend` and `WriteBackend` — rather than
one `Backend` with an optional write method. A format may have a reader, a writer, both, or
(RAR) only a reader. They live in **separate registries**: `register_reader()` /
`reader_for_format()` and `register_writer()` / `writer_for_format()`.

Each `ReadBackend`/`WriteBackend` subclass is a **stateless factory**: the class holds no
per-archive state. `open_read()` returns an `ArchiveReader` (and `open_write()` an
`ArchiveWriter`) that holds all state. This separation allows:
- Multiple readers open simultaneously from the same backend class.
- Easy testing: mock `ReadBackend.open_read()` to return a fake reader.
- Clean registration: backends register their class, not instances.

A `ReadBackend` declares its magic and extensions as **data**
(`MAGIC`/`MAGIC_OFFSET`/`EXTENSIONS`) — it has **no** `detect(peek)` method. Byte matching
is centralized in `detect_format()` (the detector aggregates each backend's declared magic
table); selection is then a pure `reader_for_format(detected_format)` lookup. Backends do
not re-run byte matching. Backend selection by format and detection are two distinct steps.

### 2.3 Single ArchiveReader ABC for all backends

→ see SPEC.md §3.2 / openspec `archive-reading`

Rather than having backend-specific reader classes be the public API, all backends return objects that implement the `ArchiveReader` ABC. The ABC provides default implementations for methods like `extract_all()` that delegate to the `internal/extraction.py` module — backends only need to implement iteration and raw data access.

```
BaseArchiveReader (ABC in internal/base_reader.py)
├── ZipReader        (formats/zip_reader.py)
├── TarReader        (formats/tar_reader.py)
├── SingleFileReader (formats/single_file_reader.py)
├── SevenZReader     (formats/sevenzip_reader.py)
├── RarReader        (formats/rar_reader.py)
├── IsoReader        (formats/iso_reader.py)
└── DirectoryReader  (formats/directory_reader.py)
```

The methods backends **must or may** implement:
```python
# --- Class-level attributes (set once per backend class, not per instance) ---

_SUPPORTS_RANDOM_ACCESS: bool = True
# Set to False for inherently sequential formats (plain .tar on a non-seekable stream).
# The ABC reads this to decide whether to allow open() and extract().

_MEMBER_LIST_UPFRONT: bool = True
# Set to True if the format has a central directory / header index (ZIP, 7z) so
# members() is cheap. Set to False for streaming formats (TAR) where listing requires
# reading the whole archive.

# --- Required abstract methods ---

@abstractmethod
def _iter_members(self) -> Iterator[ArchiveMember]: ...
# Yield ArchiveMember objects in archive order, metadata only.
# Called once by the base class to populate the member registry.
# Store any backend-specific data needed by _open_member in member.extra.

@abstractmethod
def _open_member(self, member: ArchiveMember) -> BinaryIO: ...
# Return a raw data stream for member. No link following.
# A solid backend re-decodes the block from its start (no persistent cache).
# Called only for file members.

@abstractmethod
def _close_archive(self) -> None: ...
# Release backend resources (file handles, temp dirs). Called once by close().

# --- Optional overrides ---

def _iter_with_data(self) -> Iterator[tuple[ArchiveMember, BinaryIO | None]]:
    # Default: naive — calls _open_member() per file member.
    # Correct for non-solid formats (ZIP, TAR, GZ): no extra cost.
    # Solid-archive backends MUST override for bounded streaming memory:
    #   SevenZReader: decode each folder once, yield its members as the
    #     decompressor produces bytes; peak ≈ decompressor state + one chunk.
    #   RarReader (solid): single `unrar p` pipe demultiplexed per member.
    for member in self._iter_members():
        if member.type == MemberType.FILE:
            yield member, self._open_member(member)
        else:
            yield member, None
```

`open()` and `read()` in the ABC add link-following on top of `_open_member()`. Chains are
followed recursively with **cycle detection** via a visited member-id set (there is no
fixed depth limit); a missing target raises `LinkTargetNotFoundError`; hardlinks always
resolve to an **earlier** member (the TAR model), so they resolve in a single forward pass:
```python
def open(self, member: str | ArchiveMember,
         _seen: frozenset[int] = frozenset()) -> BinaryIO:
    if isinstance(member, str):
        member = self[member]
    if member.type in (MemberType.SYMLINK, MemberType.HARDLINK) and member.link_target:
        if member.member_id in _seen:
            raise ReadError(f"Link cycle detected at '{member.name}'")
        target = member.link_target_member or self.get(member.link_target)
        if target is None:
            raise LinkTargetNotFoundError(
                f"Link target '{member.link_target}' not in archive")
        return self.open(target, _seen=_seen | {member.member_id})
    return self._open_member(member)

def stream_members(
    self,
    members: MemberSelector | None = None,   # collection of members/names or predicate
) -> Iterator[tuple[ArchiveMember, BinaryIO | None]]:
    # `members` SELECTS which members to yield. There is intentionally NO transform
    # `filter` here: this generator yields the ORIGINAL mutable ArchiveMember so the
    # backend can keep filling late-bound fields (final size/CRC, data-stored
    # link_target) in place on the object the caller holds. A MemberFilter returns a
    # .replace() COPY; applying it here would yield that copy while the backend went on
    # updating the original — the caller's object would never see the late values.
    # Transformation therefore lives at the sinks that consume the stream:
    # extract_all() and writer.add_members() (each applies it on a transient copy while
    # the original supplies accurate limits/metadata). Streams are opened lazily, so
    # unselected members cost nothing.
    ...
```

`add_members()` in `ArchiveWriter` calls `reader.stream_members()` so conversions always take the bounded-memory streaming path.

Everything else (`__iter__`, `__getitem__`, `read`, `open`, `stream_members`, `extract`, `extract_all`, `members`) is implemented once in the ABC.

### 2.4 Lazy member materialization

→ see SPEC.md §3.2 / openspec `archive-reading`, `access-mode-and-cost`

`__iter__` calls `_iter_members()` directly — a generator that never loads all members.

`members()` and `__len__` force materialization:
```python
def members(self) -> list[ArchiveMember]:
    if self._members_cache is None:
        self._members_cache = list(self._iter_members())
    return self._members_cache
```

After materialization, `__iter__` returns `iter(self._members_cache)` for efficiency (avoids re-reading on second iteration).

**Streaming guard:** if the reader was opened with `streaming=True` (forward-only), materialization is forbidden. Calling `.members()` or `__len__` raises `UnsupportedOperationError` with a clear message.

### 2.5 PeekableStream for non-seekable sources

→ see SPEC.md §8.3 / openspec `format-detection`

```
┌──────────────────────────────────────────────────────────────┐
│  PeekableStream                                              │
│  ┌─────────────────────┐    ┌────────────────────────────┐  │
│  │ buffer: bytearray   │    │ underlying: RawIO (socket) │  │
│  │ (first LIMIT bytes) │    │                            │  │
│  └─────────────────────┘    └────────────────────────────┘  │
│   └──► replayed on first read    └──► then transparently    │
│         by backend                     passed through        │
└──────────────────────────────────────────────────────────────┘
```

`PeekableStream` is only constructed when `source` is non-seekable. It wraps the stream, buffers the first `DETECTION_LIMIT` bytes (4 KiB by default, 32 KiB for ISO), exposes a `peek(n: int) -> bytes` method, and then presents itself as a regular `BinaryIO` to the backend. Reads drain from the buffer first, then fall through to the underlying stream.

**The opener owns the wrapping.** For a non-seekable source, `open_archive()` constructs
the `PeekableStream` **before** running detection and passes the **same** wrapper to both
detection and the backend. `detect_format()` itself never wraps or consumes the source: it
inspects bytes through `PeekableStream.peek(n)` (or, for paths and seekable streams, reads
and then `seek(0)`s back), so the peeked prefix is always still available to the backend.
The standalone `detect_format()` returns only a `FormatInfo`; a caller invoking it directly
on a raw non-seekable stream it intends to keep reading must pass a `PeekableStream` itself.

This is a standard "read-ahead buffer" pattern — the key property is that the backend never knows or cares whether the source was originally seekable.

### 2.6 Extraction as a separate, composable module

→ see SPEC.md §7 / openspec `safe-extraction`

`internal/extraction.py` implements the safe extraction coordinator. It has no deferred/pending state; both streaming and random-access extraction use the same unified forward pass driven by `_iter_with_data()`.

```python
class ExtractionCoordinator:
    def __init__(self, dest: Path, policy: ExtractionPolicy,
                 overwrite: OverwritePolicy, bomb_tracker: BombTracker): ...

    def run(
        self,
        members: Iterable[ArchiveMember],
        open_fn: Callable[[ArchiveMember], BinaryIO],
        hardlink_sources: dict[int, ArchiveMember],  # member_id → source member (pre-built)
    ) -> dict[Path, ArchiveMember]:
        """Single forward pass. Works identically in streaming and random-access mode."""
```

The `hardlink_sources` map is built before the pass starts by scanning the full member list (available in random-access mode) or the upcoming-members list where possible. It tells the coordinator: "this member's data will be needed for N hardlinks that follow it — make sure its path is recorded."

During the pass:
- **FILE / DIR / SYMLINK**: write immediately. Record `member_id → extracted_path`.
- **HARDLINK**: a hardlink target always **precedes** the link in archive order (the TAR model), so if its `extracted_path` is already recorded, `os.link` it; otherwise `shutil.copy2`. In streaming mode, TAR guarantees the target precedes the link — if the source was filtered out, that's an explicit error with a clear message. In random-access mode, if the source was **not** selected by the `members`/`filter`, the coordinator must **not** materialize it at its own final path (that would leak a file the caller excluded). Instead it writes the source's content to the **first selected link's** path and `os.link`s any further selected links to it; the unselected source name is never created. (Equivalently, an implementation may stage the content in a hidden temp inside `dest` — e.g. `dest/.archivey-tmp-<id>` — link the selected targets to it, then unlink the temp.) If no link to the source is selected either, its data is not extracted at all.
- After the pass: apply mtime/permissions to all extracted paths (best-effort, single `os.utime` / `os.chmod` loop).

This replaces the previous `ExtractionHelper` class and its pending/deferred state machine. No move-vs-link signaling. No `can_move_file` flag. No `pending_target_members_by_source_id` dict.

### 2.7 Symlinks: single-pass with post-creation check

→ see SPEC.md §7 / openspec `safe-extraction`

Symlinks don't need a second pass. They are written in archive order as encountered. After creating each symlink:

```
os.symlink(link_target, dest_path)
try:
    resolved = (dest_path.parent / link_target).resolve()
    escaped = not resolved.is_relative_to(dest.resolve())
except (OSError, RuntimeError):        # ELOOP / symlink loop / too many levels
    escaped = True                     # an unresolvable link is treated as an escape
if escaped:
    dest_path.unlink()
    raise SymlinkEscapeError(...)
```

This is simpler than deferred creation and catches escapes immediately rather than at the end of the run. It is safe because symlink creation is atomic on POSIX; the escape check happens before any follow-through reads. The `try/except` is essential: an adversarial cyclic-symlink archive (`a → b`, `b → a`) makes the OS reject the resolution with `ELOOP` (`OSError`, or `RuntimeError` on some platforms/versions), so the guard maps that to a safe `SymlinkEscapeError` rejection instead of letting an uncaught OS error abort the extractor.

The one edge case is a symlink whose target is a *later* member in the same archive (rare in practice). In streaming mode, this is treated the same as an escaped symlink — rejected with a clear error. In random-access mode, the check is deferred until all members are written (same final verification). The default `DATA` extraction filter already rejects most such patterns.

### 2.8 Test architecture

→ see SPEC.md §14 / openspec `testing-contract`

Tests are split into two tiers:

**Tier 1 — generated-on-demand** (the vast majority): archive content is declared as Python `ArchiveContents` / `FileInfo` specs, generated at test time into a per-session cache directory (keyed by a hash of the spec + creation parameters), and never committed to the repo. The `conftest.py` fixture handles generation and caching transparently.

```python
@pytest.mark.sample_archives(container=ContainerFormat.ZIP, configs=["default", "altlibs"])
def test_read_basic(sample_archive: SampleArchive, archivey_config):
    with open_archive(sample_archive.get_archive_path(), config=archivey_config) as ar:
        assert ar.get_members() == sample_archive.contents.expected_members()
```

The `sample_archive.contents` object is both the generation spec and the ground truth — no JSON needed for generated archives. Format-specific feature flags (`ArchiveFormatFeatures`) tell the assertion helper which fields to compare (e.g. rounded mtimes for `zipfile`-generated ZIPs, no dir entries for `py7zr`-generated 7z fixtures).

**Tier 2 — committed fixtures with JSON sidecars** (a small set, committed to `tests/fixtures/`):

- Archives that require a specific OS or unavailable tool to generate (Windows junctions, RAR created with exact version flags, malformed-but-valid-in-the-wild archives).
- Adversarial archives: hand-crafted zip bombs, path traversal attempts, corrupt headers. These are small binary files; committing them is cheap and they rarely change.
- For every committed archive `foo.rar`, a sidecar `foo.json` documents the expected member list. A single parametrized test `test_fixtures.py::test_committed_fixture` runs all of them.

```json
{
  "format": "RAR5",
  "members": [
    {"name": "dir/", "type": "DIR", "size": 0},
    {"name": "dir/file.txt", "type": "FILE", "size": 42,
     "mtime": "2023-01-15T12:00:00+00:00"}
  ]
}
```

**Cross-tool verification**: For any archive parseable by `7z l -slt` or `unrar lt`, CI can run these and compare the output against the parsed `ArchiveMember` fields. This is implemented as an optional pytest plugin (`--verify-with-7z`) so it doesn't require tool installation in all environments.

### 2.10 Filters as pure transform functions

→ see SPEC.md §7 / openspec `safe-extraction`

```python
# filters.py

def check_universal(member: ArchiveMember) -> None:
    """Raises FilterRejectionError if member violates universal constraints."""

def transform_strict(member: ArchiveMember) -> ArchiveMember:
    """Returns a member.replace()'d copy with permissions adjusted for STRICT policy."""

def transform_standard(member: ArchiveMember) -> ArchiveMember:
    """Returns a member.replace()'d copy with permissions adjusted for STANDARD policy."""

POLICY_TRANSFORMS: dict[ExtractionPolicy, Callable[[ArchiveMember], ArchiveMember]] = {
    ExtractionPolicy.STRICT:   transform_strict,
    ExtractionPolicy.STANDARD: transform_standard,
    ExtractionPolicy.TRUSTED:  lambda m: m,  # identity
}
```

The pipeline order is fixed: **`check_universal` first** (rejects illegal members),
**the policy transform second** (returns a sanitized copy), then an **optional user
filter last** (a `Callable[[ArchiveMember], ArchiveMember | None]` that returns a modified
member or `None` to skip it). Because `ArchiveMember` is mutable but caller-read-only,
every transform produces its edited member via `member.replace(...)` (copy-on-edit) — it
**never** mutates the member in place and never constructs a frozen replacement. The
original member the reader yields is left untouched.

**Original vs. transient copy — who sees what.** The coordinator builds exactly one
transient copy per member: `check_universal` runs on the original, then the policy
transform and the user filter produce the copy. The **copy** supplies the on-disk
*identity* — `name`, `mode`, timestamps, hence the destination path and the OS file
handle — and is discarded once the file is written. The **original** is what the
coordinator hands to `BombTracker.start_member()` and records in the `ExtractionResult`,
so the ratio check and reported metadata use accurate source values, including late-bound
`size`/CRC the backend fills in place as the data streams (a copy taken before reading
would never receive them). This is exactly why `stream_members()` yields originals and the
transform filter lives here, not in the reader's generator.

### 2.11 Error wrapping: per-library translators + central context stamping

→ see SPEC.md §6 / openspec `error-handling`

Exception translation has **two separable concerns**, neither scattered as manual
field-setting across the backends.

**1. Type translation is per-library, not per-format.** Each underlying library has its
own exception taxonomy (`zipfile.BadZipFile`, `tarfile.TarError`, `lzma.LZMAError`, the
`unrar` process errors, the crypto backend's errors, …). A small translator **per library**
maps those exceptions to the correct typed `ArchiveyError` subclass (`CorruptionError`,
`TruncatedError`, `EncryptionError`, …) by inspecting the exception type/payload. A library
shared across formats (e.g. `lzma`, used by both XZ and the native 7z reader) is translated
once and reused. Translators know nothing about the format, archive path, or member:

```python
# Maps one library's exceptions to typed ArchiveyErrors; sets no context fields.
@translate_library_errors(LZMA_TRANSLATOR)
def _read_block(self, ...): ...
```

**2. Context is stamped centrally.** The `ArchiveReader` ABC wraps the public operations
(listing, `open()`, `read()`, iteration, extraction) so that when an `ArchiveyError`
propagates, the base class fills in `source_format`, `archive_name`, and `member_name` from
context it already holds, then re-raises. Backends never set these fields by hand:

```python
# In the ArchiveReader ABC, around each public operation:
try:
    return op()
except ArchiveyError as exc:
    exc.source_format = exc.source_format or self.format
    exc.archive_name  = exc.archive_name  or self._archive_name
    exc.member_name   = exc.member_name   or current_member_name
    raise
```

This pattern means:
- `except ArchiveyError` catches all library errors uniformly.
- `except Exception` still shows the original traceback via `__cause__` (`raise … from exc`).
- No internal library exception leaks to the caller.

**Genuine non-decoding errors propagate unchanged.** Only exceptions originating from a
decoding library's own taxonomy are translated. An `OSError` from the filesystem or a
caller-supplied stream, `KeyboardInterrupt`, and `MemoryError` propagate **unchanged** —
the base reader may stamp context onto an `ArchiveyError` it is already re-raising, but it
MUST NOT convert an unrelated runtime exception into an `ArchiveyError`.

### 2.12 Cost Receipt computation

→ see SPEC.md §4.6 / openspec `access-mode-and-cost`

Each backend computes its `CostReceipt` in `open_read()`, **before** any heavy I/O. The
three axes are orthogonal: `listing_cost` (enumeration), `access_cost` (format layout), and
`stream_capability` (a property of the source bytes). `is_solid` lives on `ArchiveInfo`, not
on the receipt; the receipt carries `access_cost` and `solid_block_count` instead.

```
ZIP backend:
  → reads central directory (already required to open the ZIP)
  → ListingCost.INDEXED (EOCD / central directory parsed)
  → AccessCost.DIRECT (each member has an offset in central dir)
  → StreamCapability.SEEKABLE (zipfile required seek)

TAR.GZ backend:
  → ListingCost.REQUIRES_DECOMPRESSION (must inflate to reach member headers)
  → AccessCost.SOLID (gzip is a single stream)
  → StreamCapability.SEEKABLE or FORWARD_ONLY depending on the source bytes
    (a file seeks; a pipe is forward-only — this is a SOURCE property, not the layout)

plain .tar backend:
  → ListingCost.REQUIRES_SCANNING (walk 512-byte headers, no decompress)
  → AccessCost.DIRECT
  → StreamCapability.SEEKABLE on a file, FORWARD_ONLY on a pipe

7z backend (native):
  → reads the header block natively (fast, at start of file)
  → ListingCost.INDEXED
  → AccessCost.SOLID if any folder packs more than one file, else DIRECT
  → solid_block_count = folder count (from the parsed header)
  → ArchiveInfo.is_solid = True when any folder packs > 1 file
```

### 2.13 Optional dependencies and graceful degradation

→ see SPEC.md §9.1 / openspec `backend-registry`, `packaging-and-extras`

A library-backed optional read backend registers itself only inside a successful-import
guard, declaring its magic/extensions as **data** (no `detect(peek)` method). If the
dependency is absent the guard catches the `ImportError` and the format simply never
appears in `list_formats()` — import never crashes:

```python
# formats/iso_reader.py
try:
    import pycdlib
    _PYCDLIB_AVAILABLE = True
except ImportError:
    _PYCDLIB_AVAILABLE = False

class IsoReadBackend(ReadBackend):
    FORMATS = (ArchiveFormat.ISO,)
    EXTENSIONS = (".iso",)
    MAGIC = ((32769, b"CD001"),)          # declared as data; the central detector matches it
    OPTIONAL_DEPENDENCY = "pycdlib"
    def open_read(self, source, streaming, password, encoding, archive_name) -> ArchiveReader: ...

if _PYCDLIB_AVAILABLE:
    register_reader(IsoReadBackend)   # from archivey.internal.registry; only when usable
```

When an ISO file is detected but `pycdlib` is not installed, `reader_for_format()` raises
`UnsupportedFormatError` with the message:
> "ISO 9660 format detected but backend is not installed. Run: pip install archivey[iso]"

**7z and RAR reading degrade differently because they are native** (no import guard, always
registered):
- **7z reading** is native (stdlib `lzma`/`bz2`/`zlib`); only 7z *writing* is gated on
  `py7zr` (`[7z-write]`). A 7z write without the extra raises `UnsupportedOperationError`
  naming `[7z-write]`.
- **RAR reading** parses metadata natively; reading member *data* needs the external `unrar`
  binary, checked at read time — a data read without `unrar` raises `PackageNotInstalledError`
  naming the missing tool, while *listing* works without it.

---

## 3. Data Flow Diagrams

### 3.1 Opening an archive

```
archivey.open_archive("file.zip")
  │  (opener wraps a non-seekable source in PeekableStream, shared below)
  ▼
internal/detection.py: detect_format()
  │  peek first 4KiB (via the shared PeekableStream / seekable rewind)
  │  match magic bytes → ArchiveFormat.ZIP
  ▼
BackendRegistry.reader_for_format(ArchiveFormat.ZIP)
  │  find ZipReadBackend
  ▼
ZipReadBackend.open_read(source, streaming, ...)
  │  zipfile.ZipFile(source)  ← reads EOCD, central directory
  │  build CostReceipt
  │  build ArchiveInfo
  ▼
ZipReader (ArchiveReader)
  │  wraps zipfile.ZipFile instance
  │  lazy member iterator
  └─► returned to caller
```

### 3.2 Sequential iteration

```
with archivey.open_archive("archive.tar.gz") as ar:
    for member in ar:
        data = ar.read(member)
        ↑
        │
ArchiveReader.__iter__()
  └─► TarReader._iter_members()
        │  tarfile.TarFile.next() — reads one header block
        │  maps TarInfo → ArchiveMember (mutable dataclass)
        └─► yield ArchiveMember

ArchiveReader.read(member)
  └─► TarReader._open_member(member)
        │  tarfile.TarFile.extractfile(...)
        └─► returns BinaryIO  →  .read()
```

### 3.3 Safe extraction flow

```
archivey.extract("untrusted.zip", "/safe/dest", policy=STRICT)
  │
  ▼
archivey.open_archive() → ZipReader
  │
  ▼
_extraction.extract_all(reader, dest, policy=STRICT, ...)
  │
  ├─► for member, stream in reader.stream_members(members):   # selector only; yields ORIGINAL
  │     # transform pipeline (coordinator-side): check_universal → policy transform → user filter
  │     _filters.check_universal(member)    ← path traversal, absolute path, null byte (on original)
  │     safe_member = user_filter(POLICY_TRANSFORMS[STRICT](member))  ← .replace(...) transient copy
  │     bomb.start_member(member)           ← ORIGINAL drives ratio/result (accurate late-bound size)
  │     extract_member(safe_member, stream, dest, ...)   ← copy supplies name/mode/path
  │           │
  │           ├─► handle overwrite policy
  │           ├─► mkdir parents
  │           ├─► copy chunks + BombTracker.count()
  │           └─► set mtime (best-effort)
  │
  └─► symlinks created in-pass with post-creation escape verification (§2.7)
```

### 3.4 Conversion pipeline

```
with archivey.open_archive("input.tar.gz") as reader, \
     archivey.create("output.zip") as writer:
    writer.add_members(reader)
         │
         ▼
    for member in reader:
        if member.type not in writer.SUPPORTED_TYPES:
            log.warning(...)
            continue
        stream = reader.open(member)
        writer.add_stream(stream, name=member.name,
                          size=member.size, modified=member.modified,
                          mode=member.mode)
        stream.close()
```

Memory usage: one member at a time, one chunk (1 MiB) at a time. No intermediate disk spooling unless the target format requires it (e.g. ZIP needs CRC before writing local header → uses a `SpooledTemporaryFile` per member up to `spool_size`, then streams).

---

## 4. Security Architecture

### 4.1 Defense in depth for path traversal

→ see SPEC.md §7.1 / openspec `safe-extraction`

Three independent layers:

1. **`check_universal()` on the ArchiveMember** (before any I/O): purely string-based check on `member.name` after normalization. Rejects `..` components, absolute paths, null bytes.

2. **Pre-extraction path computation**: `dest / member.name` is computed and checked with `.resolve()` — verifies the resolved absolute path starts with `dest.resolve()`.

3. **Post-symlink-creation check**: after `os.symlink()`, the created link's target is re-resolved with `Path.resolve()` to detect chained symlink attacks (where earlier members created symlinks that redirect later writes).

This three-layer approach catches:
- Layer 1: obvious traversals in the name string
- Layer 2: subtle path collisions via OS-specific normalization
- Layer 3: TOCTOU symlink attacks within the archive itself

### 4.2 Bomb detection architecture

→ see SPEC.md §7.3 / openspec `safe-extraction`

```python
class BombTracker:
    def __init__(self, max_bytes: int, max_ratio: float,
                 ratio_activation_threshold: int = 5 * 2**20):  # 5 MiB
        self._max_bytes = max_bytes
        self._max_ratio = max_ratio
        self._ratio_floor = ratio_activation_threshold
        self._total_bytes = 0          # cumulative across all members
        self._member_bytes = 0         # output of the current member
        self._member = None            # the ORIGINAL member (not a filter copy)

    def start_member(self, member: ArchiveMember) -> None:
        self._member = member
        self._member_bytes = 0

    def count(self, chunk_bytes: int) -> None:
        self._total_bytes += chunk_bytes
        self._member_bytes += chunk_bytes
        if self._total_bytes > self._max_bytes:
            raise ExtractionError(
                f"Extraction limit reached: {self._total_bytes} bytes > {self._max_bytes}"
            )
        # Ratio is per-member output / compressed_size, and only after the member's
        # output passes the activation floor — so a tiny but highly-compressible
        # legitimate file (10 B → 15 KiB = 1500:1) can't trip a false positive.
        cs = self._member.compressed_size if self._member else None
        if self._member_bytes > self._ratio_floor and cs and cs > 0:
            ratio = self._member_bytes / cs
            if ratio > self._max_ratio:
                raise ExtractionError(
                    f"Decompression ratio {ratio:.0f}:1 exceeds limit {self._max_ratio:.0f}:1"
                )
```

`BombTracker` is constructed once per `extract_all()` call. The cumulative `max_bytes`
limit spans all members; the per-member ratio is evaluated against each member's own
output and only after that output crosses `ratio_activation_threshold` (default 5 MiB).
The coordinator calls `start_member()` with the **original** member so `compressed_size`
and late-bound fields are accurate.

---

## 5. Trade-off Record

### 5.1 zipfile vs third-party ZIP library

→ see SPEC.md §10.1 / openspec `format-zip`

**Decision:** use stdlib `zipfile` for the core ZIP backend.

**Considered:** `zipfile38`, `python-libarchive-c`, `zipstream-new`.

**Rationale:** `zipfile` covers 99% of real-world ZIPs and has no dependencies. Its metadata handling for Unix extra fields (UID/GID, permissions) is usable. The main gaps (Zip64 edge cases, ZIP64 data descriptors) are known and can be worked around. `python-libarchive-c` would give C-speed extraction but introduces a native dependency that complicates packaging on Windows.

**If needed later:** an optional `[fast]` extra with `python-libarchive-c` could be added as a drop-in replacement backend.

### 5.2 Mutable stdlib dataclass vs attrs/pydantic for ArchiveMember

→ see SPEC.md §4.4 / openspec `archive-data-model`

**Decision:** a **mutable** stdlib `@dataclass` with a `.replace()` copy-on-edit method.

**Considered:** `attrs`, `pydantic`; and a frozen stdlib dataclass.

**Rationale (no heavy deps):** `pydantic` adds validation (good) but is a heavy dependency
and adds runtime overhead for every member construction; `attrs` is cleaner but also a
dependency. Since Archivey aims for zero core dependencies, stdlib `dataclass` is the
correct choice. Validation happens in the backend before/while building the member, not on
the model itself.

**Rationale (mutable, not frozen — reversed from an earlier draft):** the model must be
**mutable** so the library can fill late-known fields (final `size`/CRC for gzip and ZIP
data-descriptor entries, a `link_target` stored in the member's data) **in place** during a
single streaming pass — required under `streaming=True`, where the member cannot be
re-materialized and re-fetched (see §2.1). The contract makes this safe: callers treat
members as read-only, the library is the only writer, and any caller/filter edit goes
through `member.replace(...)`, which returns a copy. The cost is that `ArchiveMember` is
**unhashable** (a mutable value object), so callers key by `name`/`member_id` rather than
using members as set/dict keys.

### 5.3 Sync-only API

→ see SPEC.md §2 (Target Environment)

**Decision:** v1 is synchronous only.

**Rationale:** the underlying machinery (`zipfile`, `tarfile`, the native 7z/RAR readers, stdlib `lzma`/`bz2`/`zlib`, the `unrar` subprocess) is all blocking/synchronous. An async API on top of blocking I/O is worse than no async API — it gives the illusion of async without the benefit. If async is needed, the pattern is `asyncio.to_thread(archivey.extract, ...)`.

A future `archivey.asyncio` module using async generators is a clean add-on.

### 5.4 No appending / in-place modification

→ see SPEC.md §11 / openspec `archive-writing`

**Decision:** write is create-only; no in-place modify.

**Rationale:** ZIP append is technically possible (write a new central directory at the end) but is fragile and creates corrupt archives if interrupted. 7z has no append mode. TAR can be appended to (`a` mode) but the result is not a valid multi-stream archive. The correct workflow is "read old, write new" — the conversion pipeline makes this trivial.

### 5.5 Decompression bomb limits: defaults

→ see SPEC.md §7.3 / openspec `safe-extraction`

`max_extracted_bytes=2 GiB`, `max_ratio=1000`:
- 2 GiB is enough for most legitimate use cases and prevents gigabyte-class bombs.
- 1000:1 is extremely generous (typical DEFLATE is 3:1 to 10:1; text compresses to maybe 20:1). Even 42.zip's outer layer reaches ~391:1. This catches pathological ratios while not triggering on legitimate very-compressible data.
- Both are caller-configurable via `extract(..., max_extracted_bytes=..., max_ratio=...)`.

### 5.6 Native streaming 7z reader vs py7zr wrapper

→ see SPEC.md §10.4 / openspec `format-7z`

**Decision (v2):** a **native** streaming 7z reader — native header parse plus stdlib
`lzma`/`bz2`/`zlib` for decompression. `py7zr` is NOT a read dependency; 7z *reading* is
part of the zero-dependency core. `py7zr` is used only for 7z **writing** (optional
`[7z-write]` extra) and as a cross-validation oracle in the test suite.

**Why native is feasible.** The 7z header (signature header, packed-streams info, folders
and coder chains, substreams info, files info) is parsed directly, yielding the full member
list and the folder→file mapping in O(1) with no decompression. The decompressed bytes of a
folder are byte-contiguous — files are laid out sequentially and sizes are known from the
header — so the reader produces a member's stream by reading exactly `member.size` bytes, in
order, from the folder's decompressed output:

```
compressed folder stream → coder chain (reverse order) → pull decompressor →
  read exactly file_0.size bytes → yield as file_0 stream →
  read exactly file_1.size bytes → yield as file_1 stream →
  ...
```

**Codec coverage.** stdlib `lzma` in `FORMAT_RAW` mode natively implements LZMA1/LZMA2, the
simple BCJ branch-filter family, and Delta; `bz2` and `zlib` cover BZip2 and Deflate. These
are the core, zero-dependency codecs. The optional `[7z]` extra adds PPMd (`pyppmd`),
Deflate64 (`inflate64`), Zstd, and Brotli; AES decryption comes from the crypto backend
(`[crypto]`). **BCJ2** (a multi-stream filter) is **detected and rejected** with
`UnsupportedFeatureError` — never garbage output and never a fallback to a third-party
reader.

**Streaming, not caching.** Decoding is true pull-based streaming: a folder is decoded once
and its members are yielded as the decompressor produces bytes, with peak memory bounded by
the decompressor's working set — no per-folder `SpooledTemporaryFile` cache. (This replaces
the earlier "py7zr + lazy per-folder caching for v1, native in Phase 2" plan — native is the
v2 plan.) Random `ar.open()` of a member inside a solid folder re-decodes the folder from
its start (it MAY cache the decoded folder for repeated access to the same folder).

### 5.7 Native RAR metadata parsing + system unrar vs rarfile wrapper

→ see SPEC.md §10.5 / openspec `format-rar`

**Decision (v2):** parse RAR4/RAR5 metadata **natively** (no `rarfile` dependency) and
delegate the proprietary RAR decompression to the system `unrar` binary, which remains the
required runtime dependency for reading member *data*. RAR is read-only. `rarfile` is used
only as a cross-validation oracle in the test suite.

**Rationale.** Because metadata is parsed natively, members can be **listed without
`unrar`** — only reading bytes needs it. For solid sequential iteration the reader runs a
single `unrar p -inul <archive>` subprocess and demultiplexes its stdout into per-member
streams using the header-provided sizes (O(archive_size) total, one subprocess — not one
per member), validating each member's CRC32/Blake2sp incrementally. Stored (uncompressed,
unencrypted) members are served directly as raw bytes without `unrar`. RAR5 header
encryption is decrypted **natively** via the crypto backend (`[rar]`/`[crypto]`), so even a
header-encrypted archive can be listed without `unrar`.

**Rolling a full native RAR decompressor is out of scope:** the RAR format is proprietary
and documented only through reverse engineering; the reference implementation is the `unrar`
tool itself, which is why it stays the decompressor for member data.

---

## 6. Dependency Matrix

| Extra | Package / tool | Version floor | Purpose |
|-------|----------------|---------------|---------|
| (core) | zipfile | stdlib | ZIP read/write |
| (core) | tarfile | stdlib | TAR read/write |
| (core) | gzip, bz2, lzma, zlib | stdlib | single-file compressors |
| (core) | native parser + stdlib `lzma`/`bz2`/`zlib` | stdlib | **7z reading** (LZMA1/LZMA2/BCJ/Delta/Deflate/BZip2) |
| (core) | native RAR4/RAR5 metadata parser | stdlib | **RAR listing** (no `unrar` needed to list) |
| (system) | `unrar` binary on PATH | — | RAR member **data** reads (decompressor) |
| `[7z]` | `pyppmd`, `inflate64`, `zstandard`, `brotli` | — | optional 7z codecs: PPMd, Deflate64, Zstd, Brotli |
| `[7z-write]` | `py7zr` | ≥0.20 | 7-Zip **writing** only (reading is native) |
| `[crypto]` | `cryptography` | — | AES decryption (7z AES, RAR5 header encryption) |
| `[iso]` | `pycdlib` | ≥1.14 | ISO 9660 read |
| `[zstd]` | `zstandard` | ≥0.21 | Zstandard `.zst` and `.tar.zst` |
| `[lz4]` | `lz4` | — | LZ4 `.lz4` and `.tar.lz4` |
| `[seekable]` | `rapidgzip`, `indexed_bzip2` | — | fast seekable random access into gzip/bzip2 streams |
| `[all]` | all above | — | Everything |

Dev/test extras: `pytest`, `pytest-cov` (coverage **report only — no gate**), `pyrefly`,
`ty` (type-checking is Pyrefly + ty; the library is kept clean on both, no mypy), `ruff`,
`hypothesis`. The dev group also
pins `py7zr` and `rarfile` purely as **cross-validation oracles** — they are not runtime
read dependencies (7z/RAR reading is native).

---

## 7. Performance Notes

### 7.1 ZIP central directory caching

→ see SPEC.md §10.1 / openspec `format-zip`

`zipfile.ZipFile` reads the central directory on `__init__`. The ZIP backend does not re-read it. Member-name lookup is `O(1)` (the ZIP central-directory name index) via an internal dict (`self._zf.NameToInfo`).

### 7.2 TAR sequential read

→ see SPEC.md §10.2 / openspec `format-tar`

TAR backends in streaming mode (`r|gz`) read blocks of 512 bytes and yield `TarInfo` objects. They never seek backward. The Python `tarfile` module handles this internally; the backend just iterates.

For random access on a compressed TAR (`.tar.gz` etc.), there is no efficient option — the backend materializes a sorted list of `(offset, TarInfo)` tuples by doing a full streaming scan once, then uses those offsets for subsequent random access (requiring seeking in the decompressed stream — only possible for plain `.tar` without compression wrapper). For compressed TARs, random access requires decompressing from the start each time — this is reported via `AccessCost.SOLID`.

### 7.3 7z backend — native streaming folder decode

→ see SPEC.md §10.4 / openspec `format-7z`

The 7z backend is native: the header is parsed natively and decompression is driven through
stdlib `lzma`/`bz2`/`zlib` (plus optional `[7z]`/`[crypto]` codecs). There is **no**
`SpooledTemporaryFile` per-folder cache and no push-model background thread/queue.

**Folder-to-file mapping** comes from the natively parsed header: each member knows its
containing folder, and the substreams info gives the file count per folder and the
contiguous layout of files within the folder's decompressed output.

**`_iter_with_data()` / `stream_members()` — progressive folder decode, bounded memory.** A
solid folder is decoded **once** and its members are yielded **in order as the decompressor
produces bytes** — the reader reads exactly `member.size` bytes per member from the folder's
decompressed stream, yields that member's stream, then advances. Peak memory is the
decompressor's working state plus one in-flight chunk, **not** the whole folder. The CRC32
(`hashes["crc32"]`) is verified incrementally as bytes are read; a mismatch raises
`CorruptionError`.

**`_open_member()` — random access into a solid folder.** A random `ar.open()` for a member
inside a solid folder **re-decodes the folder from its start** and skips to the member,
emitting a `logging.WARNING` advising `stream_members()` for full sequential passes. The
library MUST NOT hold a growing cache of decoded block data released only at `close()`; it
MAY cache a single decoded folder for repeated access to members of that same folder. For
single-file folders (`AccessCost.DIRECT`) there is no re-decode penalty.

**`_iter_members()`** is pure metadata from the parsed header — O(1), no decompression.

**Coder chains** are applied in reverse coder order (e.g. `AES → LZMA2` means decrypt, then
decompress). **BCJ2** is detected and rejected with `UnsupportedFeatureError`.

### 7.4 RAR backend — native metadata + streamed unrar

→ see SPEC.md §10.5 / openspec `format-rar`

Metadata is parsed natively (RAR4/RAR5), so listing needs no `unrar`. Member **data** comes
from the system `unrar` binary.

- **Solid sequential iteration** (`stream_members()`): a **single** `unrar p -inul <archive>`
  subprocess is run and its stdout is **demultiplexed** into per-member streams using the
  header-provided sizes — O(archive_size) total, one subprocess, not one per member. Each
  member's checksum (CRC32 or Blake2sp, per `hashes`) is validated incrementally as it is
  read.
- **Stored members** (uncompressed, unencrypted) are served directly as raw bytes without
  invoking `unrar`.
- **Non-solid random access** reads just that member's data via `unrar` — O(member_size).
- **Solid random access** MAY extract once with `unrar x` into a temporary directory and
  serve subsequent reads from disk, cleaned up on `close()`.

A lower-priority, benchmark-gated optimization MAY build a temporary single-file RAR for a
small member and run `unrar` on that smaller archive; it is adopted only if measured to help
and MUST be byte-identical to the direct `unrar` path. If `unrar` is required but absent from
PATH, the reader raises `PackageNotInstalledError` naming `unrar`.

### 7.5 Chunk size for extraction

→ see SPEC.md §7 / openspec `safe-extraction`

Default chunk size is 1 MiB (1 048 576 bytes). This is a balance between:
- Too small: excessive system call overhead.
- Too large: excessive peak memory usage.

The chunk size is passed through to `shutil.copyfileobj(src, dst, length=CHUNK_SIZE)`.

---

## 8. Link-Following in ArchiveReader

→ see SPEC.md §3.2 / openspec `archive-reading`

Archive formats handle links differently at the library level:

| Format | hardlink `open()` | symlink `open()` |
|--------|------------------|-----------------|
| TAR | tarfile follows automatically — `extractfile()` returns data of linked file | tarfile follows automatically |
| RAR5 | native `file_redir` maps `RAR5_XREDIR_HARD_LINK` / `FILE_COPY` to the target member | symlink stored with the target path as content (resolved by the ABC layer) |
| ZIP | no hardlink concept | symlink stored as regular file with target path as content |
| 7z | no hardlink concept | symlink stored with metadata; content is target path |

The ABC layer adds uniform link-following on top, catching the ZIP/7z/RAR symlink cases. Backends that already follow links internally (TAR hardlinks, RAR5 hardlinks/file-copies) do so at a lower level — the ABC-level check is a no-op for those (the result is already the target's data, not the link path).

Cycle handling in the ABC `open()` uses **cycle detection via a visited member-id set**
(passed down the recursive resolution) rather than a fixed depth cap: an acyclic link chain
of any length resolves, and only an actual cycle raises a `ReadError`. A missing target
raises `LinkTargetNotFoundError`. Hardlink targets are always **earlier** members (the TAR
model), so a hardlink resolves during a single forward pass.
