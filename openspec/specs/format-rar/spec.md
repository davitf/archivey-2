# RAR Archive Support

## Purpose

Archivey parses RAR metadata natively (RAR 1.5 / 2.x through RAR5) with no
`rarfile` dependency. Listing uses the native parser only; reading compressed or
encrypted member data delegates to the system RARLAB `unrar` binary. RAR is
read-only, and `rarfile` is only a test oracle.

This native-metadata/system-decompressor split follows the `archivey-dev`
`rar-native-metadata-reader` exploration. A full native RAR decompressor is out
of scope because RAR compression is proprietary and `unrar` is the reference
implementation.

## Related specs

| Spec | Relationship |
| --- | --- |
| `archive-reading` | Read API, multi-source input, passwords, link following, bounded storage |
| `access-mode-and-cost` | Seek requirement, cost receipt, solid access semantics |
| `compressed-streams` | Pass-through stored reads and checksum verification |
| `packaging-and-extras` | RARLAB `unrar`, `[rar]`, `[crypto]` availability |
| `testing-contract` | Native parser coverage and `rarfile` oracle checks |

## Requirements

### Requirement: Declare RAR format properties

The RAR backend SHALL expose these properties:

| Property | Value |
| --- | --- |
| Read dependency (metadata) | None; native RAR 1.5–RAR5 header parser |
| Read dependency (data) | RARLAB `unrar` binary on `PATH` |
| Listing cost | O(1); headers parsed natively, no member-data decompression |
| Access cost | `SOLID` for solid archives; `DIRECT` otherwise |
| Supports write | No |
| Requires seek | Yes |

#### Scenario: format property matrix

| Case | Expected |
| --- | --- |
| Open non-header-encrypted RAR without `unrar` | Listing and metadata still work through the native parser |
| Open from a non-seekable source | Open fails because RAR header parsing requires seek |
| Attempt to create/write RAR | `UnsupportedOperationError` |

### Requirement: Parse RAR headers natively (RAR 1.5 through RAR5)

The system SHALL parse RAR archive headers natively — including RAR 1.5 / 2.x
archives that advertise extract version ≤ 20, RAR3/RAR4, and RAR5 — to produce
the full member list and per-member metadata: names, packed/unpacked sizes,
timestamps, mode, flags, solid state, RAR5 redirect (`file_redir`) records,
encryption flags, and integrity hashes. Listing SHALL not import `rarfile` or
invoke `unrar`. Extract version ≤ 20 MUST NOT by itself cause rejection: those
archives share the same header block layout the parser already understands, and
member data remains RARLAB `unrar`'s responsibility.

#### Scenario: native header matrix

| Case | Expected |
| --- | --- |
| Open RAR 1.5 / 2.x archive (extract version ≤ 20) | Members and metadata come from native headers; no `UnsupportedFeatureError` for extract version alone |
| Open RAR3/RAR4 archive whose members advertise extract version 20 | Listing succeeds (stored/small members often carry `unp_ver=20`) |
| Open RAR4 archive | Members and metadata come from native headers |
| Open RAR5 archive | Members, flags, hashes, and redirect metadata come from native headers |
| `unrar` missing during listing | Listing succeeds unless header decryption needs unavailable crypto/password |

### Requirement: Use RARLAB unrar only for member data that needs it

The system SHALL read stored, uncompressed, unencrypted members directly as raw
bytes through the shared pass-through backend. All other member data SHALL be
read by invoking the system RARLAB `unrar` binary. If `unrar` is required and
missing or incompatible, the system SHALL raise `PackageNotInstalledError`
naming RARLAB `unrar`. Archivey MUST NOT silently use `unrar-free`, `unar`,
`bsdtar`, `7z`, or a degraded backend.

#### Scenario: unrar dependency matrix

| Case | Expected |
| --- | --- |
| Stored member, `unrar` missing | Raw bytes are returned without invoking `unrar` |
| Compressed member, `unrar` missing | `PackageNotInstalledError` names `unrar` |
| PATH `unrar` is not RARLAB `unrar` | `PackageNotInstalledError` names RARLAB `unrar` |
| Listing only, `unrar` missing | No data dependency is checked |

### Requirement: Stream solid RAR archives through one unrar pipe

For solid-archive `stream_members()`, the system SHALL run one
`unrar p -inul <archive>` subprocess and demultiplex stdout into per-member
streams using native-header sizes. It SHALL validate each member's CRC32 or
Blake2sp hash incrementally via the shared verification stage. This path MUST
process the archive once, not spawn one subprocess per member.

#### Scenario: solid streaming matrix

| Case | Expected |
| --- | --- |
| `stream_members()` on a solid RAR | Exactly one `unrar p` process; stdout is split by member sizes |
| Member has CRC32/Blake2sp | Verification runs as bytes are read and raises on mismatch |
| A later member is selected in the same pass | Earlier bytes are drained through the single pipe, not separate member subprocesses |

### Requirement: Serve random access and extraction with bounded explicit temp use

The system SHALL serve non-solid random reads by invoking `unrar` for the target
member, doing O(member_size) data work. For solid random reads, the system SHALL
decode from archive start to the target member or extract once with `unrar x` into
an explicitly managed temporary directory and serve later reads from disk; that
directory is cleaned up on reader close. `extract_all()` MAY use one `unrar x`
to a temporary directory. Any temp materialization SHALL be a declared RAR
strategy, not an implicit in-memory buffer.

#### Scenario: random/extract matrix

| Case | Expected |
| --- | --- |
| Random `open()` in non-solid RAR | `unrar` reads the requested member; work is O(member_size) |
| Repeated random opens in solid RAR | Backend may use one tempdir extraction and remove it on close |
| `extract_all()` | Backend may use one-shot `unrar x` |

### Requirement: Support benchmark-gated small-member optimization

The reader SHALL allow an optional small-member optimization only when
benchmarks justify it. The optimization MAY build a temporary single-file RAR
containing the requested member and invoke `unrar` on that smaller archive when
the member is below a benchmark-derived threshold. Output MUST be byte-identical
to the direct `unrar` path.

#### Scenario: small-member optimization matrix

| Case | Expected |
| --- | --- |
| Many small random reads with optimization enabled | Bytes match direct `unrar`; measured overhead is lower |
| Benchmark does not justify the threshold | Optimization is not used |

### Requirement: Report RAR cost and version-specific metadata

The system SHALL treat RAR solidity as a binary archive-level property because
RAR exposes no per-solid-block boundaries. `ArchiveInfo.is_solid` reflects the
native solid flag and `CostReceipt.solid_block_count` SHALL be `None`. Timestamp
mapping SHALL preserve RAR version semantics: RAR4 local wall-clock timestamps
become naive `datetime` values, and RAR5 UTC/sub-second timestamps become
timezone-aware UTC `datetime` values. RAR5 Blake2sp-only members SHALL store the
digest bytes at `member.hashes["blake2sp"]` and omit `"crc32"`.

#### Scenario: metadata matrix

| Case | Expected |
| --- | --- |
| Solid RAR | `ArchiveInfo.is_solid` true; `solid_block_count is None` |
| RAR4 timestamp | `ArchiveMember.modified` is naive local wall-clock time |
| RAR5 timestamp | `ArchiveMember.modified` is timezone-aware UTC |
| RAR5 member with Blake2sp only | `"blake2sp"` present as bytes; `"crc32"` absent |

### Requirement: Handle RAR5 redirect link types natively

The system SHALL read RAR5 link semantics from native `file_redir` metadata.
Hardlinks and file-copies (`RAR5_XREDIR_HARD_LINK`,
`RAR5_XREDIR_FILE_COPY`) map the member to its redirect target so reads return
the target data. Unix symlinks (`RAR5_XREDIR_UNIX_SYMLINK`) store the link target
path as content and are resolved by the format-independent link-following layer.

#### Scenario: redirect matrix

| Case | Expected |
| --- | --- |
| RAR5 hardlink/file-copy member is read | Backend returns redirect target data |
| RAR5 Unix symlink member is read | `ArchiveReader` link following resolves the target and returns target data |

### Requirement: Decrypt RAR5 header-encrypted archives natively

The system SHALL decrypt RAR5 header-encrypted archives through the optional
crypto backend when a valid password is supplied. The native parser derives the
AES key and decrypts headers itself; `unrar` is not required for listing and
remains required only for member data. Header-encrypted listing without a
password SHALL raise `EncryptionError`; with a password but no `[rar]` or
`[crypto]` backend, it SHALL raise `PackageNotInstalledError`. Any encrypted RAR
SHALL set `ArchiveInfo.is_encrypted` to `True`.

#### Scenario: header encryption matrix

| Case | Expected |
| --- | --- |
| Header-encrypted RAR5, no password | `EncryptionError` |
| Header-encrypted RAR5, password but no crypto backend | `PackageNotInstalledError` |
| Header-encrypted RAR5, valid password + crypto | Headers decrypt natively; members list; `is_encrypted` true |
| Read member data from that archive | `unrar` is still required |

### Requirement: Reject unsupported RAR variants clearly

Multi-volume RAR sets SHALL be supported by the volume contract, not rejected as
an unsupported variant. Opening a later volume before the first volume of a set
SHALL raise `UnsupportedFeatureError` (or a truncated/out-of-order error) rather
than silently mis-joining members. Truly unreadable layouts (corrupt headers,
unknown required crypto without the extra) continue to raise typed errors from
their existing requirements.

#### Scenario: unsupported variant matrix

| Case | Expected |
| --- | --- |
| Multi-volume RAR4/RAR5 set is opened from volume 1 | Handled by the multi-volume requirement |
| Multi-volume set opened from a later volume first | `UnsupportedFeatureError` or truncated/out-of-order error |

### Requirement: Support multi-volume RAR sets

The system SHALL support multi-volume RAR archives named `name.partN.rar`
(RAR5/newer RAR4) or `name.rar` + `name.r00`, `name.r01`, ... (older RAR4). The
native parser SHALL read volume headers in order and stitch members that span
volume boundaries into one logical member using continuation flags.
`open_archive()` SHALL accept either a path inside the set, with sibling
discovery in order, or an explicit ordered source sequence. For path sources,
data reads point `unrar` at the first volume so it can find later volumes. For
stream sources, data reads SHALL materialize ordered volumes for `unrar` when
needed. Missing or out-of-order volumes SHALL raise `UnsupportedFeatureError` or
a truncated error instead of a partial result.

#### Scenario: volume matrix

| Case | Expected |
| --- | --- |
| Open `name.part1.rar` with complete siblings | Headers across all volumes parse as one archive |
| Read a member spanning volumes | Returned stream reassembles the member across boundaries |
| Open explicit ordered stream volumes | Metadata parses in order; data reads materialize volumes for `unrar` if needed |
| Missing or out-of-order volume | Error instead of partial or garbled output |
