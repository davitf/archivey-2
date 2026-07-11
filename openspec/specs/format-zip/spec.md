# ZIP Format Behavior

## Purpose

The ZIP backend presents ZIP archives through the unified `ArchiveReader` / `ArchiveWriter` interface using Python's stdlib `zipfile` module. It reads the central directory on open for O(1) member listing, supports direct random access to any member, and can write archives in streaming mode using data descriptors.

## Requirements

### Requirement: Report ZIP format properties

The system SHALL expose the following cost and capability properties for every opened ZIP archive:

| Property | Value |
|----------|-------|
| Backend dependency | `zipfile` (stdlib) |
| Listing cost | O(1) — central directory is read first |
| Access cost | DIRECT — independent local file offsets |
| Supports write | Yes |
| Requires seek | Yes for read (central dir at EOF); No for streaming write |

#### Scenario: CostReceipt on open

- **WHEN** a ZIP archive is opened with `archivey.open_archive()`
- **THEN** the returned reader's `cost` property reports `ListingCost.INDEXED`, `AccessCost.DIRECT`, and `StreamCapability.SEEKABLE`

#### Scenario: Central directory lookup is O(1)

- **WHEN** `reader.get("some/member.txt")` is called on a ZIP reader
- **THEN** the lookup is satisfied via the reader's in-memory name→member map (built from the central directory) with no additional I/O

### Requirement: Map ZIP member metadata to the unified ArchiveMember model

The system SHALL map each `ZipInfo` entry to a `ArchiveMember` dataclass using the following field rules:

- `mode`: parsed from `external_attr >> 16`. If `external_attr == 0` and `create_system != 3` (Unix), `mode` is set to `None`.
- `modified`/`accessed`/`created`: layered by precedence, each layer overriding only the
  times it actually carries. Base: the DOS `date_time` tuple as a naive `datetime` (no TZ;
  local wall-clock, 2-second granularity; `None` for the year-1980 "no timestamp"
  sentinel). Above it: the NTFS extra field (`0x000A`) — three 64-bit FILETIMEs
  (modification/access/creation, 100 ns UTC ticks since 1601, zero = "not set"; written
  by Windows tools such as 7-Zip) — as timezone-aware UTC `datetime`s. Highest: the
  Extended Timestamp extra field (`0x5455`) — signed 32-bit Unix times, its flags byte
  signaling which of modification/access/creation are present — as timezone-aware UTC
  `datetime`s.
- `type`: inferred from `mode` if Unix, otherwise from `is_dir()` and symlink detection via extra field `0x000A` (NTFS) or `0x7875` (Unix UID/GID).
- `compression`: map `compress_type` integer to `CompressionMethod`.
- `is_encrypted`: set to `True` when `flag_bits & 0x1` is non-zero.

> **Phase 3 → 7 gap (member decode via stdlib zipfile).** Member *data* decompression
> currently goes through stdlib `zipfile`, which cannot decode deflate64/PPMd (or zstd
> before Python 3.14) even when the corresponding codec packages are installed —
> reading such a member raises `UnsupportedFeatureError`. Until Phase 7 wires the shared
> `compressed-streams` codec layer into ZIP member reads (see `openspec/project.md`),
> `format_availability(ZIP)` SHALL report **PARTIAL** regardless of optional codec
> installation (`backend-registry`); listing is unaffected. Installing `[7z]` / `[zstd]`
> alone does not unlock those member reads before Phase 7.
>
> The intended fix (Phase 7, alongside the 7z container codecs) keeps `zipfile` for the
> central directory but bypasses its decompressor for member data: locate the member's
> raw compressed bytes (local-header offset + a `SlicingStream` view) and decode them
> through the shared `compressed-streams` codec layer. `zipfile` exposes no decompressor
> plug-in point, so this raw-slice route — a first step toward the full native ZIP
> reader in `IDEAS.md` — is the mechanism; the Phase 7 change proposal specifies it.

#### Scenario: Unix mode from external_attr

- **WHEN** a ZIP entry has `create_system == 3` (Unix) and a non-zero `external_attr`
- **THEN** `member.mode` is set to `external_attr >> 16` (low 12 bits: permission bits)

#### Scenario: Non-Unix or missing mode

- **WHEN** a ZIP entry has `external_attr == 0` or `create_system != 3`
- **THEN** `member.mode` is set to `None`

#### Scenario: Extended Timestamp takes precedence over DOS date_time

- **WHEN** a ZIP entry carries an Extended Timestamp extra field (`0x5455`) with a modification time
- **THEN** `member.modified` is a timezone-aware UTC `datetime` derived from that Unix time, overriding the value from `date_time`

#### Scenario: NTFS timestamps used when no Extended Timestamp is present

- **WHEN** a ZIP entry carries an NTFS extra field (`0x000A`) with non-zero FILETIMEs and no `0x5455` field
- **THEN** `member.modified`/`accessed`/`created` are timezone-aware UTC `datetime`s derived from those FILETIMEs, overriding the value from `date_time`

#### Scenario: Encrypted entry detection

- **WHEN** a ZIP entry has `flag_bits & 0x1` set
- **THEN** `member.is_encrypted` is `True`

### Requirement: Handle non-seekable ZIP streams

The ZIP central directory resides at the **end** of the file, so a ZIP cannot be read from a non-seekable source (a pipe/socket) without first buffering it to seekable storage. Per the access-mode contract (`access-mode-and-cost`), the system SHALL raise `StreamNotSeekableError` at open time for a non-seekable ZIP source, advising the caller to buffer the source (save to disk or a `BytesIO`) and reopen, rather than buffering implicitly.

> **Reconcile when the ZIP backend lands (Phase 3).** The earlier design auto-spooled a non-seekable ZIP into a `tempfile.SpooledTemporaryFile` transparently (threshold `spool_max_size`, default 50 MiB; oversized → `ReadError`). That convenience conflicts with the decided rule that `streaming=False` **fails fast** on a source it cannot random-access and the library does **not** implicitly buffer. If transparent spooling is wanted back, it must return as an **explicit opt-in** (e.g. a `spool_max_size` argument), not the default. Finalize this when the backend is implemented.

#### Scenario: non-seekable ZIP fails fast

- **WHEN** a ZIP stream is opened from a non-seekable source (e.g. a network pipe) with the default `streaming=False`
- **THEN** `StreamNotSeekableError` is raised at open time, advising the caller to buffer the source and reopen

### Requirement: Reject multi-volume (split/spanned) ZIP archives with a clear error

The system SHALL detect multi-volume (split/spanned) ZIP archives and raise
`UnsupportedFeatureError` with a clear error rather than mis-reading the archive or
surfacing a cryptic stdlib `BadZipFile`. Unlike multi-volume 7z and RAR (which
Archivey joins — see `format-7z` and `format-rar`), the stdlib `zipfile` backend
cannot read a multi-volume ZIP. A ZIP **split** set (`name.z01`, `name.z02`, …, final
`name.zip`) or a **spanned** set (written across removable media) records each entry's
location as a *(disk-number, offset-within-disk)* pair; `zipfile` rejects the ZIP64
multi-disk locator outright, and naive concatenation of the segments is unreliable
(non-zero disk fields in the end-of-central-directory, a possible leading spanning
marker, and non-absolute offsets).

- Detection MAY use: a non-zero "number of this disk" / "disk where the central
  directory starts" field in the (ZIP64) end-of-central-directory record, a `disks > 1`
  ZIP64 EOCD locator, or being pointed at a `.z01`/`.zNN` segment.
- The error message SHOULD advise the caller to rejoin the volumes first
  (e.g. `zip -s 0 split.zip --out whole.zip`).
- Proper multi-volume ZIP support is deferred to a future **native ZIP reader**
  (see `IDEAS.md`), which can resolve *(disk, offset)* addressing across a
  concatenation of the segments.

#### Scenario: opening a split ZIP set is rejected

- **WHEN** `open_archive()` is given a multi-volume ZIP (a `.z01`…`.zip` split set, or any segment of one)
- **THEN** `UnsupportedFeatureError` is raised, advising the caller to rejoin the volumes first

#### Scenario: a ZIP declaring multiple disks is rejected cleanly

- **WHEN** a ZIP whose end-of-central-directory declares a non-zero disk number (or a ZIP64 locator with `disks > 1`) is opened
- **THEN** `UnsupportedFeatureError` is raised rather than a stdlib `BadZipFile`

### Requirement: Multi-candidate disambiguation for traditional ZipCrypto

For a member encrypted with traditional ZipCrypto (PKWARE), whose per-open password check
is a single verification byte while the authoritative check is the member's CRC-32 (and,
for a compressed member, completion of the decompressor), the ZIP reader SHALL confirm a
candidate that passes the byte check before accepting it whenever another candidate may be
tried under `archive-reading` → "Confirm candidates when a weak check permits retries".
Confirmation SHALL be bounded per `archive-reading` → "Bounded implicit temporary
storage": it SHALL NOT buffer plaintext proportional to the member size to memory or
temporary disk.

**Compressed members (DEFLATE / BZIP2 / LZMA).** A candidate is confirmed by decompressing
a bounded plaintext prefix (an internal constant on the order of 1 MiB of decompressed
output), discarding the output. A wrong ZipCrypto key feeds the decompressor
high-entropy garbage, which each stdlib codec rejects far within that bound (the
gibberish-rejection investigation in this change's tasks records the measured margins).
If the member's decompressed size is within the bound, the prefix read reaches EOF and
`zipfile`'s CRC check runs, making confirmation exact.

**STORED members.** No decompressor exists to reject garbage and only the full-stream
CRC-32 discriminates, so per-candidate full reads would cost one pass per candidate.
The reader SHALL disambiguate surviving candidates (those that pass the verification
byte) in a **single shared pass** over the member's ciphertext: decrypt the stream once
per candidate in parallel, accumulate each candidate's plaintext CRC-32 in constant
memory, and at EOF accept the candidate whose CRC matches the central directory value.
At most one extra full read of the member is performed in total, regardless of the
number of candidates. If more than one candidate's CRC matches (a CRC collision across
keystreams), the earliest match in candidate order is accepted.

A compressibility (or magic-byte) early-accept probe was considered and rejected: STORED
members are typically already-compressed media that look random to such heuristics, while
compressible plaintext is rarely stored uncompressed — so a probe would almost never
avoid the CRC pass in practice, and the multi-candidate ZipCrypto case is already niche.

**Returning the winner.** After confirmation the reader SHALL open a fresh stream with the
accepted password for the caller (ZIP requires a seekable source, so re-open is always
available) and SHALL record the password as known-good. The caller's stream retains the
format's ordinary read-time integrity checking — `zipfile` verifies the CRC-32 at EOF —
so acceptance by bounded confirmation never weakens the read-time contract relative to
the single-candidate path: plaintext that is wrong beyond the confirmed prefix still
surfaces as `CorruptionError` during the caller's read.

The candidate-confirmation failure set SHALL include only `zipfile.BadZipFile` whose
message identifies `"Bad CRC-32 for file ..."`, `zlib.error`, `lzma.LZMAError`, and
exactly the BZIP2 decoder's `OSError("Invalid data stream")`. Local-header mismatch, bad
local-header magic, overlap, and other structural `BadZipFile` failures SHALL remain
`CorruptionError` and stop candidate iteration. Other `OSError` values SHALL propagate
unchanged. Streams opened for a rejected candidate SHALL be closed before the next
candidate is tried.

If one or more candidates reach confirmation but no candidate succeeds, the reader SHALL
raise `EncryptionError` explaining that the passwords may be wrong or the encrypted member
may be corrupt. This is intentionally not a promise to distinguish those equivalent
failure observations. The reader SHALL never accept a candidate through a path that
bypasses the caller stream's ordinary read-time integrity check: candidate order alone,
neighbour-member affinity, or guess-with-warning are not acceptance signals. (The
bounded decompress prefix is an acceptance accelerator whose residual error is still
caught by the caller's EOF CRC check, so it does not bypass it.)

With one distinct static candidate, including duplicate copies of it, the reader SHALL
retain its normal lazy stream with no eager confirmation read. A read-time integrity
failure on that path SHALL be translated as corruption in the ordinary way.

#### Scenario: colliding wrong candidate is rejected for every stdlib ZIP codec

- **WHEN** a STORED, DEFLATE, BZIP2, or LZMA ZipCrypto member is opened with a wrong candidate that passes the verification byte followed by the correct candidate
- **THEN** confirmation rejects the wrong candidate and the reader returns a fresh stream opened with the correct candidate

#### Scenario: single candidate keeps the fast path

- **WHEN** a ZipCrypto member is opened with one distinct static candidate password
- **THEN** the reader does not perform a confirmation read; the member streams as before

#### Scenario: confirming a large compressed member is bounded

- **WHEN** a compressed ZipCrypto member much larger than the confirmation bound is opened with multiple candidates
- **THEN** confirmation decompresses at most the bounded prefix per candidate, retains no plaintext in memory or temporary storage, and the caller receives a fresh stream whose CRC is still verified at EOF by the ordinary read path

#### Scenario: stored members disambiguate with one shared CRC pass

- **WHEN** a STORED ZipCrypto member is opened with several candidates that pass the verification byte
- **THEN** one shared pass over the ciphertext computes every candidate's plaintext CRC-32 concurrently, the matching candidate is accepted and re-opened fresh for the caller, and no candidate's plaintext is buffered

#### Scenario: multiple stored CRC matches resolve by candidate order

- **WHEN** the single-pass STORED disambiguation finds two candidates whose plaintext CRC-32 both match the stored value
- **THEN** the earliest match in candidate order is accepted

#### Scenario: corruption beyond the confirmed prefix surfaces on the caller's read

- **WHEN** a candidate is accepted after bounded prefix confirmation but the member's data is corrupt beyond the confirmed prefix
- **THEN** the caller's read raises `CorruptionError` at the point the ordinary ZIP read path detects it — identical to the single-candidate behavior

#### Scenario: corrupt encrypted data cannot be distinguished from a collision

- **WHEN** multiple candidates are available and a candidate passes the verification byte but fails confirmation
- **THEN** if no candidate is confirmed, `EncryptionError` states that the passwords may be wrong or the member may be corrupt and returns no bytes

#### Scenario: unrelated OSError is not a candidate failure

- **WHEN** confirmation encounters an `OSError` other than BZIP2's exact `"Invalid data stream"` decoder message
- **THEN** that `OSError` propagates unchanged and the failed stream is closed

#### Scenario: structural BadZipFile is not password ambiguity

- **WHEN** opening an encrypted member reports a local-header or other structural `BadZipFile` failure
- **THEN** the reader raises `CorruptionError` immediately rather than trying another password or reporting candidate exhaustion

### Requirement: Support streaming ZIP write via data descriptor

The system SHALL support writing ZIP archives to non-seekable destinations using the data descriptor mechanism.

When writing, the backend sets `flag_bits |= 0x8` (data descriptor flag), which allows the CRC-32 and compressed/uncompressed sizes to be written after the file data rather than before. File size is therefore not required in advance from the caller.

#### Scenario: Streaming write without pre-known size

- **WHEN** `writer.add_stream(stream, name=...)` is called without a `size` argument
- **THEN** the ZIP backend writes the local file header with placeholder CRC and sizes, streams the data, and appends a data descriptor record with the actual CRC-32 and sizes
- **AND** the resulting ZIP file is valid and readable by standard ZIP tools
