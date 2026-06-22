# Archive Data Model — delta (Phase 3)

Phase 3 reads standalone `lzip` / `zlib` / `brotli` / `unix-compress` streams, but the
`StreamFormat` enum only defines `gz`/`bz2`/`xz`/`zst`/`lz4`, so those formats cannot be
named. The enum is already documented as extensible ("new outer codecs are added here
(lzip, brotli, …)"); this change makes that concrete. It also sharpens the definition of
`raw_name` to "exactly what the archive stored," so the gzip case (where `name` is
source-derived) has a well-defined home for the stored `FNAME` bytes.

## MODIFIED Requirements

### Requirement: Archive format identity (ArchiveFormat)

The system SHALL model a format as the combination of a **container** and a **stream**
codec, rather than a single flat enum. `ContainerFormat` names the member layout (zip,
tar, 7z, …) and `StreamFormat` names the outer single-stream codec the container is
wrapped in (gzip, xz, … or `UNCOMPRESSED`). `ArchiveFormat` is a frozen
`(container, stream)` dataclass; the familiar named formats (`ZIP`, `TAR_GZ`,
`SEVEN_Z`, …) are predefined class-var instances, so callers keep writing
`ArchiveFormat.TAR_GZ` while the model underneath is compositional.

`StreamFormat` SHALL cover every outer single-stream codec the library can read as a
standalone stream, so that single-file and `tar.<codec>` formats are all expressible:

```python
class StreamFormat(StrEnum):
    UNCOMPRESSED  = "uncompressed"
    GZIP          = "gz"
    BZIP2         = "bz2"
    XZ            = "xz"
    ZSTD          = "zst"     # requires [zstd] extra
    LZ4           = "lz4"     # requires [lz4] extra
    LZIP          = "lz"
    ZLIB          = "zz"
    BROTLI        = "br"
    UNIX_COMPRESS = "Z"       # requires [unix-compress] extra
    # extensible: further outer codecs are added here
```

Named standalone `ArchiveFormat` constants SHALL exist for the bare single-stream
formats — `GZ`, `BZ2`, `XZ`, `ZST`, `LZIP`, `ZLIB`, `BROTLI`, `Z` (each
`RAW_STREAM × <codec>`) — alongside the container constants (`ZIP`, `TAR`, `TAR_GZ`, …).
Container × codec combinations that are **not in common practice** (e.g. `tar.lz`,
`tar.br`) SHALL NOT get a predefined constant; they are constructed on demand as
`ArchiveFormat(container, stream)` and compare equal to any other instance with the same
pair. `file_extension()` derives from the codec for a `RAW_STREAM` (e.g. `Z` → `"Z"`,
`LZIP` → `"lz"`) and from `container.codec` for a container (e.g. `TAR × LZIP` → `"tar.lz"`).

#### Scenario: format identity round-trips through the (container, stream) pair

- **WHEN** a caller compares `ar.format` against `ArchiveFormat(ContainerFormat.TAR, StreamFormat.GZIP)`
- **THEN** it is equal to `ArchiveFormat.TAR_GZ`

#### Scenario: a standalone lzip stream has a named format

- **WHEN** a `.lz` (lzip) stream is opened
- **THEN** `ar.format == ArchiveFormat.LZIP`, whose `container == ContainerFormat.RAW_STREAM` and `stream == StreamFormat.LZIP`

#### Scenario: an uncommon container×codec combination is built on demand

- **WHEN** a `tar.lz` (tar wrapped in lzip) source is opened
- **THEN** `ar.format == ArchiveFormat(ContainerFormat.TAR, StreamFormat.LZIP)` even though no `TAR_LZIP` class constant is predefined
- **AND** `ar.format.file_extension() == "tar.lz"`

### Requirement: ArchiveMember name normalization rules

The system SHALL normalize `ArchiveMember.name` according to a deterministic set of rules, while preserving the verbatim stored bytes in `ArchiveMember.raw_name`. When normalization changes the logical path, a warning SHALL be emitted via the `archivey.normalization` logger.

Normalization rules applied in order:
1. Replace all `\` with `/`.
2. Strip leading `/` and `./`.
3. Collapse `//` and `foo/../bar` sequences.
4. Append `/` for directory members if not already present.
5. Never produce an empty string — the root directory becomes `"."`.

`name` is produced by decoding the stored bytes (using the format's internal encoding
signal where present, otherwise the resolved/auto-detected `encoding`) and then applying
the rules above.

`raw_name` holds **exactly what the archive stored** for the member's name — the
verbatim, encoded, pre-normalization bytes — so the name can be re-decoded losslessly
under a different encoding; it is `None` only when the format exposes no separate raw
form. For formats where the logical `name` is **not** taken from archive content but
derived elsewhere (a single-file compressor, whose `name` comes from the *source
filename*), `raw_name` still holds the archive's stored name when one exists — e.g. a
gzip stream's `FNAME` bytes — so `raw_name` may legitimately differ from a value
`name` would decode to. Treating the source-filename derivation as the "normalization"
step for these formats keeps one rule: `raw_name` is ground truth, `name` is the
normalized presentation.

#### Scenario: backslash conversion

- **WHEN** an archive member is stored with the name bytes `b"foo\\bar\\baz.txt"`
- **THEN** `member.name == "foo/bar/baz.txt"` and `member.raw_name == b"foo\\bar\\baz.txt"`

#### Scenario: traversal sequence collapsed

- **WHEN** an archive member has the name `"foo/../bar"`
- **THEN** `member.name == "bar"` and a warning is emitted via `archivey.normalization`

#### Scenario: raw_name carries the stored name even when name is source-derived

- **WHEN** a `.gz` stream stores `FNAME = "report.csv"` and is opened from a path `archive.gz`
- **THEN** `member.raw_name` holds the undecoded `FNAME` bytes while `member.name == "archive"` (from the source filename), and the decoded `FNAME` is also available in `member.extra["gzip.original_filename"]`
