# seekable-decompressor-streams — blocked gzip + indexed_gzip delta

## ADDED Requirements

### Requirement: Native random access for blocked gzip (BGZF / mgzip)

The system SHALL provide zero-dependency random access within **blocked gzip** streams —
BGZF and mgzip — by reading the per-block size recorded in each gzip member's extra field,
without decompressing to find block boundaries. Blocked gzip is a sequence of independent
standalone gzip members; the system SHALL build a seek-point index by walking the members
(each member's compressed size comes from its extra subfield; its uncompressed size from
its gzip `ISIZE` trailer) and SHALL serve a seek by decoding only the member(s) containing
the target offset. Recognition is by the member's extra subfield: BGZF's `BC` subfield
(`BSIZE`, ≤64 KiB blocks) or mgzip's `MZ` subfield (4-byte compressed-member-size body).
A gzip stream with **no** recognized blocked-gzip subfield is not treated as blocked; it
falls back to the sequential path (or an accelerator, if enabled). Because blocked gzip is
valid gzip, the same bytes remain decompressible by any gzip tool.

When the blocked-gzip index is available, the reader SHALL report random-access capability
in its cost (non-SOLID `access_cost` and a `seekable` flag), per
`format-single-file-compressors`.

#### Scenario: seeking within a BGZF file via the BC subfields

- **WHEN** a seekable source whose first gzip member carries a `BC` extra subfield is opened
- **THEN** the system walks the members using each block's `BSIZE` to build a block index without decompressing
- **AND** a subsequent seek to an arbitrary uncompressed offset decodes only the block(s) containing that offset

#### Scenario: seeking within an mgzip file via the MZ subfield

- **WHEN** a seekable source whose gzip members carry the mgzip `MZ` extra subfield is opened
- **THEN** the system builds the block index from the per-member compressed sizes and serves seeks by decoding only the needed member(s)

#### Scenario: a plain gzip stream is not treated as blocked

- **WHEN** a gzip stream has no recognized blocked-gzip extra subfield
- **THEN** the native blocked-gzip path does not engage; the stream is served sequentially (or by an accelerator, if enabled), exactly as today

### Requirement: indexed_gzip as an alternative gzip accelerator

The system SHALL support `indexed_gzip` (a zlib `zran`-based backend) as an alternative to
`rapidgzip` for random access within **arbitrary** gzip streams, resolved by the same
access-mode-aware configuration as the other accelerators and gated by the `[seekable]`
extra. When enabled and installed it builds an index of seek points on demand; it MAY
support importing/exporting that index to a persistent file. When it is absent or disabled,
behavior is unchanged (sequential, with the rewind warning). `indexed_gzip` requires a
seekable source.

#### Scenario: gzip random access via indexed_gzip

- **WHEN** the `indexed_gzip` backend is enabled and installed and a seekable gzip source is opened
- **THEN** a seek to an arbitrary uncompressed offset is served without decompressing from the start

#### Scenario: indexed_gzip backend absent

- **WHEN** `indexed_gzip` is not installed or is disabled
- **THEN** gzip random access falls back to any other enabled accelerator, else to the sequential path (which warns once on a rewind)
