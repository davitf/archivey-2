# 7-Zip Archive Support (native reader; py7zr for writing)

## Purpose

Archivey reads 7-Zip archives with a native, zero-dependency reader: the header
is parsed natively and decompression is driven through Python's standard library
— `lzma` in `FORMAT_RAW` mode covers LZMA1, LZMA2, the simple BCJ branch-filter
family, and Delta, while `bz2` and `zlib` cover BZip2 and Deflate. This yields
true pull-based streaming with none of the background-thread/queue or per-folder
spooling that a push-based library backend requires. The `py7zr` library is NOT a
read dependency; it is used only to provide 7z *writing* (optional `[7z-write]`
extra) and as a cross-validation oracle in the test suite.

> Provenance: the native-reader direction and its key feasibility finding (stdlib
> `lzma` `FORMAT_RAW` natively implements LZMA1/LZMA2 + the BCJ family + Delta)
> come from the `archivey-dev` `sevenzip-native-reader` exploration, distilled in
> COMPARISON.md §3 and ARCHITECTURE.md §5.6.

## Requirements

### Requirement: Declare format properties

The system SHALL expose the following properties for the 7-Zip backend:

| Property | Value |
|----------|-------|
| Read dependency | None — native parser + stdlib `lzma`/`bz2`/`zlib` (7z **reading** is part of the zero-dependency core) |
| Write dependency | `py7zr` (optional `[7z-write]` extra) |
| Listing cost | O(1) — header parsed natively upfront |
| Access cost | SOLID when a folder packs multiple files; DIRECT for single-file folders |
| Supports write | Yes, via `py7zr` (`[7z-write]`) |
| Requires seek | Yes |

#### Scenario: listing members of a 7z archive

- **WHEN** a caller opens a 7-Zip archive
- **THEN** the native parser reads the header region and the full member list is available in O(1), decompressing no file data
- **AND** no third-party library is imported for reading

#### Scenario: opening a 7z archive from a non-seekable source

- **WHEN** the source stream does not support seeking
- **THEN** the backend rejects the open with an appropriate error, because `Requires seek` is `True`

---

### Requirement: Parse the 7-Zip header natively

The system SHALL parse the 7z structure natively — signature header, packed-streams
info, the unpacked folders and their coder chains, substreams info, and files info
— without any third-party library. This produces the full member list and the
folder→file mapping in O(1), decompressing no file data. Each folder's file count
and the contiguous layout of files within the folder's decompressed output are
derived from the substreams info.

#### Scenario: member list and folder mapping from the header

- **WHEN** a 7-Zip archive is opened
- **THEN** the backend produces every member's metadata and the mapping of members to their containing folder purely from the parsed header, without decompressing any folder

---

### Requirement: Decode folders natively via stdlib codecs

The system SHALL decode each folder's coder chain by composing standard-library
decompressors: LZMA1/LZMA2, the simple BCJ filters (x86 / ARM / ARMT / PPC /
SPARC / IA64), and Delta via `lzma` `FORMAT_RAW` filters; BZip2 via `bz2`; Deflate
via `zlib`; and STORED as a pass-through. Files within a folder are laid out
contiguously in the decompressed output, so the backend produces a member's stream
by reading exactly `member.size` bytes, in order, from the folder's decompressed
byte stream.

#### Scenario: member compressed with a BCJ + LZMA2 chain

- **WHEN** a member lives in a folder coded as BCJ-over-LZMA2
- **THEN** the backend composes the stdlib `lzma` `FORMAT_RAW` filter chain, decodes the folder, and returns bytes identical to the original file content

---

### Requirement: Reject unsupported codecs explicitly

The system SHALL raise a clear error that names the codec when a folder uses a
codec outside the natively supported set — notably **PPMD** and **BCJ2**, which
are not available through the standard library (BCJ2 is a multi-stream filter not
implemented by `lzma`). The library MUST NOT silently return incorrect data and
MUST NOT fall back to a third-party reader.

#### Scenario: PPMD-compressed member

- **WHEN** a member's folder is compressed with PPMD
- **THEN** the backend raises an error naming PPMD as an unsupported codec, rather than returning data

#### Scenario: BCJ2-filtered member

- **WHEN** a member's folder uses the BCJ2 filter
- **THEN** the backend raises an error naming BCJ2 as an unsupported codec

---

### Requirement: True pull-based streaming with bounded memory

The system SHALL provide `stream_members()` as a true pull stream: each folder is
decoded once, its members yielded in archive order as the decompressor produces
bytes, with no buffering of the whole folder and no background thread or queue.
Peak memory is bounded by the decompressor's working set rather than the folder's
uncompressed size. For random `ar.open()` of a member inside a solid folder, the
backend decodes the folder from its start; it MAY cache the decoded folder so that
repeated access to members of the same folder is served without re-decoding.

#### Scenario: streaming a solid 7z archive

- **WHEN** a caller iterates a solid 7-Zip archive with `stream_members()`
- **THEN** each folder is decoded once and its members are yielded as a pull stream, with peak memory bounded by the decompressor working set, not the folder size

#### Scenario: random access into a solid folder

- **WHEN** `ar.open(member)` is called for a member inside a multi-file folder
- **THEN** the backend decodes the folder from its start to produce the member's bytes, optionally caching the decoded folder for subsequent access

---

### Requirement: Report solid block metadata in CostReceipt

The system SHALL populate `CostReceipt` from the natively parsed header:
`solid_block_count` is the number of folders, and `is_solid` is `True` when any
folder packs more than one file.

#### Scenario: reporting cost for a solid 7z archive

- **WHEN** a 7-Zip archive contains folders that pack multiple files
- **THEN** `CostReceipt.is_solid` is `True` and `CostReceipt.solid_block_count` reflects the folder count from the header

#### Scenario: reporting cost for a non-solid 7z archive

- **WHEN** every folder packs exactly one file
- **THEN** `CostReceipt.is_solid` is `False` and `CostReceipt.access_cost` is `DIRECT`

---

### Requirement: Map compression chain to CompressionMethod

The system SHALL map each folder's natively parsed coder chain to a
`tuple[CompressionMethod, ...]` on every `Member`, modelling the filter chain in
order (e.g. `(CompressionMethod(BCJ), CompressionMethod(LZMA2))`).

#### Scenario: member with a BCJ + LZMA2 filter chain

- **WHEN** a member's folder uses a BCJ pre-filter followed by LZMA2
- **THEN** `member.compression` reflects the full chain in order as `CompressionMethod` values

---

### Requirement: Represent absent POSIX metadata as None

The system SHALL set `mode`, `uid`, and `gid` to `None` when the 7-Zip archive
does not include a POSIX metadata attribute block — never a guessed default.

#### Scenario: 7z archive created without a POSIX attribute block

- **WHEN** a 7-Zip archive lacks a POSIX metadata attribute block
- **THEN** `member.mode`, `member.uid`, and `member.gid` are all `None`

---

### Requirement: Provide 7-Zip writing via py7zr

The system SHALL provide 7-Zip *writing* through `py7zr`, gated behind the
optional `[7z-write]` extra. Reading MUST NOT depend on `py7zr`. If a 7z write is
attempted without `[7z-write]` installed, the system SHALL raise a clear error
indicating the extra is required.

#### Scenario: writing a 7z archive with the extra installed

- **WHEN** `archivey.create(dest, ArchiveFormat.SEVEN_Z)` is called and `[7z-write]` is installed
- **THEN** the archive is written using `py7zr`

#### Scenario: writing a 7z archive without the extra

- **WHEN** a 7z write is attempted and `[7z-write]` is not installed
- **THEN** the system raises a clear error indicating the `[7z-write]` extra is required
