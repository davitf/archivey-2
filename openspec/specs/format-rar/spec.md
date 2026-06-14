# RAR Archive Support (native metadata parser + system unrar)

## Purpose

Archivey parses RAR4 and RAR5 metadata natively — with no `rarfile` Python
dependency — and delegates the proprietary RAR decompression to the system
`unrar` binary, which remains a required runtime dependency for reading member
*data*. Because metadata is parsed natively, members can be listed without
`unrar`; only reading bytes needs it. RAR is read-only. `rarfile` is used only as
a cross-validation oracle in the test suite.

> Provenance: native RAR *metadata* parsing (drop `rarfile`, keep `unrar` as the
> decompressor) and the single `unrar p` pipe-demultiplexer streaming approach
> come from the `archivey-dev` `rar-native-metadata-reader` exploration and
> `RarStreamReader`, distilled in COMPARISON.md §3/§4 and ARCHITECTURE.md §5.7.
> Rolling a full native RAR decompressor is explicitly out of scope: the format
> is proprietary and the reference implementation is `unrar` itself.

## Requirements

### Requirement: Declare format properties

The system SHALL expose the following properties for the RAR backend:

| Property | Value |
|----------|-------|
| Read dependency (metadata) | None — native RAR4/RAR5 header parser |
| Read dependency (data) | system `unrar` binary on PATH |
| Listing cost | O(1) — headers parsed natively upfront |
| Access cost | SOLID if solid archive; DIRECT otherwise |
| Supports write | No — RAR is proprietary; read-only |
| Requires seek | Yes (for header parsing) |

#### Scenario: write attempt on a RAR archive

- **WHEN** a caller attempts to create or write a RAR archive
- **THEN** the system SHALL raise `UnsupportedOperationError`

#### Scenario: opening from a non-seekable source

- **WHEN** the source stream does not support seeking
- **THEN** the backend SHALL reject the open with an appropriate error

---

### Requirement: Parse RAR4 and RAR5 headers natively

The system SHALL parse RAR4 and RAR5 archive headers natively to produce the full
member list and per-member metadata — name, sizes, timestamps, mode, flags, the
solid flag, link redirect (`file_redir`) information, and encryption flags —
without the `rarfile` library. Listing is O(1) and decompresses no member data.

#### Scenario: listing a RAR without the unrar binary present

- **WHEN** a (non-header-encrypted) RAR archive is opened and `unrar` is not on PATH
- **THEN** the member list and all metadata are still available, because listing is satisfied entirely by the native header parser

---

### Requirement: Require the unrar binary to read compressed member data

The system SHALL read **stored** (uncompressed, unencrypted) members directly as
raw bytes through the shared `compressed-streams` pass-through backend, and SHALL
obtain all other member *data* by invoking the system `unrar` binary. If `unrar` is
required but not available on PATH, the system SHALL raise
`PackageNotInstalledError` naming `unrar`. Listing never requires `unrar`.

#### Scenario: reading a compressed member without unrar installed

- **WHEN** `reader.read(member)` is called on a non-stored member and `unrar` is not on PATH
- **THEN** the system raises `PackageNotInstalledError` naming `unrar`

#### Scenario: reading a stored member without unrar

- **WHEN** `reader.read(member)` is called on a stored (uncompressed) member and `unrar` is not on PATH
- **THEN** the raw bytes are returned without invoking `unrar`

---

### Requirement: Stream solid archives via a single unrar pipe

For solid-archive sequential iteration (`stream_members()`), the system SHALL run
a single `unrar p -inul <archive>` subprocess and demultiplex its stdout into
per-member streams using the member sizes from the native header, validating each
member's checksum (CRC32 or Blake2sp, per `hashes`) incrementally. This processes
the whole archive in one subprocess — O(archive_size) total — rather than spawning
one subprocess per member.

#### Scenario: streaming a solid RAR with stream_members()

- **WHEN** `stream_members()` is called on a solid RAR archive
- **THEN** the system runs exactly one `unrar p` subprocess, demultiplexes its stdout into per-member streams by header-provided sizes, and validates each member's checksum as it is read

---

### Requirement: Random access and non-solid strategy

The system SHALL serve random and non-solid access without the single-pipe
streaming path:

- **Non-solid archives:** read a member via `unrar` for that member, which is
  O(member_size).
- **Solid random access:** decode from the archive start up to the requested
  member, or extract once with `unrar x` into a temporary directory and serve
  subsequent reads from disk (cleaned up on `close()`).
- **`extract_all`:** MAY use a one-shot `unrar x` to a temporary directory.

#### Scenario: random access on a non-solid RAR

- **WHEN** `ar.open(member)` is called on a non-solid RAR archive
- **THEN** the backend reads just that member's data via `unrar`, doing O(member_size) work

#### Scenario: repeated random access on a solid RAR

- **WHEN** multiple members of a solid RAR are accessed via `ar.open()`
- **THEN** the backend MAY extract the archive once with `unrar x` into a temporary directory and serve reads from disk, removing the directory on `close()`

---

### Requirement: Report the absence of solid block boundary information

The system SHALL treat RAR solidity as binary per archive — there is no per-block
granularity — and SHALL set `CostReceipt.solid_block_count` to `None` for RAR
archives.

#### Scenario: CostReceipt for a solid RAR

- **WHEN** a solid RAR archive is opened
- **THEN** `CostReceipt.is_solid` is `True` and `CostReceipt.solid_block_count` is `None`

---

### Requirement: Handle RAR4 and RAR5 timestamp differences

The system SHALL map timestamps from the native header according to RAR version:

- **RAR4:** local wall-clock time → `Member.modified` is a naive `datetime`.
- **RAR5:** UTC with sub-second precision → `Member.modified` is a timezone-aware `datetime`.

#### Scenario: RAR4 archive timestamp

- **WHEN** a member's modification time is read from a RAR4 archive
- **THEN** `Member.modified` is a naive `datetime` representing local wall-clock time

#### Scenario: RAR5 archive timestamp

- **WHEN** a member's modification time is read from a RAR5 archive
- **THEN** `Member.modified` is a timezone-aware UTC `datetime`

---

### Requirement: Handle RAR5 link types from native redirect metadata

The system SHALL read RAR5 link semantics from the natively parsed `file_redir`
field:

- **Hardlinks and file-copies** (`RAR5_XREDIR_HARD_LINK`, `RAR5_XREDIR_FILE_COPY`):
  the member is mapped to its redirect target so that reading it returns the
  target file's data.
- **Symlinks** (`RAR5_XREDIR_UNIX_SYMLINK`): stored with the link target path as
  content; resolution is handled by the format-independent link-following layer
  in the `ArchiveReader` base class.

#### Scenario: reading a RAR5 hardlink or file-copy member

- **WHEN** `read()` is called on a member whose native `file_redir` marks it a hardlink or file-copy
- **THEN** the backend returns the redirect target's data

#### Scenario: reading a RAR5 symlink member

- **WHEN** `read()` is called on a RAR5 Unix symlink member
- **THEN** the ABC-level link-following layer resolves the target and returns the target member's data

---

### Requirement: Decrypt header-encrypted RAR5 via an optional crypto backend

RAR5 header encryption uses AES, which the standard library cannot perform. The
native parser SHALL derive the AES key and decrypt encrypted headers *itself* via
the wrapped crypto backend (`[crypto]`, see `compressed-streams`) when a password is
supplied — it does NOT need the `unrar` binary to list a header-encrypted archive
(`unrar` is required only to read member *data*). The system SHALL set
`ArchiveInfo.is_encrypted = True`. Without a password, listing SHALL raise
`EncryptionError`; with a password but no crypto backend installed, listing SHALL
raise `PackageNotInstalledError`.

#### Scenario: listing a header-encrypted RAR5 archive without a password

- **WHEN** a header-encrypted RAR5 archive is opened without a password
- **THEN** the system raises `EncryptionError`

#### Scenario: header-encrypted RAR5 with a password but no crypto backend

- **WHEN** a header-encrypted RAR5 archive is opened with a password but `[crypto]` is not installed
- **THEN** the system raises `PackageNotInstalledError`

#### Scenario: listing a header-encrypted RAR5 archive with a password

- **WHEN** a header-encrypted RAR5 archive is opened with a valid password and `[crypto]` installed
- **THEN** the member list is produced natively by decrypting the headers (no `unrar` needed for listing) and `ArchiveInfo.is_encrypted` is `True`
- **AND** reading member *data* still requires the `unrar` binary

---

### Requirement: Surface unsupported RAR variants and integrity limits

The native parser SHALL raise `UnsupportedFeatureError` for archive shapes it does
not support — multi-volume RAR sets and legacy RAR2 archives (extract version ≤ 20)
— rather than mis-parsing them. For RAR5 members that carry only a Blake2sp hash and
no CRC32, `Member.hashes` SHALL contain a `"blake2sp"` entry (bytes) and no
`"crc32"` key — never a guessed CRC.

#### Scenario: multi-volume RAR set

- **WHEN** a multi-volume RAR archive is opened
- **THEN** the system raises `UnsupportedFeatureError` rather than a partial or garbled member list

#### Scenario: legacy RAR2 archive

- **WHEN** a RAR2-era archive (extract version ≤ 20) is opened and is not supported
- **THEN** the system raises `UnsupportedFeatureError`

#### Scenario: RAR5 member with only a Blake2sp hash

- **WHEN** a RAR5 member records a Blake2sp hash but no CRC32
- **THEN** `member.hashes["blake2sp"]` holds the digest (bytes) and `"crc32"` is absent from `member.hashes`
