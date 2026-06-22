# Single-File Compressor Format Behavior — delta (Phase 3)

This change fixes the *structure* of the single-file support (it is one multi-format
backend, not a backend class per compressor) and reconciles two behavioral details with
the actual data model: the gzip stored filename has no `raw_filename` field to live in,
and the access cost of a one-member archive is `DIRECT`, not `SOLID`. The remaining
behavioral requirements (member naming, per-format size rules) are unchanged.

## ADDED Requirements

### Requirement: A single multi-format backend serves every single-file compressor

The system SHALL implement single-file compressor reading as **one** `ReadBackend`
(`SingleFileBackend`) whose `FORMATS` tuple lists every standalone-stream codec, not a
separate backend class per format. The backend is codec-agnostic: it infers the member
name and metadata shell, then delegates decompression to the `compressed-streams`
codec layer resolved from the member's stream codec. This keeps the per-format logic to
a small set of **per-codec metadata hooks** rather than parallel reader classes, and
means a newly added standalone codec becomes readable by registering the codec, adding
its `ArchiveFormat`/`StreamFormat` enum value, and adding its detection entry — with no
new backend code.

- The per-codec metadata hooks SHALL be a dispatch table keyed by codec, not an
  `if format == …` chain. Each hook fills the format-specific fields the capability
  already specifies: gzip's `FNAME` → `extra["gzip.original_filename"]` + `raw_name`
  (and optional mtime); xz/zst header size; lz4 frame size; lzip trailer size; and the
  size-availability rules
  (`gz` always `None`; `bz2`/`zlib`/`br`/`Z` `None` until full decompression). A codec
  with no extra metadata simply registers no hook.
- The decodability of any single-file format follows from its **codec backend's**
  availability (per `backend-registry`'s compositional support), not from a
  per-format backend's presence: the `SingleFileBackend` itself is always registered;
  a format whose sole codec backend is missing is reported as support `NONE`.

#### Scenario: one backend reads multiple compressors

- **WHEN** `.gz`, `.bz2`, and `.xz` sources are opened
- **THEN** each is served by the same `SingleFileBackend` instance class (its `FORMATS` includes GZIP, BZIP2, and XZ), each yielding exactly one `FILE` member with the correct per-codec metadata

#### Scenario: a new standalone codec needs no new backend

- **WHEN** a new standalone codec is added to the `compressed-streams` registry with a matching `ArchiveFormat`/`StreamFormat` value and a detection entry
- **THEN** that format is readable as a single-file archive through the existing `SingleFileBackend` without adding a new `ReadBackend` subclass
- **AND** its availability is reported by `format_availability()` from the new codec backend's presence

## MODIFIED Requirements

### Requirement: Surface the gzip stored filename

The gzip format optionally records the original filename in its header (the `FNAME`
field). When present, the system SHALL expose the **decoded** value in
`member.extra["gzip.original_filename"]` and preserve the **undecoded** bytes in
`member.raw_name`. (`ArchiveMember` has no `raw_filename` field; the earlier spec text
referred to one that does not exist.) By default the member `name` is still inferred
from the *source* filename (stripping the `.gz` extension); the embedded name is not
automatically trusted as the logical name, since it may disagree with the container
filename. A configuration option MAY direct the reader to prefer the gzip-stored name
for `name`. The other single-file compressors (BZ2, XZ, ZST, LZ4, LZIP, ZLIB, BR, Z)
carry no embedded filename, so they set neither field from header data.

#### Scenario: gzip with a stored filename

- **WHEN** a `.gz` stream whose header carries `FNAME = "report.csv"` is opened from a path like `archive.gz`
- **THEN** `member.extra["gzip.original_filename"]` is `"report.csv"` and `member.raw_name` holds its undecoded bytes, while `member.name` remains `"archive"` (derived from the source filename) by default

#### Scenario: gzip without a stored filename

- **WHEN** a `.gz` stream has no `FNAME` header field
- **THEN** `member.extra` has no `"gzip.original_filename"` key and `member.name` is derived from the source filename

### Requirement: Report single-file compressor format properties

The system SHALL expose the following cost and capability properties for every opened
single-file compressor archive:

| Property | Value |
|----------|-------|
| Listing cost | `INDEXED` — exactly one member, always |
| Access cost | `DIRECT` — a single member has no inter-member (solid-block) dependency |
| Supports write | Yes |
| Requires seek | No (except unix-compress `.Z`, which needs a seekable source) |

The access cost is `DIRECT` because the archive holds a single member, so the
`AccessCost.SOLID` notion ("reading member N may require decompressing earlier members")
does not apply. Whether the member's decompressed *stream* supports cheap random access
(xz block index, bzip2 via `indexed_bzip2`, gzip via `rapidgzip`, seekable-zstd) is a
property of the **member stream** provided by `seekable-decompressor-streams`, surfaced
when the stream is opened — not a field of the archive-level `CostReceipt`.

#### Scenario: CostReceipt on open

- **WHEN** a GZ, BZ2, XZ, ZST, LZ4, LZIP, ZLIB, BR, or Z archive is opened
- **THEN** `cost.listing_cost` is `ListingCost.INDEXED` and `cost.access_cost` is `AccessCost.DIRECT`

#### Scenario: unix-compress requires a seekable source

- **WHEN** a `.Z` (unix-compress) archive is opened from a non-seekable source
- **THEN** `StreamNotSeekableError` is raised, while the other single-file formats open successfully from a non-seekable source
