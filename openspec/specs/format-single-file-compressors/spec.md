# Single-File Compressor Format Behavior (GZ, BZ2, XZ, ZST, LZ4, LZIP, ZLIB, BR, Z)

## Purpose

Single-file compressors (GZ, BZ2, XZ, ZST, LZ4, LZIP, ZLIB, BR, Z) are presented as a one-member pseudo-archive through the unified `ArchiveReader` / `ArchiveWriter` interface. The compressed stream is treated as an archive containing exactly one file, with the member name inferred from the source filename. This allows single-file compressed streams to participate in the same iteration, extraction, and conversion workflows as multi-member archives.

The set is the standalone-stream side of the `compressed-streams` codec table: every codec
that can stand alone as a bare `.ext` stream (no container) appears here, including
**unix-compress (`.Z`, LZW)** via the `uncompresspy` backend (the `[unix-compress]`
extra). unix-compress has two quirks: it requires a **seekable** source (the backend
decodes via random access) and gives **no truncation signal** — the `.Z` format has no
length or checksum trailer, so a cut stream simply yields fewer bytes with no error. Raw
Deflate and raw LZMA1/LZMA2 are **not** standalone formats: they carry no self-framing and
only ever appear inside a container (ZIP/7z), so they are reachable through
`compressed-streams` but not as single-file compressors here.
## Requirements
### Requirement: Present a single-file compressor as a one-member archive

The system SHALL present any GZ, BZ2, XZ, ZST, LZ4, LZIP, ZLIB, BR, or Z source as an archive containing exactly one `ArchiveMember` of type `MemberType.FILE`. No directory members are synthesized.

The member's name is inferred from the source filename as follows:

- If the filename ends in a **recognized single-file-compressor extension** — `.gz`, `.bz2`, `.xz`, `.zst`, `.lz4`, `.lz`, `.zz`, `.br`, `.Z` (case-insensitively) — that extension is **stripped** (e.g. `data.txt.gz` → `data.txt`).
- Otherwise the extension is **not** removed: it may be meaningful and unrelated to compression (the stream was identified by content/magic, not by a matching name), so the suffix `.uncompressed` is **appended** instead (e.g. `mystery.bin` → `mystery.bin.uncompressed`, `backup` → `backup.uncompressed`). Blindly stripping an arbitrary extension would discard real information and could yield an empty or misleading name.
- If no filename is available (e.g. an anonymous stream), the member name defaults to `"data"`.

The recognized compression extensions are exactly the standalone-stream extensions of the codecs in this capability; the combined `tar.gz`/`.tgz`-style names are a `format-detection` concern, not single-file member naming.

#### Scenario: ArchiveMember name inferred from filename

- **WHEN** a single-file compressor archive is opened from a path such as `data.txt.gz`
- **THEN** the single member's `name` is `"data.txt"` (the compression extension `.gz` is stripped)

#### Scenario: source extension is not a known compression extension

- **WHEN** a single-file compressor archive is opened from a path such as `mystery.bin` (identified as compressed by content, not by a `.gz`/`.xz`/… name)
- **THEN** the extension is left intact and the single member's `name` is `"mystery.bin.uncompressed"` (the original extension is not discarded)

#### Scenario: ArchiveMember name defaults when no filename is available

- **WHEN** a single-file compressor archive is opened from a non-seekable stream with no associated filename
- **THEN** the single member's `name` is `"data"`

#### Scenario: Exactly one member is present

- **WHEN** any GZ, BZ2, XZ, ZST, LZ4, LZIP, ZLIB, BR, or Z source is opened
- **THEN** iterating the reader yields exactly one `ArchiveMember`

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

### Requirement: Report member size with format-specific caveats

The system SHALL populate `member.size` (uncompressed size) according to format-specific limitations:

- **GZ:** `member.size` is `None`. The GZ format stores the uncompressed size modulo 2³², making it unreliable for files larger than 4 GiB. The field is never reported to avoid silently returning a wrong value.
- **BZ2, ZLIB, BR, Z:** `member.size` is `None` until the stream has been fully decompressed. These formats do not store the uncompressed size in their header; the size becomes known only after full decompression. (unix-compress (`.Z`) additionally has no trailer, so a truncated stream is indistinguishable from a complete one.)
- **XZ** and **ZST:** `member.size` may be available from the stream header if the encoder wrote it; otherwise `None`.
- **LZ4:** `member.size` may be available from the frame header's optional content-size field; otherwise `None`.
- **LZIP:** `member.size` is available — the lzip format records each member's uncompressed size in its trailer, which the seekable lzip backend reads cheaply.

#### Scenario: GZ member size is always None

- **WHEN** a `.gz` archive is opened
- **THEN** `member.size` is `None` for the single member

#### Scenario: BZ2 member size before full decompression

- **WHEN** a `.bz2` archive is opened and the member has not yet been fully decompressed
- **THEN** `member.size` is `None`

#### Scenario: BZ2 member size after full decompression

- **WHEN** a `.bz2` archive is opened and the member stream has been fully read to EOF
- **THEN** `member.size` may be updated to reflect the actual uncompressed byte count

### Requirement: Surface the gzip stored filename

The system SHALL surface the gzip stored filename — the optional `FNAME` header field —
when present: the **decoded** value in `member.extra["gzip.original_filename"]` and the
**undecoded** bytes in `member.raw_name`. (`ArchiveMember` has no `raw_filename` field;
the earlier spec text referred to one that does not exist.) By default the member `name` is still inferred
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

### Requirement: Per-codec metadata comes from the codec descriptor

The single multi-format `SingleFileBackend` SHALL obtain each format's metadata extraction
from its codec descriptor's metadata hook rather than a reader-local dispatch table, keeping
the reader codec-agnostic. The "one backend, per-codec hooks" structure SHALL be preserved —
only the hooks' home moves onto the descriptor — and the surfaced metadata (gzip `FNAME` →
`extra["gzip.original_filename"]` + `raw_name`, gzip mtime, xz/lzip decompressed size, and
the per-format size-availability rules) MUST be unchanged.

#### Scenario: gzip metadata extraction lives on the codec descriptor

- **WHEN** a `.gz` source with a stored `FNAME` and mtime is opened
- **THEN** `extra["gzip.original_filename"]` (Latin-1 decoded), `raw_name`, and `modified` are populated exactly as before, via the gzip descriptor's metadata hook rather than a reader method

#### Scenario: a codec with no extra metadata needs no hook

- **WHEN** a `.bz2` source (no header metadata) is opened
- **THEN** the member carries the default shell with `size` `None`, because its descriptor registers no metadata hook

### Requirement: A single multi-format backend serves every single-file compressor

The system SHALL implement single-file compressor reading as **one** `ReadBackend`
(`SingleFileBackend`) whose `FORMATS` tuple lists every standalone-stream codec, not a
separate backend class per format. The backend is codec-agnostic: it infers the member
name and metadata shell, then delegates decompression to the `compressed-streams`
codec layer resolved from the member's stream codec. Per-codec metadata and detection
data live on each codec's `StreamCodec` descriptor (see `compressed-streams`); the
backend derives its format tables from those descriptors rather than hand-listing them.
A newly added standalone codec becomes readable by registering one descriptor — with no
new backend code.

- The per-codec metadata hooks SHALL live on the codec descriptors, not a reader-local
  dispatch table. Each hook fills the format-specific fields the capability already
  specifies: gzip's `FNAME` → `extra["gzip.original_filename"]` + `raw_name`
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

- **WHEN** a new standalone codec descriptor is registered with a matching `ArchiveFormat`/`StreamFormat` value
- **THEN** that format is readable as a single-file archive through the existing `SingleFileBackend` without adding a new `ReadBackend` subclass
- **AND** its availability is reported by `format_availability()` from the new codec backend's presence

