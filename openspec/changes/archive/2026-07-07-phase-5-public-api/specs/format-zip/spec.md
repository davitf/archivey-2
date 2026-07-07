# format-zip — Phase 5 deltas

## MODIFIED Requirements

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
