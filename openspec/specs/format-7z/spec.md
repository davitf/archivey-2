# 7-Zip Archive Support

## Purpose

Archivey reads 7-Zip archives with a native, zero-dependency reader. The reader
parses headers itself and uses shared `compressed-streams` decoders backed by the
standard library for the common methods (`lzma` `FORMAT_RAW`, `bz2`, `zlib`).
`py7zr` is not a read dependency; it is used only for optional 7z writing and as
a test oracle.

The native-first strategy preserves true pull-based streaming and avoids
background thread/queue or per-folder spooling behavior from push-based library
readers. It follows the `archivey-dev` `sevenzip-native-reader` exploration.

## Related specs

| Spec | Relationship |
| --- | --- |
| `archive-reading` | `open_archive`, multi-source input, passwords, bounded storage, `stream_members` |
| `access-mode-and-cost` | Seek requirement, cost receipt, solid access semantics |
| `compressed-streams` | Decoder composition, CRC verification, optional codec backends |
| `packaging-and-extras` | `[7z]`, `[crypto]`, `[7z-write]` extras |
| `testing-contract` | Native parser coverage and `py7zr` oracle checks |

## Requirements

### Requirement: Declare 7-Zip format properties

The 7-Zip backend SHALL expose these properties:

| Property | Value |
| --- | --- |
| Read dependency | None; native parser + shared stdlib-backed decoders |
| Write dependency | `py7zr` via optional `[7z-write]` |
| Listing cost | O(1); native header parse, no file-data decompression |
| Access cost | `SOLID` when any folder packs multiple files; `DIRECT` for single-file folders |
| Supports write | Yes, via `[7z-write]` |
| Requires seek | Yes |

#### Scenario: format property matrix

| Case | Expected |
| --- | --- |
| Open a seekable 7z for listing | Header is parsed natively; full member list is available; no third-party reader imports |
| Open from a non-seekable source | Open fails because 7z requires seek |
| Attempt 7z write with `[7z-write]` installed | Archive is written through `py7zr` |
| Attempt 7z write without `[7z-write]` | Clear missing-extra error |

### Requirement: Parse 7-Zip headers natively

The system SHALL parse the signature header, packed-streams info, folders/coder
chains, substreams info, files info, archive comment, and anti-items without any
third-party reader. The parsed header produces every member, each member's folder
mapping, each folder's file count, contiguous file layout inside decompressed
folder output, and `ArchiveInfo.comment` when present. Anti-items SHALL not
corrupt the member list.

#### Scenario: native header matrix

| Case | Expected |
| --- | --- |
| Open any supported 7z | Members and folder mapping come from the header without decompressing folders |
| Archive stores a comment | `ArchiveInfo.comment` contains the comment |
| Archive contains anti-items | Member list remains correct |

### Requirement: 7z anti-items are MemberType.ANTI

7z `FILES_INFO` ANTI-bit entries SHALL be exposed as `MemberType.ANTI`
(`is_anti`, not `is_file`). `open`/`read` SHALL raise `ArchiveyUsageError`;
`stream_members` SHALL yield `None`. Extraction follows `safe-extraction` anti
rules. This replaces empty-payload `FILE` anti opens.

#### Scenario: 7z anti matrix

| Case | Expected |
| --- | --- |
| ANTI-bit entry in member list | `type == MemberType.ANTI`; `is_anti`; not `is_file` |
| `open`/`read` anti member | `ArchiveyUsageError` |
| `stream_members` anti member | Stream `None` |
| Content then later anti same path | Content `is_current` false; anti `is_anti` and `is_current` true |

### Requirement: Decode folder coder chains through compressed-streams

The system SHALL decode each folder by composing shared `compressed-streams`
backends in decoding order. A coder list such as `AES -> LZMA2` decrypts, then
decompresses. Files in a folder are yielded by reading exactly `member.size`
bytes in archive order from the decompressed folder stream. Per-member CRC32
values SHALL appear in `hashes["crc32"]` and SHALL be verified by the shared
verification stage as data is read.

| 7z codec | Method ID | Backend | Availability |
| --- | --- | --- | --- |
| STORED | `0x00` | pass-through | core |
| LZMA1 / LZMA2 | `0x030101` / `0x21` | `lzma` `FORMAT_RAW` | core |
| Delta | `0x03` | `lzma.FILTER_DELTA` | core |
| BCJ x86/ARM/ARMT/PPC/SPARC/IA64 | `0x04`-`0x09`, `0x03030103`... | `lzma` BCJ filters (LZMA2+BCJ); `pybcj` for LZMA1+BCJ | core for LZMA2+BCJ; `[7z]` for LZMA1+BCJ |
| Deflate | `0x040108` | raw `zlib` | core |
| BZip2 | `0x040202` | `bz2` | core |
| Zstd | `0x04f71101` | stdlib `compression.zstd` / `backports.zstd` | core on 3.14+; otherwise `[7z]` |
| Brotli | `0x04f71102` | `brotli` | `[7z]` |
| PPMd (var.H) | `0x030401` | `pyppmd` | `[7z]` |
| Deflate64 | `0x040109` | `inflate64` | `[7z]` |
| AES-256 / SHA-256 | `0x06f10701` | crypto backend | `[crypto]` / `[7z]` |
| BCJ2 | `0x0303011B` | none | unsupported |

The `[7z]` extra SHALL provide PPMd, Deflate64, Zstd on Python versions without
stdlib zstd, Brotli, AES, and LZMA1+BCJ (`pybcj`) support in one install.

LZMA1+BCJ folders SHALL NOT be decoded via a single combined `lzma` `FORMAT_RAW`
filter chain: liblzma can silently truncate the final BCJ look-ahead bytes when
LZMA1 lacks an end-of-stream marker. The reader MUST stage LZMA1 (and any non-BCJ
`lzma` filters such as Delta) through stdlib `lzma`, then apply each BCJ stage
through `pybcj`. LZMA2+BCJ remains a single stdlib filter chain in core.

#### Scenario: coder-chain matrix

| Case | Expected |
| --- | --- |
| BCJ + LZMA2 folder | Shared `lzma` raw filter chain returns original bytes |
| BCJ + LZMA1 folder with `[7z]` / `pybcj` | Staged LZMA1 then `pybcj` returns original bytes |
| BCJ + LZMA1 folder without `pybcj` | `PackageNotInstalledError` names `pybcj` and the `[7z]` extra |
| Member with stored CRC32 | Terminal verification raises `CorruptionError` on mismatch |
| PPMd without `[7z]` | `PackageNotInstalledError` names `pyppmd` and the `[7z]` extra |
| AES + LZMA2 folder | Crypto stage decrypts before LZMA2 decompression |

### Requirement: Reject unsupported codecs without fallback

The system SHALL raise `UnsupportedFeatureError` naming the codec or method ID
when a folder uses a coder with no available backend. This includes BCJ2, newer
branch filters absent from installed liblzma, and unrecognized method IDs. The
reader MUST NOT return garbage and MUST NOT fall back to `py7zr` or another
third-party reader. PPMd, Deflate64, and LZMA1+BCJ are optional-supported via
`[7z]`, and multi-volume 7z is supported by volume joining.

#### Scenario: unsupported-codec matrix

| Case | Expected |
| --- | --- |
| Folder uses BCJ2 | `UnsupportedFeatureError` names BCJ2; no output bytes |
| Folder uses unknown method ID | `UnsupportedFeatureError` names the method ID |
| Folder uses PPMd with `[7z]` installed | Member is decoded, not rejected |
| Folder uses LZMA1+BCJ with `[7z]` installed | Member is decoded via staged `pybcj`, not rejected |

### Requirement: Stage LZMA1+BCJ through pybcj under `[7z]`

The system SHALL decode linear folders whose coder chain includes both LZMA1 and
at least one BCJ branch filter (x86/ARM/ARMT/PPC/SPARC/IA64) by composing
stdlib LZMA1 decompression with `pybcj` BCJ filters. The reader MUST NOT feed
LZMA1 and BCJ into one `lzma.LZMADecompressor` `FORMAT_RAW` filter list. When
`pybcj` is absent, opening such a member SHALL raise `PackageNotInstalledError`
naming `pybcj` and `pip install archivey[7z]`. BCJ2 remains unsupported.

#### Scenario: LZMA1+BCJ matrix

| Case | Expected |
| --- | --- |
| 7-Zip CLI `-m0=BCJ -m1=LZMA` fixture + `pybcj` | Round-trip bytes match; no silent truncation |
| py7zr `FILTER_X86`+`FILTER_LZMA` fixture + `pybcj` | Round-trip bytes match |
| Same fixtures without `pybcj` | `PackageNotInstalledError` for `pybcj` / `[7z]` |
| LZMA2+BCJ without `pybcj` | Still works in core via stdlib filters |

### Requirement: Support multi-volume 7z by ordered concatenation

The system SHALL support split 7z sets (`name.7z.001`, `name.7z.002`, ...) by
joining volumes in order into one logical byte stream and parsing that stream as
ordinary 7z. `open_archive()` SHALL accept either a path inside the set, with
sibling discovery in numeric order, or an explicit ordered source sequence. If a
volume is missing or the stream cannot be reconstructed, the system SHALL raise
`UnsupportedFeatureError` or a truncated/corrupt error, never a partial result.

#### Scenario: volume matrix

| Case | Expected |
| --- | --- |
| Open `name.7z.001` with complete siblings | Volumes join in numeric order; listing and reads match a single-file archive |
| Open an explicit ordered volume list | Sources concatenate and read as one archive |
| Missing or out-of-order volume | Error instead of partial or garbage output |

### Requirement: Decrypt AES-encrypted 7z with archive-reading passwords

The system SHALL read AES-256-encrypted 7z folders and header-encrypted archives
when a valid password and crypto backend are available. Header encryption SHALL
decrypt the end header before listing; without a password the system raises
`EncryptionError`, and with no crypto backend it raises `PackageNotInstalledError`.
Any archive or folder encryption SHALL set `ArchiveInfo.is_encrypted` to `True`.

Passwords SHALL use the `archive-reading` candidate model: known-good successes
for this reader, remaining static candidates, then provider requests. Header
requests use `member is None`; folder/member requests identify the member being
decrypted where possible. Members in different encrypted folders MAY use different
passwords in one open or one `stream_members()` pass. Key derivation SHALL use the
7z SHA-256 scheme (UTF-16LE password, salt, `1 << NumCyclesPower` rounds with the
documented `0x3f` special case) via a 7z-local helper that feeds `AesParams` into
the shared crypto stage — not a generic crypto-surface KDF. Because 7z has no
password check value, the reader SHALL cache derived keys by
`(password, salt, cycles)`, try known-good passwords first, and surface wrong
passwords as `EncryptionError`/`CorruptionError`, never silent bytes.

#### Scenario: encryption matrix

| Case | Expected |
| --- | --- |
| Header-encrypted archive, no password/provider result | `EncryptionError` before listing |
| Header-encrypted archive, valid password + crypto | Header is decrypted natively; members list; `is_encrypted` true |
| Encrypted folders with different passwords | Each folder uses its matching candidate in random access or one streaming pass |
| Sole wrong password | `EncryptionError`/`CorruptionError`; no incorrect data |
| Repeated salt/cycles/password | Derived key cache avoids repeated key derivation |

### Requirement: Stream solid folders with bounded memory

The system SHALL implement `stream_members()` as a pull stream: each folder is
decoded once, members are yielded in archive order as bytes become available, and
peak memory is bounded by decoder working set plus in-flight output. The reader
MUST NOT buffer an entire solid folder in memory or keep a growing decompressed
cache until close. Random `open()` for a member inside a solid folder MAY
re-decode from the folder start or use explicitly bounded/disk-backed retention.

#### Scenario: streaming and random access matrix

| Case | Expected |
| --- | --- |
| `stream_members()` over a solid 7z | Each folder decodes once; peak memory is not proportional to folder size |
| Random `open()` inside a multi-file folder | Backend decodes from folder start or uses bounded/disk-backed retention |
| Repeated random opens in one folder | Any acceleration obeys `archive-reading` bounded-storage rules |

### Requirement: Report 7z cost and member metadata

The system SHALL populate 7z metadata from the native header. `ArchiveInfo.is_solid`
is `True` when any folder packs more than one file, `CostReceipt.solid_block_count`
is the folder count, and non-solid archives report `AccessCost.DIRECT`. Each
member's parsed coder chain SHALL map to `tuple[CompressionMethod, ...]` in filter
order. If a POSIX attribute block is absent, `mode`, `uid`, and `gid` SHALL be
`None`, never guessed defaults.

#### Scenario: metadata matrix

| Case | Expected |
| --- | --- |
| Folder packs multiple files | `ArchiveInfo.is_solid` true; `solid_block_count` equals folder count |
| Every folder packs one file | `ArchiveInfo.is_solid` false; access cost is `DIRECT` |
| BCJ pre-filter followed by LZMA2 | `member.compression` records the full chain in order |
| No POSIX attribute block | `member.mode`, `member.uid`, and `member.gid` are all `None` |
