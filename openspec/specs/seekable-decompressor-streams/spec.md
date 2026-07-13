# Seekable Decompressor Streams

## Purpose

Archivey provides random access inside single-file compressed streams when the
caller declares seekability. The subsystem uses native indexes where formats
provide them and optional rapidgzip accelerators where they do not, while keeping
forward-only streams free of seek machinery.

## Related specs

| Spec | Relationship |
| --- | --- |
| `compressed-streams` | Public `open_stream(..., seekable=...)` and codec backend dispatch |
| `archive-reading` | `MemberStreams.SEEKABLE` on archive member streams |
| `access-mode-and-cost` | Declared capabilities vs access modes |
| `diagnostics` | Rewind and seek-index diagnostic policy/retention |
| `error-handling` | Codec exception translation and `DiagnosticRaisedError` |

## Requirements

### Requirement: Seek machinery is demand-driven

The system SHALL construct seek support only when seekability is declared:
`MemberStreams.SEEKABLE` on `open_archive()` or `seekable=True` on the
single-stream API. Undeclared streams SHALL NOT parse XZ footers, scan lzip
trailers, instantiate rapidgzip accelerators, retain rewind buffers, or retain
seek-point tables; they are forward-only.

`use_rapidgzip` and `use_indexed_bzip2` SHALL resolve their `AUTO` / `ON` / `OFF`
configuration against declared seek demand, not the `streaming` access-mode
proxy. For declared-seekable streams, native XZ/lzip indexes, rapidgzip-backed
gzip/bzip2 indexes, and stdlib fallback rewinds retain their contracts. The
stdlib O(n)-per-rewind path MAY serve the seek but MUST emit the documented
slow-rewind diagnostic/warning.

#### Scenario: demand matrix

| Case | Expected |
| --- | --- |
| gzip/xz/bzip2/lzip opened without seekability under `AUTO` | No index, no accelerator, forward-only |
| Same stream opened with seekability, `AUTO`, accelerator installed | Accelerator or native index provides random access |
| Declared-seekable gzip without accelerator, caller seeks backward | Seek re-decompresses from start and warns/names `[seekable]` accelerator |

### Requirement: XZ and lzip use format-native indexes

The system SHALL support random access in XZ and lzip by reading their embedded
index structures. XZ SHALL use the footer and block index to map uncompressed
offsets to compressed block positions. Lzip SHALL scan trailers to locate block
boundaries. A seek SHALL decompress only the block range needed for the requested
uncompressed offset.

#### Scenario: native index matrix

| Case | Expected |
| --- | --- |
| Seekable XZ source opens | Footer/block index maps uncompressed offsets to compressed positions |
| Seek within XZ | Only containing block range is decompressed |
| Seekable lzip source opens | Trailer scan locates block boundaries |
| Seek within lzip | Only required block range is decompressed |

### Requirement: Gzip and bzip2 random access use rapidgzip only

The system SHALL use `rapidgzip` as the only optional accelerator library for
both gzip and bzip2: gzip through `rapidgzip.RapidgzipFile`, bzip2 through
`rapidgzip.IndexedBzip2File`. It MUST NOT import the standalone `indexed_bzip2`
package because loading both C++ cores in one process can corrupt the heap on
macOS. `use_indexed_bzip2` remains the bzip2 configuration flag but selects the
rapidgzip-bundled decoder.

When rapidgzip is unavailable or disabled, gzip and bzip2 SHALL use stdlib
decoders; backward seek is serviced by re-decompressing from the start and MUST
not degrade silently.

#### Scenario: accelerator matrix

| Case | Expected |
| --- | --- |
| `use_rapidgzip` enabled and package installed | gzip seeks without full re-decompression |
| `use_indexed_bzip2` enabled and rapidgzip installed | bzip2 uses `rapidgzip.IndexedBzip2File`; standalone `indexed_bzip2` never imports |
| rapidgzip absent or flag `OFF` | stdlib decoder; backward seek re-decompresses and warns |

### Requirement: Accelerator errors translate uniformly

The system SHALL translate corrupt/truncated input from rapidgzip-backed gzip and
bzip2 into the same `compressed-streams` errors as stdlib paths:
`CorruptionError` or `TruncatedError`, never raw third-party exceptions. This
translator SHALL account for platform-varying rapidgzip exception types/messages.

For seekable-source gzip through rapidgzip, the system SHALL backstop truncation
by comparing full-read decompressed length modulo 2^32 with the gzip ISIZE
trailer. A conservative multi-member scan SHALL prevent valid concatenated gzip
streams from being misreported when the trailer records only the last member.

#### Scenario: accelerator error matrix

| Case | Expected |
| --- | --- |
| Corrupt gzip/bzip2 through rapidgzip | `CorruptionError`; raw accelerator exception never escapes |
| Truncated gzip through rapidgzip from seekable source | `TruncatedError` via ISIZE backstop or `CorruptionError` from accelerator; never silent short read |
| Valid concatenated multi-member gzip | Decompresses fully without false truncation |

### Requirement: Accelerator lifecycle is safe at shutdown

The system SHALL protect rapidgzip streams with a `weakref.finalize` guard that
closes the raw object exactly once when the wrapper is collected or at
interpreter exit. The guard MUST hold a strong reference to the raw object until
close completes, because `join_threads()` alone does not stop rapidgzip's C++
worker thread and an unclosed worker can abort the process during finalization.

The system SHALL keep one accelerator library in process by using rapidgzip for
both gzip and bzip2 and never importing standalone `indexed_bzip2`. With these
measures, `AUTO` MAY select rapidgzip for declared random access on every
platform.

#### Scenario: accelerator safety matrix

| Case | Expected |
| --- | --- |
| Accelerator stream is leaked, cyclically collected, or process exits | Finalizer closes raw object before free; process terminates cleanly |
| Process accelerates gzip and bzip2 | Both use rapidgzip; standalone `indexed_bzip2` is absent; no cross-library heap double-free |
| Declared random access with `AUTO` and rapidgzip available | rapidgzip may be selected on every platform |

### Requirement: Index-less rewinds emit diagnostic data

When an index-less codec first services a backward seek by re-decompressing from
the start, the system SHALL emit `STREAM_REWIND_REDECOMPRESSES` with codec,
before/after offsets, and accelerator name or `None`. It SHALL emit at most once
per stream. Forward/no-op seeks SHALL emit nothing.

The event SHALL live on the stream operation and cumulative owning-reader
aggregate, never on `CostReceipt` or `ArchiveInfo`. Gzip/bzip2 context names the
`[seekable]` accelerator when relevant; brotli, lz4, zstd, and zlib record no
accelerator. XZ, lzip, and unix-compress indexed seeks SHALL NOT emit this event.
Stdlib zstd SHALL rewind in place like other index-less codecs.

#### Scenario: slow rewind matrix

| Case | Expected |
| --- | --- |
| One index-less stream performs many backward seeks | One `STREAM_REWIND_REDECOMPRESSES` occurrence; later rewinds no duplicate |
| Rewind diagnostic resolves to `RAISE` | `DiagnosticRaisedError` is raised from that seek |
| Only forward/no-op seeks occur | No rewind occurrence |
| zstd stream rewinds via stdlib backend | Re-decompresses from start in place and emits one occurrence |

### Requirement: Recoverable seek-index degradation is diagnostic data

When an XZ/lzip backward index or trailer scan fails but sequential
decompression remains safe, the system SHALL emit `SEEK_INDEX_DEGRADED` with
codec, scan kind, and public failure type, then use sequential fallback unless
policy escalates. The occurrence SHALL be aggregate-only on stream/reader
operation summaries. Unsafe corruption SHALL remain a typed
`CorruptionError`/`TruncatedError`, not a recoverable diagnostic.

#### Scenario: seek-index degradation matrix

| Case | Expected |
| --- | --- |
| Recoverable XZ index scan failure | `SEEK_INDEX_DEGRADED` collected/logged; stream falls back sequentially |
| Same issue resolves to `RAISE` | `DiagnosticRaisedError`; no fallback |
| Corruption prevents correct sequential decoding | `CorruptionError` / `TruncatedError`, not diagnostic fallback |

### Requirement: Unix-compress uses CLEAR seek points

When seekability is declared and the compressed source is seekable, the system
SHALL register `SeekPoint`s at stream start (after the 3-byte `.Z` header) and at
each LZW CLEAR realignment. A seek SHALL resume from the nearest preceding
seek point with an empty dictionary and MUST NOT emit
`STREAM_REWIND_REDECOMPRESSES`.

Forward decode SHALL NOT call `seek` on the compressed source: CLEAR bit-block
realignment MUST use a bounded in-memory buffer. When seekability is not
declared, the system SHALL NOT retain a CLEAR seek-point table. When the source
is not seekable, the decompressor stream SHALL report `seekable() is False` and
`seek` SHALL raise `io.UnsupportedOperation`.

Unix-compress has no length or checksum trailer: source EOF SHALL end the stream
successfully even if a partial trailing code remains; the system MUST NOT raise
`TruncatedError` solely because the bitstream ended mid-code.

#### Scenario: unix-compress seek matrix

| Case | Expected |
| --- | --- |
| Seekable `.Z`, `seekable=True`, seek backward across a CLEAR | Resumes from CLEAR/`SeekPoint`; no rewind diagnostic |
| Seekable `.Z`, `seekable=False` | Forward-only; no CLEAR table retained |
| Non-seekable `.Z` pipe, forward read | Decompresses; `seekable()` false |
| Truncated `.Z` (cut bitstream) | Yields fewer bytes; no `TruncatedError` |
| Corrupt LZW codes | `CorruptionError` |
