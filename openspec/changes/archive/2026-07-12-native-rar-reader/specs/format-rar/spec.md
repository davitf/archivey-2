## ADDED Requirements

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

## MODIFIED Requirements

### Requirement: Stream solid RAR archives through one unrar pipe

For solid-archive `stream_members()`, the system SHALL run one
`unrar p -inul <archive>` subprocess **with no member path arguments** and demultiplex
stdout into per-member streams using the unpacked sizes of **payload FILE members only**
(members whose content `unrar p` emits). Symlinks, hardlinks, file-copies, and
directories MUST NOT consume pipe bytes even when native headers advertise a non-zero
size. Demultiplexing SHALL use `SolidBlockReader` (or equivalent forward-only slicing).
The system SHALL validate each payload member's CRC32 or Blake2sp hash incrementally via
the shared verification stage. This path MUST process the archive once, not spawn one
subprocess per member.

#### Scenario: solid streaming matrix

| Case | Expected |
| --- | --- |
| `stream_members()` on a solid RAR | Exactly one unnamed `unrar p` process |
| Solid archive with symlinks / hardlinks | Pipe length equals Σ payload FILE sizes only; link members yield `stream is None` |
| Member has CRC32/Blake2sp | Verification runs as bytes are read and raises on mismatch |
| A later member is selected in the same pass | Earlier payload bytes are drained through the single pipe, not separate member subprocesses |
| Filtered `stream_members(selector)` | Still one unnamed `unrar p`; unselected payload tails are skipped via the shared iterator close/`SolidBlockReader` lazy skip |

### Requirement: Handle RAR5 redirect link types natively

The system SHALL read RAR5 link semantics from native `file_redir` metadata.
Hardlinks and file-copies (`RAR5_XREDIR_HARD_LINK`, `RAR5_XREDIR_FILE_COPY`) SHALL be
exposed as `MemberType.HARDLINK` with `link_target` set from the redirect, so
`ArchiveReader` link following returns the target FILE's data. Unix symlinks and
Windows symlinks/junctions (`RAR5_XREDIR_UNIX_SYMLINK`, `RAR5_XREDIR_WINDOWS_SYMLINK`,
`RAR5_XREDIR_WINDOWS_JUNCTION`) SHALL be exposed as `MemberType.SYMLINK` with
`link_target` from the redirect and resolved by the format-independent link-following
layer. Redirect members MUST NOT appear in the solid `unrar p` demux size map.

#### Scenario: redirect matrix

| Case | Expected |
| --- | --- |
| RAR5 hardlink / `FILE_COPY` `open()` | Follows to target FILE data |
| RAR5 Unix/Windows symlink `open()` | Link following resolves target; symlink itself has no `unrar p` payload |
| Solid stream past a redirect member | Demux does not advance the pipe for that member |

### Requirement: Support benchmark-gated small-member optimization

The reader SHALL allow an optional small-member optimization only when benchmarks
justify it. The optimization MAY build a temporary single-file RAR containing the
requested member and invoke `unrar` on that smaller archive when the member is below a
benchmark-derived threshold. Output MUST be byte-identical to the direct `unrar` path.
This optimization is **deferred**: the initial native RAR reader MUST NOT implement it.

#### Scenario: small-member optimization matrix

| Case | Expected |
| --- | --- |
| Initial native RAR reader | Extract-hack / temp single-file RAR path is not used |
| Future enablement after benchmarks | Bytes match direct `unrar`; measured overhead is lower |
| Benchmark does not justify the threshold | Optimization is not used |

### Requirement: Serve random access and extraction with bounded explicit temp use

The system SHALL serve non-solid random reads by invoking `unrar` for the target
member **with that member's path as the sole path argument**, doing O(member_size) data
work. For solid random reads, the system SHALL decode from archive start to the target
member (named `unrar p … <member>`) or extract once with `unrar x` into an explicitly
managed temporary directory and serve later reads from disk; that directory is cleaned
up on reader close. `extract_all()` MAY use one `unrar x` to a temporary directory. Any
temp materialization SHALL be a declared RAR strategy, not an implicit in-memory buffer.
Mixed-password nonsolid archives MUST NOT demultiplex one unnamed `unrar p` ALL pipe
against the full member list (wrong-password members are omitted from stdout and would
desynchronize sizes).

#### Scenario: random/extract matrix

| Case | Expected |
| --- | --- |
| Random `open()` in non-solid RAR | `unrar p … <archive> <member>`; work is O(member_size) |
| Repeated random opens in solid RAR | Backend may use one tempdir extraction and remove it on close |
| `extract_all()` | Backend may use one-shot `unrar x` |
| Mixed-password nonsolid stream/open | Per-member named `unrar` (or equivalent); no ALL-pipe demux |

### Requirement: Parse RAR headers natively (RAR 1.5 through RAR5)

The system SHALL parse RAR archive headers natively — including RAR 1.5 / 2.x
archives that advertise extract version ≤ 20, RAR3/RAR4, and RAR5 — to produce
the full member list and per-member metadata. Extract version ≤ 20 MUST NOT by
itself cause rejection: those archives share the same header block layout the
parser already understands, and member data remains RARLAB `unrar`'s
responsibility. RAR3 archives whose stored/small members advertise `unp_ver=20`
MUST list successfully.

#### Scenario: legacy extract-version matrix

| Case | Expected |
| --- | --- |
| Open RAR 1.5 / 2.x archive (extract version ≤ 20) | Members list from native headers; data via `unrar` |
| Open RAR3 archive with a member `unp_ver=20` | Listing and reads succeed |
| Extract version ≤ 20 alone | No `UnsupportedFeatureError` |

### Requirement: Reject unsupported RAR variants clearly

Multi-volume RAR sets SHALL be supported by the volume contract, not rejected as
an unsupported variant. Opening a later volume before the first volume of a set
SHALL raise `UnsupportedFeatureError` (or a truncated/out-of-order error) rather
than silently mis-joining members. Legacy RAR 1.5 / 2.x archives MUST NOT be
rejected solely for extract version ≤ 20.

#### Scenario: unsupported variant matrix

| Case | Expected |
| --- | --- |
| Multi-volume RAR4/RAR5 set is opened from volume 1 | Handled by the multi-volume requirement |
| Multi-volume set opened from a later volume first | `UnsupportedFeatureError` or truncated/out-of-order error |
| RAR 1.5 / 2.x archive is opened | Listing succeeds; not rejected for extract version |
