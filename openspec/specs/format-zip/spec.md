# ZIP Format Behavior

## Purpose

ZIP archives are read through the unified `ArchiveReader` API using stdlib
`zipfile` for the central directory and current member data path. ZIP listing is
indexed, member access is direct, read sources must be seekable, and streaming
write uses data descriptors.

## Related specs

| Spec | Relationship |
| --- | --- |
| `archive-reading` | Reader API, password candidates, weak-check confirmation, bounded storage |
| `access-mode-and-cost` | Random-access vs streaming rules; non-seekable random-access failure |
| `diagnostics` | Timestamp and symlink-target diagnostic values / policy |
| `backend-registry` | `format_availability(ZIP)` partial-read status |
| `compressed-streams` | Future ZIP member-data codec path |

## Requirements

### Requirement: Report ZIP format properties

The ZIP backend SHALL expose these properties for every opened ZIP archive:

| Property | Value |
| --- | --- |
| Backend dependency | `zipfile` (stdlib) |
| Listing cost | `ListingCost.INDEXED` — central directory read at open |
| Access cost | `AccessCost.DIRECT` — independent local file offsets |
| Stream capability | `StreamCapability.SEEKABLE` |
| Read source | Seekable only; no implicit buffering/spooling |
| Write support | Yes, including streaming write |

`reader.get()` and other name lookups SHALL use the central-directory-derived
member map without extra archive I/O. ZIP member data still goes through stdlib
`zipfile`; unsupported methods such as Deflate64/PPMd and zstd before Python
3.14 SHALL raise `UnsupportedFeatureError`. Until ZIP member reads use the
shared codec layer, `format_availability(ZIP)` SHALL report **PARTIAL**
regardless of optional codec installation. Installing `[7z]` / `[zstd]` alone
does not unlock those member reads.

The future raw-slice member-data path SHALL keep `zipfile` for the central
directory, compute each member's compressed byte range from local headers, and
decode via `compressed-streams`; `zipfile` has no decompressor plug-in point.

#### Scenario: ZIP property matrix

| Case | Expected |
| --- | --- |
| Open valid ZIP | `cost.listing_cost=INDEXED`, `cost.access_cost=DIRECT`, `cost.stream_capability=SEEKABLE` |
| `reader.get("some/member.txt")` | Satisfied from the in-memory central-directory name map; no additional archive I/O |
| Member uses unsupported stdlib method | Listing succeeds; reading raises `UnsupportedFeatureError`; availability remains `PARTIAL` |
| Streaming write without pre-known size | Local header uses data-descriptor placeholders; data descriptor stores final CRC and sizes; standard ZIP tools can read the result |

### Requirement: Reject non-seekable ZIP read sources

The ZIP central directory is at EOF, so the ZIP reader SHALL raise
`StreamNotSeekableError` at open when reading from a non-seekable source. The
library MUST NOT buffer, spool, or copy a non-seekable ZIP source into seekable
storage implicitly. Callers must provide a seekable source or choose an access
path that does not require opening ZIP as random-access. Any future spooling
convenience must be an explicit opt-in, not default behavior.

#### Scenario: non-seekable read matrix

| Case | Expected |
| --- | --- |
| Non-seekable ZIP with default `streaming=False` | `StreamNotSeekableError` at open; no reader |
| Non-seekable ZIP with `streaming=True` | Still rejected because this backend cannot provide ZIP reading without seek |
| Implementation lacks seekable source | No implicit seekable-copy, temp-file, or spool fallback |

### Requirement: Map ZIP member metadata to ArchiveMember

The ZIP backend SHALL map each `ZipInfo` to `ArchiveMember` with these field
rules:

| Field | Mapping |
| --- | --- |
| `mode` | `external_attr >> 16` only for Unix entries with non-zero attrs; otherwise `None` |
| timestamps | DOS `date_time` base (naive local wall-clock, 2s granularity, 1980 sentinel → `None`); NTFS extra `0x000A` UTC FILETIMEs override present fields; Extended Timestamp `0x5455` UTC Unix times override present fields |
| `type` | Infer from Unix mode when available; otherwise directory marker and symlink hints |
| `compression` | `compress_type` mapped to `CompressionMethod` |
| `is_encrypted` | `flag_bits & 0x1 != 0` |

Invalid DOS or NTFS timestamp values SHALL fall through to the next valid
precedence layer or `None` and emit `MEMBER_TIMESTAMP_INVALID`. If listing
cannot read an encrypted symlink target because no correct password is
available, `link_target` SHALL remain unset and `SYMLINK_TARGET_UNAVAILABLE`
SHALL be emitted with reason `"password_required"`. Diagnostic payloads SHALL
not include passwords, candidates, provider returns, key material, or decrypted
target bytes. Under `RAISE`, listing halts with `DiagnosticRaisedError`.

#### Scenario: ZIP metadata matrix

| Case | Expected |
| --- | --- |
| Unix entry with non-zero `external_attr` | `member.mode = external_attr >> 16` |
| Non-Unix entry or missing attrs | `member.mode is None` |
| Extended Timestamp carries modification time | `member.modified` is timezone-aware UTC from `0x5455`, overriding DOS / NTFS |
| NTFS FILETIMEs present, no Extended Timestamp | Present `modified` / `accessed` / `created` fields are timezone-aware UTC from `0x000A` |
| `flag_bits & 0x1` | `member.is_encrypted is True` |
| Out-of-range NTFS or DOS timestamp | Fallback value used; `MEMBER_TIMESTAMP_INVALID` counted and may attach to member |
| Timestamp diagnostic resolves to `RAISE` | Listing halts with `DiagnosticRaisedError` |
| Encrypted symlink target unavailable | Listing continues with `link_target=None`; `SYMLINK_TARGET_UNAVAILABLE` contains no secret |

### Requirement: Reject multi-volume ZIP cleanly

The ZIP backend SHALL detect split/spanned ZIP archives and raise
`UnsupportedFeatureError` with a clear rejoin-first message instead of
mis-reading data or surfacing stdlib `BadZipFile`. Detection MAY use `.z01` /
`.zNN` segment names, non-zero disk fields in EOCD/ZIP64 EOCD, or ZIP64 locator
`disks > 1`. Archivey joins multi-volume 7z/RAR elsewhere; stdlib `zipfile`
cannot resolve ZIP `(disk-number, offset-within-disk)` addressing, and naive
segment concatenation is unreliable. Proper support is deferred to a future
native ZIP reader.

#### Scenario: multi-volume ZIP matrix

| Case | Expected |
| --- | --- |
| `open_archive()` receives `.z01`...`.zip` split set or any segment | `UnsupportedFeatureError`; caller is told to rejoin volumes first |
| EOCD declares non-zero disk number | `UnsupportedFeatureError`, not `BadZipFile` |
| ZIP64 locator reports `disks > 1` | `UnsupportedFeatureError` |

### Requirement: Confirm multi-candidate ZipCrypto passwords

For traditional ZipCrypto, the per-open verification byte is weak and the
authoritative check is CRC-32 plus decompressor completion. When another
distinct candidate may be tried, the ZIP reader SHALL confirm a candidate that
passes the byte check before accepting it, following `archive-reading` weak-check
confirmation and bounded-storage rules. With one distinct static candidate
(duplicates included), the reader SHALL keep the normal lazy stream path; any
read-time integrity failure is translated normally.

Compressed members (`DEFLATE`, `BZIP2`, `LZMA`) SHALL confirm by decompressing a
bounded plaintext prefix and discarding it. If EOF is reached within the bound,
the CRC check makes confirmation exact. STORED members SHALL disambiguate all
surviving candidates in one shared ciphertext pass, computing each candidate's
plaintext CRC-32 in constant memory; if multiple candidates match, candidate
order wins. No candidate plaintext may be buffered.

After confirmation, the reader SHALL open a fresh caller stream with the accepted
password, promote it to known-good, and retain ordinary read-time integrity
checking. Confirmation failure for all candidates SHALL raise `EncryptionError`
explaining that passwords may be wrong or the member may be corrupt.

Candidate failures SHALL include only `zipfile.BadZipFile` with `"Bad CRC-32 for
file ..."`, `zlib.error`, `lzma.LZMAError`, and exactly BZIP2's
`OSError("Invalid data stream")`. Local-header mismatch, bad local-header magic,
overlap, and other structural `BadZipFile` failures SHALL become
`CorruptionError` immediately. Other `OSError` values SHALL propagate unchanged.
Rejected-candidate streams SHALL be closed before trying the next candidate.

#### Scenario: ZipCrypto confirmation matrix

| Case | Expected |
| --- | --- |
| Wrong candidate passes verification byte before correct one (STORED / DEFLATE / BZIP2 / LZMA) | Wrong candidate rejected; fresh stream opened with correct candidate |
| One distinct static candidate | No confirmation read; member streams lazily |
| Large compressed member | At most bounded prefix decompressed per candidate; no proportional plaintext storage; caller stream still checks CRC at EOF |
| STORED member with several surviving candidates | One shared ciphertext pass computes every candidate CRC; matching candidate accepted and reopened |
| Multiple STORED CRC matches | Earliest matching candidate in order wins |
| Corruption beyond confirmed prefix | Caller read raises `CorruptionError` where the ordinary ZIP path detects it |
| Candidates fail confirmation | `EncryptionError` says password may be wrong or member corrupt; no bytes returned |
| Non-BZIP2 `OSError("Invalid data stream")` or any unrelated `OSError` | Propagates unchanged; failed stream is closed |
| Structural `BadZipFile` | `CorruptionError`; no further password iteration |

### Requirement: Support streaming ZIP write via data descriptors

The ZIP writer SHALL support non-seekable destinations by setting data descriptor
flag `0x8`, writing placeholder CRC and sizes in the local file header, streaming
data, and appending the actual CRC-32 and compressed/uncompressed sizes after the
member data. File size need not be known in advance.

#### Scenario: streaming ZIP write matrix

| Case | Expected |
| --- | --- |
| `writer.add_stream(stream, name=...)` without `size` | Header placeholders written; data streamed; descriptor appended with final CRC and sizes |
| Result read by standard ZIP tool | Archive is valid |

### Requirement: Decode unflagged ZIP member names by UTF-8-validity sniff

The ZIP backend SHALL decode a member name whose general-purpose bit 11 (UTF-8/EFS flag) is
**clear** by first attempting UTF-8, and only falling back to a configurable legacy encoding
(default cp437, per APPNOTE) when the bytes are not valid UTF-8. This sniff SHALL apply only
in the absence of an authoritative encoding signal: a set bit 11 SHALL be honored as UTF-8,
and an explicit caller-supplied `encoding=` SHALL be used verbatim and SHALL disable the
sniff. When the sniff selects a non-default encoding — i.e. UTF-8 for an unflagged name — the
backend SHALL emit a `diagnostics` warning identifying the member and the chosen encoding, so
the decision is observable and escalatable via `DiagnosticPolicy`. Decoding SHALL NOT raise a
bare `UnicodeDecodeError`; the fallback encoding (cp437 by default) decodes every byte.

#### Scenario: UTF-8 bytes without the flag

- **WHEN** an archive stores a member name as valid UTF-8 bytes (e.g. `Español.txt`,
  `emoji_😀.txt`) with bit 11 **clear** and the caller passes no `encoding=`
- **THEN** the member name is decoded as UTF-8 (`Español.txt`, `emoji_😀.txt`), not cp437
  mojibake, and a diagnostic records that UTF-8 was inferred for an unflagged name

#### Scenario: Legacy bytes without the flag

- **WHEN** an unflagged member name is not valid UTF-8
- **THEN** it is decoded with the configured legacy fallback (default cp437), and no bare
  `UnicodeDecodeError` escapes

#### Scenario: Authoritative signal disables the sniff

- **WHEN** bit 11 is set, **or** the caller passed an explicit `encoding=`
- **THEN** the name is decoded as UTF-8 (flag) or with the caller's `encoding` respectively,
  with no sniff and no override diagnostic
