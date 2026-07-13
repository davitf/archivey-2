## MODIFIED Requirements

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
