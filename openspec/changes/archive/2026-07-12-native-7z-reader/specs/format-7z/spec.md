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

### Requirement: Decode folder coder chains natively

The system SHALL decode each folder's coder chain by composing decompressors —
the common codecs needing only the standard library, with a few less-common ones
provided by small optional packages:

| 7z codec | Method ID | Backend | Availability |
|----------|-----------|---------|--------------|
| STORED | `0x00` | pass-through | core |
| LZMA1 / LZMA2 | `0x030101` / `0x21` | `lzma` `FORMAT_RAW` | core |
| Delta | `0x03` | `lzma.FILTER_DELTA` | core |
| BCJ x86/ARM/ARMT/PPC/SPARC/IA64 | `0x04`–`0x09`, `0x03030103`… | `lzma.FILTER_X86`/`ARM`/… | core |
| Deflate | `0x040108` | `zlib` (raw) | core |
| BZip2 | `0x040202` | `bz2` | core |
| Zstd | `0x04f71101` | stdlib `compression.zstd` / `backports.zstd` | optional `[7z]` on <3.14; core on 3.14+ |
| Brotli | `0x04f71102` | `brotli` | optional `[7z]` |
| PPMd (var.H) | `0x030401` | `pyppmd` | optional `[7z]` |
| Deflate64 | `0x040109` | `inflate64` | optional `[7z]` |
| AES-256 / SHA-256 | `0x06f10701` | crypto backend | optional `[7z]` |
| BCJ2 | `0x0303011B` | — | unsupported (detect & raise) |

Files within a folder are laid out contiguously in the decompressed output, so the
backend produces a member's stream by reading exactly `member.size` bytes, in
order, from the folder's decompressed byte stream. The core codec set requires no
third-party runtime dependency; the `[7z]` bundle adds every optional 7z codec
(PPMd, Deflate64, Zstd, Brotli) and AES decryption in one install. A coder chain is
applied in reverse coder order for decoding (e.g. an `AES → LZMA2` coder list means
decrypt, then decompress). Decoding composes the shared `compressed-streams`
backends — the 7z reader does NOT call codec libraries (`lzma`, `pyppmd`,
`inflate64`, the crypto backend) directly — and the reader verifies each member's
stored CRC32 (`hashes["crc32"]`) via the shared `compressed-streams` verification
stage as it is read.

The BCJ branch filters compose into a single `lzma` `FORMAT_RAW` filter chain when
paired with **LZMA2** (the common 7z executable case, e.g. `BCJ → LZMA2`), so that
pairing is core/supported as the table shows. The **LZMA1+BCJ** pairing is NOT assumed
to decode correctly through a single raw chain — it is governed by the *Reject
genuinely unsupported codecs and variants* requirement above (supported only if a
validated stdlib-only composition exists, otherwise `UnsupportedFeatureError`, never
wrong bytes). "BCJ is core" in the table therefore means the BCJ-over-LZMA2 chain, not
every possible pairing.

#### Scenario: member compressed with a BCJ + LZMA2 chain

- **WHEN** a member lives in a folder coded as BCJ-over-LZMA2
- **THEN** the backend composes the shared `lzma` `FORMAT_RAW` filter-chain backend, decodes the folder, and returns bytes identical to the original file content

#### Scenario: per-member CRC verified on read

- **WHEN** a 7z member that records a CRC32 is decoded
- **THEN** the reader verifies the decompressed bytes against `hashes["crc32"]` and raises `CorruptionError` on mismatch

#### Scenario: PPMd member without the [7z] extra

- **WHEN** a folder is PPMd-compressed and `pyppmd` is not installed
- **THEN** the backend raises `PackageNotInstalledError` naming `pyppmd` (installable via the `[7z]` extra)

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
