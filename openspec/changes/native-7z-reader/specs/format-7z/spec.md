## MODIFIED Requirements

### Requirement: Reject genuinely unsupported codecs and variants

The system SHALL raise `UnsupportedFeatureError`, naming the codec, when a folder
uses a coder with no available backend — notably **BCJ2** (`0x0303011B`), a
multi-stream filter not implemented by any standard or available package, or any
newer branch filter absent from the installed liblzma — or an unrecognized method
ID. The system SHALL also raise `UnsupportedFeatureError` for coder combinations
that cannot be decoded **correctly** with the available stdlib/optional backends
without custom non-stdlib filter code (in particular **LZMA1+BCJ** when a validated
stdlib-only composition is not available). The library MUST NOT silently return
incorrect data and MUST NOT fall back to a third-party reader. (PPMd and Deflate64
are supported via the optional `[7z]` extra and are NOT in this category;
multi-volume 7z is supported by volume-joining, see the next requirement, not
rejected. Anti-items are listed and extracted per the anti-item requirement, not
rejected.)

#### Scenario: BCJ2-filtered member

- **WHEN** a member's folder uses the BCJ2 filter
- **THEN** `UnsupportedFeatureError` is raised naming BCJ2, with no garbage output

#### Scenario: unrecognized method ID

- **WHEN** a folder uses a coder whose method ID is not recognized
- **THEN** `UnsupportedFeatureError` is raised naming the unknown method ID

#### Scenario: LZMA1+BCJ without a validated stdlib path

- **WHEN** a folder uses an LZMA1+BCJ coder chain and no validated stdlib-only decode path is implemented
- **THEN** `UnsupportedFeatureError` is raised naming the combination, with no garbage output
- **AND** the limitation is documented for a later improvement (the reader MUST NOT pull in `pybcj` solely for this path)

---

### Requirement: True pull-based streaming with bounded memory

The system SHALL provide `stream_members()` as a true pull stream: each folder is
decoded once, its members yielded in archive order as the decompressor produces
bytes, with no buffering of the whole folder and no background thread or queue.
Peak memory is bounded by the decompressor's working set rather than the folder's
uncompressed size. For random `ar.open()` of a member inside a solid folder, the
backend SHALL decode the folder from its start and skip to the member's substream.
The backend MUST NOT retain decoded folder output in a spool, temporary file, or
unbounded RAM cache — repeated `open()` calls MAY re-decode.

#### Scenario: streaming a solid 7z archive

- **WHEN** a caller iterates a solid 7-Zip archive with `stream_members()`
- **THEN** each folder is decoded once and its members are yielded as a pull stream, with peak memory bounded by the decompressor working set, not the folder size

#### Scenario: random access into a solid folder

- **WHEN** `ar.open(member)` is called for a member inside a multi-file folder
- **THEN** the backend decodes the folder from its start to produce the member's bytes, without writing decoded output to disk and without retaining a decoded-folder cache across opens

---

## ADDED Requirements

### Requirement: List and extract 7z anti-items

The system SHALL parse the `FILES_INFO` ANTI bitmask and expose every anti-item as
an `ArchiveMember` with `is_anti=True` in the member list and during iteration.
Anti-items typically have no payload (`size` 0 / empty stream). The reader SHALL also
compute each member's `is_current` (see `archive-data-model`) from the ANTI bitmask
and same-name shadowing, so a content member superseded by a later anti-item (or by a
later re-add of the same name) is `is_current=False` while the surviving entry is
`is_current=True`. Extraction behavior for anti-items is defined by `safe-extraction`:
superseded content is skipped by default and an anti-item never deletes data the
extraction did not create. The member list MUST remain well-formed when anti-items are
present (no dropped neighbors, no corrupt indices).

#### Scenario: anti-items appear in the member list

- **WHEN** a 7z archive contains one or more anti-items
- **THEN** each anti-item is present in the member list with `is_anti=True`
- **AND** non-anti members remain listed with correct metadata

#### Scenario: opening an anti-item yields no payload

- **WHEN** `ar.open(anti_member)` is called
- **THEN** the returned stream is empty (no file content)

#### Scenario: an anti-item marks the superseded content non-current

- **WHEN** a 7z archive adds a path and a later anti-item deletes it
- **THEN** the content member is `is_current=False`, and the anti-item is `is_anti=True` and `is_current=True`
