# Single-File Compressor Format Behavior

## Purpose

Single-file compressors (GZ, BZ2, XZ, ZST, LZ4, LZIP, ZLIB, BR, Z) are exposed
as one-member pseudo-archives through the unified `ArchiveReader` /
`ArchiveWriter` interface. Each source contains exactly one file member whose
name is inferred from the source filename.

This capability is the standalone-stream side of `compressed-streams`. Raw
Deflate and raw LZMA1/LZMA2 are not standalone formats because they lack
self-framing; they appear only inside containers such as ZIP or 7z. Unix-compress
(`.Z`, LZW) is decoded natively in core, streams from non-seekable sources under
`streaming=True`, and cannot signal truncation because the format has no length or
checksum trailer.

## Related specs

| Spec | Relationship |
| --- | --- |
| `archive-reading` | One-member reader behavior, random vs streaming access modes |
| `access-mode-and-cost` | Listing/access cost and non-seekable legality |
| `compressed-streams` | Codec descriptors, decoder availability, metadata hooks |
| `format-detection` | Bare stream detection and combined TAR-compressor detection |
| `packaging-and-extras` | Optional codec extras such as Brotli, LZ4, Zstd |

## Requirements

### Requirement: Present each compressor as a one-member archive

The system SHALL present any GZ, BZ2, XZ, ZST, LZ4, LZIP, LZMA Alone, ZLIB, BR,
or Z source as an archive containing exactly one `ArchiveMember` of type
`MemberType.FILE`. No directory members SHALL be synthesized.

The member name SHALL be inferred from the source filename:

| Source filename | Member name |
| --- | --- |
| Ends in `.gz`, `.bz2`, `.xz`, `.zst`, `.lz4`, `.lz`, `.lzma`, `.zz`, `.br`, or `.Z` (case-insensitive) | Strip exactly that recognized compression extension |
| Has a filename but no recognized compressor extension | Append `.uncompressed`; do not strip arbitrary extensions |
| Anonymous stream | `data` |

Combined names such as `.tar.gz` / `.tgz` / `.tar.lzma` / `.tlz` are a
`format-detection` concern, not single-file member naming.

Raw Deflate and raw LZMA1/LZMA2 (`FORMAT_RAW`) remain container-only; LZMA Alone
is a framed standalone stream and is in scope here.

#### Scenario: one-member naming matrix

| Case | Expected |
| --- | --- |
| Open `data.txt.gz` | One file member named `data.txt` |
| Open `data.txt.lzma` | One file member named `data.txt` |
| Open compressed `mystery.bin` detected by content | One file member named `mystery.bin.uncompressed` |
| Open anonymous non-seekable stream with `streaming=True` | One file member named `data` |
| Iterate any supported single-file compressor | Exactly one file member is yielded |

### Requirement: Report single-file compressor properties

The backend SHALL expose these properties for every single-file compressor:

| Property | Value |
| --- | --- |
| Listing cost | `INDEXED`; exactly one member |
| Access cost | `DIRECT`; no inter-member dependency exists |
| Supports write | Yes |
| Requires seek | Random access (`streaming=False`) requires seek; forward-only `streaming=True` accepts non-seekable sources for every supported single-file codec including `.Z` |

Random access over a non-seekable source SHALL fail fast at open with
`StreamNotSeekableError`; the backend MUST NOT buffer an unbounded source to
simulate repeatable reads. Under `streaming=True`, every supported single-file
codec including unix-compress `.Z` SHALL stream from non-seekable sources.

Member-stream seekability is a stream-level property from index- or
accelerator-backed decoders (for example xz indexes, CLEAR seek points for
unix-compress, `indexed_bzip2`, `rapidgzip`, seekable zstd), not an archive-level
`CostReceipt` field.

#### Scenario: property matrix

| Case | Expected |
| --- | --- |
| Open any supported single-file compressor | `listing_cost=INDEXED`; `access_cost=DIRECT` |
| Non-seekable source with `streaming=False` | `StreamNotSeekableError` |
| Non-seekable source with `streaming=True` (including `.Z`) | Opens and `stream_members()` yields data |
| Seekable `.Z` with declared member-stream seekability | Member stream is seekable via CLEAR seek points |

### Requirement: Report member size with codec caveats

The system SHALL populate `member.size` according to codec metadata and
format-specific reliability limits:

| Codec | `member.size` behavior |
| --- | --- |
| GZ | Always `None`; stored ISIZE is modulo 2^32 and may be wrong |
| BZ2, ZLIB, BR, Z | `None` until full decompression; `.Z` also has no truncation signal |
| XZ, ZST | Header size when encoder wrote it; otherwise `None` |
| LZ4 | Frame content-size field when present; otherwise `None` |
| LZIP | Available from the trailer through the seekable lzip backend |
| LZMA Alone | 8-byte Alone header size when not the unknown marker (`0xFFFFFFFFFFFFFFFF`); otherwise `None` |

When a decoder learns the true uncompressed size after EOF, the member MAY be
updated to that byte count.

#### Scenario: size matrix

| Case | Expected |
| --- | --- |
| `.gz` opened | Single member size is `None` |
| `.bz2` before full decompression | Size is `None` |
| `.bz2` fully read to EOF | Size may update to actual uncompressed byte count |
| `.lz` opened through seekable lzip backend | Size is available from the trailer |
| Alone stream with known header size | `member.size` equals that size |
| Alone stream with unknown-size marker | Size is `None` until EOF may update it |
| Truncated `.Z` | Decoder may yield fewer bytes with no truncation error |

### Requirement: Surface gzip stored metadata without trusting it as the name

The system SHALL surface gzip `FNAME` when present: the decoded value appears in
`member.extra["gzip.original_filename"]` and the undecoded bytes appear in
`member.raw_name`. By default, `member.name` SHALL still come from the source
filename; embedded gzip names are not automatically trusted because they may
disagree with the container filename. A configuration option MAY prefer the
gzip-stored name for `member.name`. Other single-file compressors SHALL not set
these fields from header data because they carry no embedded filename.

#### Scenario: gzip metadata matrix

| Case | Expected |
| --- | --- |
| `.gz` with `FNAME="report.csv"` opened from `archive.gz` | `extra["gzip.original_filename"] == "report.csv"`; `raw_name` holds undecoded bytes; default `name == "archive"` |
| `.gz` without `FNAME` | No gzip original filename extra; name is derived from source filename |
| `.xz` / `.zst` / `.lz4` / `.lz` / `.bz2` | No gzip filename fields are set |

### Requirement: Get per-codec metadata from codec descriptors

`SingleFileBackend` SHALL obtain per-codec metadata from each
`compressed-streams` codec descriptor rather than a reader-local dispatch table.
Descriptor hooks SHALL preserve existing surfaced metadata: gzip `FNAME`,
`raw_name`, optional gzip mtime, xz/zst/lz4/lzip size hints, and the
format-specific size-availability rules. A codec with no extra metadata SHALL
register no hook.

#### Scenario: descriptor metadata matrix

| Case | Expected |
| --- | --- |
| `.gz` with stored `FNAME` and mtime | Gzip descriptor hook populates extra filename, `raw_name`, and `modified` |
| `.bz2` source | No hook is needed; default single-file member shell is used |
| Size-aware lzip source | Lzip descriptor hook supplies trailer-derived size |

### Requirement: Use one backend for every standalone codec

The system SHALL implement single-file compressor reading as one
`SingleFileBackend` whose `FORMATS` tuple lists every standalone-stream codec.
The backend SHALL infer the one-member shell, then delegate decompression and
metadata to the stream codec resolved from the member's stream format. Detection
tables and availability SHALL derive from codec descriptors: the backend remains
registered, while a format whose required codec backend is missing reports
support `NONE`. Adding a new standalone codec descriptor SHALL make that format
readable without adding another `ReadBackend` subclass.

#### Scenario: backend matrix

| Case | Expected |
| --- | --- |
| Open `.gz`, `.bz2`, `.xz`, `.lzma` | Same `SingleFileBackend` class serves each format with per-codec metadata |
| Register a new standalone codec descriptor | Existing backend reads it through the descriptor |
| Required codec backend is missing | Format availability reports `NONE`, not a separate backend failure |
