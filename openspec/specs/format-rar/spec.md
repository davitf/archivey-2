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
| Open RAR 1.5 / 2.x archive (extract version ≤ 20) | Members and metadata come from native headers; data via `unrar`; no `UnsupportedFeatureError` for extract version alone |
| Open RAR3/RAR4 archive whose members advertise extract version 20 | Listing and reads succeed (stored/small members often carry `unp_ver=20`) |
| Open RAR4 archive | Members and metadata come from native headers |
| Open RAR5 archive | Members, flags, hashes, and redirect metadata come from native headers |
| `unrar` missing during listing | Listing succeeds unless header decryption needs unavailable crypto/password |
| Extract version ≤ 20 alone | No `UnsupportedFeatureError` |

### Requirement: Bound RAR parser member tables at open

The native RAR header walk SHALL refuse to retain more than `1_048_576` logical
members (same default as `ListingLimits.max_members`) and raise a typed error
when that ceiling is crossed. This is defense-in-depth against allocation during
`open_archive()` for indexed RAR backends that build the full member table up
front.

Spine `ListingLimits` (`archive-reading`) still apply when members are
registered into a materialized list and raise `ResourceLimitError` when
configured caps are exceeded. Archives within the parser ceiling but over the
reader's `listing_limits` MUST still fail at `members()` / extract-prep
materialization rather than requiring a separate open-time listing-limits
failure. Open MAY therefore allocate up to the parser ceiling before listing
caps are evaluated.

#### Scenario: RAR parser bound matrix

| Case | Expected |
| --- | --- |
| Hostile archive past the parser member ceiling | Fail during parse / open; no giant member table |
| Archive within parser bounds but over `listing_limits.max_members` | Open may succeed; `members()` / materialization raises `ResourceLimitError` |
| Default limits, typical archive | Open and listing succeed |

### Requirement: Expose RAR file-version history members

The system SHALL include RAR file-version history FILE blocks in the member list
instead of omitting them. RAR5 extra type `0x04` (and RAR3 `FILE_VERSION` when
present) identifies a prior revision. History members SHALL use the WinRAR /
`unrar` presented name `path;n` (version `n != 0`), set
`extra["rar.file_version"] = n`, and set `is_current=False`. The live revision of
the same archive path (no version extra, or version 0) SHALL keep the plain path
name and `is_current=True`.

`open` / `read` of a history `FILE` SHALL return that revision’s bytes. For
`unrar`-backed reads the backend SHALL request the exact presented member name
(`path;n`). Solid ALL-pipe demux SHALL pass `unrar`’s `-ver` switch when the
member list contains any versioned payload FILE so the pipe includes history
bytes in archive order; otherwise solid demux MAY omit `-ver`.

Default `extract` / `extract_all` SHALL skip history rows through the existing
`is_current=False` coordinator behavior (`safe-extraction`), recording each as
`ExtractionStatus.SUPERSEDED`. History rows have unique `path;n` presentation
names, so the shared last-entry-wins pass leaves their backend `is_current=False`
untouched. History rows SHALL count toward listing / parser member ceilings like
any other FILE.

#### Scenario: file-version matrix

| Case | Expected |
| --- | --- |
| RAR5 `-ver` archive with revisions 1..k then live path | Members include `path;1`…`path;k` (`is_current=False`) and `path` (`is_current=True`) |
| `read("path;1")` / `open` that member | Bytes of revision 1 |
| `read("path")` | Bytes of the live revision |
| `extract_all` default | Writes live `path` only; history rows `SUPERSEDED` |
| Solid archive that includes versioned payload FILEs | ALL-pipe demux uses `-ver`; stream order stays aligned |
| Nonsolid named `unrar p` of `path;n` | Exact member name; `-ver` not required |
| Hostile archive with many version rows | Rows count toward member caps |

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

### Requirement: Constrain unrar argv by call site

The system SHALL invoke RARLAB `unrar` with member path arguments only as follows:

| Call site | Member path args after the archive |
| --- | --- |
| Solid `stream_members()` / solid `_iter_with_data` | none (unnamed `unrar p -inul <archive>`) |
| `_open_member` for a FILE member | exactly one archive-relative member path |
| Stored M0 unencrypted member | `unrar` not invoked |

The system MUST NOT pass multiple member paths, globs, or `@listfile` filters in this
capability’s initial implementation. Hardlink / file-copy members are never named on the
`unrar` command line; the shared link-following layer opens the target FILE instead.

#### Scenario: unrar argv matrix

| Case | Expected |
| --- | --- |
| Solid full or filtered `stream_members()` | One `unrar p` with no member path args |
| Nonsolid `open()` / lazy stream of a FILE | `unrar p … <archive> <member>` |
| `open()` on hardlink / `FILE_COPY` | `unrar` receives the target FILE path only (after link follow), or equivalent target open |
| Symlink member | No `unrar` data read for the link payload |

### Requirement: Stream solid RAR archives through one unrar pipe

For solid-archive `stream_members()`, the system SHALL run one
`unrar p -inul <archive>` subprocess **with no member path arguments** and
demultiplex stdout into per-member streams using the unpacked sizes of
**payload FILE members only** (members whose content `unrar p` emits).
Symlinks, hardlinks, file-copies, and directories MUST NOT consume pipe bytes
even when native headers advertise a non-zero size. Demultiplexing SHALL use
`SolidBlockReader` (or equivalent forward-only slicing). The system SHALL
validate each payload member's CRC32 or Blake2sp hash incrementally via the
shared verification stage. This path MUST process the archive once, not spawn
one subprocess per member.

#### Scenario: solid streaming matrix

| Case | Expected |
| --- | --- |
| `stream_members()` on a solid RAR | Exactly one unnamed `unrar p` process |
| Solid archive with symlinks / hardlinks | Pipe length equals Σ payload FILE sizes only; link members yield `stream is None` |
| Member has CRC32/Blake2sp | Verification runs as bytes are read and raises on mismatch |
| A later member is selected in the same pass | Earlier payload bytes are drained through the single pipe, not separate member subprocesses |
| Filtered `stream_members(selector)` | Still one unnamed `unrar p`; unselected payload tails are skipped via the shared iterator close/`SolidBlockReader` lazy skip |

### Requirement: Serve random access and extraction with bounded explicit temp use

The system SHALL serve non-solid random reads by invoking `unrar` for the target
member **with that member's path as the sole path argument**, doing O(member_size)
data work. For solid random reads, the system SHALL decode from archive start to
the target member (named `unrar p … <member>`) or extract once with `unrar x`
into an explicitly managed temporary directory and serve later reads from disk;
that directory is cleaned up on reader close. `extract_all()` MAY use one
`unrar x` to a temporary directory. Any temp materialization SHALL be a declared
RAR strategy, not an implicit in-memory buffer. Mixed-password nonsolid archives
MUST NOT demultiplex one unnamed `unrar p` ALL pipe against the full member list
(wrong-password members are omitted from stdout and would desynchronize sizes).

#### Scenario: random/extract matrix

| Case | Expected |
| --- | --- |
| Random `open()` in non-solid RAR | `unrar p … <archive> <member>`; work is O(member_size) |
| Repeated random opens in solid RAR | Backend may use one tempdir extraction and remove it on close |
| `extract_all()` | Backend may use one-shot `unrar x` |
| Mixed-password nonsolid stream/open | Per-member named `unrar` (or equivalent); no ALL-pipe demux |

### Requirement: Support benchmark-gated small-member optimization

The reader SHALL allow an optional small-member optimization only when
benchmarks justify it. The optimization MAY build a temporary single-file RAR
containing the requested member and invoke `unrar` on that smaller archive when
the member is below a benchmark-derived threshold. Output MUST be byte-identical
to the direct `unrar` path. This optimization is **deferred**: the initial
native RAR reader MUST NOT implement it.

#### Scenario: small-member optimization matrix

| Case | Expected |
| --- | --- |
| Initial native RAR reader | Extract-hack / temp single-file RAR path is not used |
| Future enablement after benchmarks | Bytes match direct `unrar`; measured overhead is lower |
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
Hardlinks and file-copies (`RAR5_XREDIR_HARD_LINK`, `RAR5_XREDIR_FILE_COPY`)
SHALL be exposed as `MemberType.HARDLINK` with `link_target` set from the
redirect, so `ArchiveReader` link following returns the target FILE's data.
Unix symlinks and Windows symlinks/junctions (`RAR5_XREDIR_UNIX_SYMLINK`,
`RAR5_XREDIR_WINDOWS_SYMLINK`, `RAR5_XREDIR_WINDOWS_JUNCTION`) SHALL be
exposed as `MemberType.SYMLINK` with `link_target` from the redirect and
resolved by the format-independent link-following layer. Redirect members MUST
NOT appear in the solid `unrar p` demux size map.

#### Scenario: redirect matrix

| Case | Expected |
| --- | --- |
| RAR5 hardlink / `FILE_COPY` `open()` | Follows to target FILE data |
| RAR5 Unix/Windows symlink `open()` | Link following resolves target; symlink itself has no `unrar p` payload |
| Solid stream past a redirect member | Demux does not advance the pipe for that member |

### Requirement: Resolve RAR link targets when possible at list time

The system SHALL set `ArchiveMember.link_target` during member registration /
`_ensure_link_target` whenever the target is available without interactive input:

| Variant | Source of `link_target` |
| --- | --- |
| RAR5 symlink / Windows symlink / junction | native `file_redir` target string |
| RAR5 hardlink / `FILE_COPY` | native `file_redir` target string (`MemberType.HARDLINK`) |
| RAR4 Unix symlink | stored member bytes (direct read when M0 / readable without `unrar`) |

Encrypted link targets without a usable password MAY leave `link_target` unset and emit
the existing symlink-target diagnostic; listing MUST still succeed.

#### Scenario: link-target resolution matrix

| Case | Expected |
| --- | --- |
| RAR5 symlink | `type=SYMLINK`, `link_target` set from `file_redir` |
| RAR5 hardlink or `FILE_COPY` | `type=HARDLINK`, `link_target` set; `open()` follows to target data |
| RAR4 stored symlink | `link_target` equals stored target bytes decoded as text |
| Encrypted RAR4 symlink, no password | `link_target` may be unset; no crash on list |

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
than silently mis-joining members. Legacy RAR 1.5 / 2.x archives MUST NOT be
rejected solely for extract version ≤ 20. Truly unreadable layouts (corrupt
headers, unknown required crypto without the extra) continue to raise typed
errors from their existing requirements.

#### Scenario: unsupported variant matrix

| Case | Expected |
| --- | --- |
| Multi-volume RAR4/RAR5 set is opened from volume 1 | Handled by the multi-volume requirement |
| Multi-volume set opened from a later volume first | `UnsupportedFeatureError` or truncated/out-of-order error |
| RAR 1.5 / 2.x archive is opened | Listing succeeds; not rejected for extract version |

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
| Open `name.rar` with `name.r00` / `name.r01` siblings | Same as partN: one logical archive; member data spans volumes |
| Read a member spanning volumes | Returned stream reassembles the member across boundaries |
| Open explicit ordered stream volumes | Metadata parses in order; data reads materialize volumes for `unrar` if needed |
| Missing or out-of-order volume | Error instead of partial or garbled output |
