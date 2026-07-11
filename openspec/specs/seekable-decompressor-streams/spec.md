# Seekable Decompressor Streams

## Purpose

Archivey (DEV) provides a subsystem that gives random access inside single-file compressed streams — formats that would otherwise require full decompression from the start. This is achieved by exploiting format-native index structures and optional accelerator backends, enabling use cases such as cheaply reading the last member of a multi-gigabyte `.tar.xz`.

## Requirements

### Requirement: Seekable random access via format-native indexes

The system SHALL support seekable random access within XZ and lzip compressed streams by reading the index structures embedded in those formats. For XZ, this is done by parsing the XZ stream footer and block index, which records the uncompressed offset of each block without requiring full decompression. For lzip, this is done by scanning the lzip trailer at the end of the stream. These index-based approaches make it possible to seek to an arbitrary uncompressed offset by decompressing only the block(s) that contain it.

#### Scenario: seeking within an XZ stream using the block index

- **WHEN** a seekable source containing an XZ-compressed stream is opened
- **THEN** the system reads the XZ stream footer and block index to construct a mapping from uncompressed offsets to compressed block positions
- **AND** a subsequent seek to an arbitrary uncompressed offset decompresses only the block(s) containing that offset, not the entire stream from the start

#### Scenario: seeking within a lzip stream using the trailer scan

- **WHEN** a seekable source containing a lzip-compressed stream is opened
- **THEN** the system scans the lzip trailer to locate block boundaries
- **AND** a subsequent seek to an arbitrary uncompressed offset decompresses only the required block(s)

### Requirement: Optional accelerator backends for gzip and bzip2 random access

The system SHALL support optional accelerator backends for formats that have no native block index, using the `rapidgzip` library as the **single** accelerator backend for both codecs: gzip via `rapidgzip.RapidgzipFile` and bzip2 via rapidgzip's bundled `rapidgzip.IndexedBzip2File`. The system SHALL NOT use the standalone `indexed_bzip2` package — loading both `rapidgzip` and `indexed_bzip2` into one process corrupts the heap and aborts on macOS (their statically-linked C++ cores share symbols that collide under dyld), and the library author's own guidance is to "depend on rapidgzip" when both gzip and bzip2 are needed. These backends are opt-in (controlled by `use_rapidgzip` and `use_indexed_bzip2` configuration flags, tri-state `AUTO`/`ON`/`OFF` resolved against the caller's access mode — the `streaming` flag; `use_indexed_bzip2` now selects rapidgzip's bundled bzip2 decoder). When the accelerator is not available or not enabled, gzip and bzip2 streams stay backed by the stdlib decoders, which still support seeking but service it by re-decompressing from the start (O(n) per rewind). The slow path is permitted — not every format can offer fast random access, and a slow seek beats failing — but it MUST NOT be silent: a seek that rewinds the stream SHALL log a warning naming the `[seekable]` accelerator (`rapidgzip`).

#### Scenario: gzip random access with rapidgzip enabled

- **WHEN** `use_rapidgzip` is enabled and the `rapidgzip` package is installed
- **THEN** a gzip-compressed stream supports seeking to arbitrary uncompressed offsets without decompressing from the start

#### Scenario: bzip2 random access via rapidgzip's bundled IndexedBzip2File

- **WHEN** `use_indexed_bzip2` is enabled and the `rapidgzip` package is installed
- **THEN** a bzip2-compressed stream supports seeking to arbitrary uncompressed offsets without decompressing from the start, using `rapidgzip.IndexedBzip2File` (the standalone `indexed_bzip2` package is never imported)

#### Scenario: accelerator backend absent

- **WHEN** `rapidgzip` is not installed, or the corresponding flag is `OFF`
- **THEN** gzip and bzip2 streams stay backed by the stdlib decoders, which service a seek only by re-decompressing from the start (O(n) per rewind)
- **AND** a seek that rewinds the stream logs a warning naming the `[seekable]` accelerator, rather than degrading silently or failing

### Requirement: Accelerator backends surface corruption and truncation uniformly

The system SHALL surface corrupt or truncated input read through the `rapidgzip`
accelerator (driving gzip via `RapidgzipFile` and bzip2 via its bundled
`IndexedBzip2File`) as the same `compressed-streams` error types as the stdlib path —
`CorruptionError` or `TruncatedError` — never as a raw third-party exception, per the
error-translation contract. The accelerator has its own exception taxonomy that differs
from the stdlib decoders' and **varies by platform** (e.g. `rapidgzip` raises a
`RuntimeError` "Failed to parse gzip/zlib header" on Linux but a `ValueError` "… Invalid
gzip magic bytes" on macOS for the same corrupt input).

Truncation needs extra care for `rapidgzip`: it does **not** reliably report a truncated
gzip — it raises for a cut that leaves a partially-decodable block, but for other cuts it
silently returns short or zero output (its end-of-file condition does not always reach the
caller). The system SHALL backstop this: when a gzip is read through `rapidgzip` from a
seekable source, a full read to EOF compares the decompressed length (mod 2³²) against the
gzip ISIZE trailer and raises `TruncatedError` on a mismatch. A concatenated multi-member
gzip (whose trailer records only the *last* member's size) is disambiguated by a
conservative scan for a further gzip header, so a valid file is never misreported.

#### Scenario: corrupt input through an accelerator is translated

- **WHEN** a corrupt gzip is read through `rapidgzip` (or a corrupt bzip2 through `indexed_bzip2`)
- **THEN** a `CorruptionError` is raised (the raw accelerator exception is never propagated), on every platform

#### Scenario: truncated gzip through rapidgzip is reported

- **WHEN** a truncated gzip is read to EOF through `rapidgzip` from a seekable source
- **THEN** the read raises `TruncatedError` (via the ISIZE backstop) or `CorruptionError` (when the accelerator itself detects the cut) — never a silent short read

#### Scenario: a valid multi-member gzip is not misreported as truncated

- **WHEN** a concatenated multi-member gzip is read through `rapidgzip`
- **THEN** it decompresses fully with no error, because the ISIZE backstop disambiguates the multi-member case rather than flagging the size mismatch

The accelerator spawns **C++ worker threads** (invisible to Python's `threading` module). Two
distinct failure modes must be handled for the accelerator to be safe at interpreter shutdown:

1. **Finalization without close.** A worker thread still running when the interpreter finalizes
   trips the library's own guard and aborts the process with SIGABRT. Stopping the thread requires
   **closing** the object: `join_threads()` alone does not stop it — only `close()` does. The
   owning object must therefore be closed before it is freed, and never left to the garbage
   collector unclosed, which on a reference cycle (e.g. an exception traceback capturing the
   stream) can finalize it without closing. The system SHALL guarantee this with a
   `weakref.finalize` guard per accelerator stream: it **closes** the raw object exactly once —
   when the wrapper is collected (cyclically or not) or at interpreter exit, whichever comes first
   — holding a strong reference to the raw object so the close always completes before it is freed.

2. **Two accelerator libraries in one process.** Loading both `rapidgzip` and the standalone
   `indexed_bzip2` into one process corrupts the heap and aborts (a `malloc … pointer being freed
   was not allocated` double-free) on macOS, because their statically-linked C++ cores share weak
   symbols that dyld coalesces across the two dynamic libraries. The system SHALL therefore use
   `rapidgzip` as the only accelerator library — driving bzip2 through its bundled
   `IndexedBzip2File` — and SHALL NOT import `indexed_bzip2`. This matches the library author's
   guidance ("if you need to use both, depend on rapidgzip"). With a single accelerator library in
   the process the collision cannot occur.

With both measures in place the accelerator runs cleanly on every platform, so `AUTO` MAY select
it for random access on every platform (a forward-only `streaming=True` pass needs no seeking and
stays on the sequential backend). See `docs/known-issues.md`.

#### Scenario: a leaked accelerator stream does not crash at shutdown

- **WHEN** a process opens an accelerator-backed stream through the library and exits, or lets the garbage collector reclaim it (including via a reference cycle), without closing it explicitly
- **THEN** the process terminates cleanly on every platform, because the `weakref.finalize` guard closes the raw object before it is freed, rather than aborting from a worker thread still running at interpreter finalization

#### Scenario: gzip and bzip2 accelerated in the same process do not corrupt the heap

- **WHEN** a process decompresses both a gzip stream and a bzip2 stream through the accelerators
- **THEN** both are served by `rapidgzip` alone (bzip2 via `rapidgzip.IndexedBzip2File`), the standalone `indexed_bzip2` package is never imported, and the process exits cleanly rather than aborting from a cross-library heap double-free

#### Scenario: AUTO does not select an accelerator on macOS

- **WHEN** a gzip or bzip2 stream is opened for random access on macOS with the accelerator mode left at `AUTO`
- **THEN** the sequential stdlib backend is used (no accelerator), because the full-process shutdown abort on macOS is not yet resolved — a rewinding seek is serviced slowly (and warns) rather than risking a crash

### Requirement: Index-less codecs warn on a rewinding seek

When an index-less codec first services a backward seek by re-decompressing from the
start, the system SHALL emit `STREAM_REWIND_REDECOMPRESSES` with codec, before/after
offsets, and accelerator name (or `None`) in typed context. It SHALL be emitted at most
once per stream, matching the existing warning's transition semantics; exact counts
therefore report affected streams, not every seek call.

The event SHALL live on the stream operation and cumulative owning-reader aggregate, never
on `CostReceipt` or `ArchiveInfo`. Diagnostic policy controls logging, callback delivery,
and escalation. Forward/no-op seeks SHALL emit nothing.

This applies to gzip/bzip2 when their accelerator is unavailable or disabled and to
brotli, lz4, zstd, and zlib, which have no random-access index. Gzip/bzip2 context names
the `[seekable]` accelerator; the other codecs record no accelerator. XZ, lzip, and
unix-compress use their own indexes and SHALL not emit this event for indexed seeks.

With the stdlib zstd backend (`compression.zstd` / `backports.zstd`), zstd rewinds **in
place** like the other index-less codecs (no reopen-from-source special case).

#### Scenario: repeated rewinds emit once for the stream

- **WHEN** one index-less stream performs 1,000 backward seeks
- **THEN** it emits one `STREAM_REWIND_REDECOMPRESSES` occurrence, later rewinds emit no duplicate, and no open-time metadata object changes

#### Scenario: raised rewind halts at the seek

- **WHEN** `STREAM_REWIND_REDECOMPRESSES` resolves to `RAISE`
- **THEN** the backward seek's diagnostic is delivered and `DiagnosticRaisedError` is raised from that seek operation

#### Scenario: forward seek has no rewind event

- **WHEN** an index-less stream seeks only forward or to its current position
- **THEN** no `STREAM_REWIND_REDECOMPRESSES` occurrence is emitted

#### Scenario: zstd rewinds in place via the stdlib backend

- **WHEN** a zstd stream is read forward and then seeked backward
- **THEN** the stdlib `ZstdFile` services the rewind by re-decompressing from the start (no reopen-from-source special case) and emits one `STREAM_REWIND_REDECOMPRESSES` occurrence

### Requirement: Optional seek-index degradation is diagnostic data

When an XZ/lzip backward index or trailer scan fails in a way for which the stream can
safely fall back to sequential decompression, the system SHALL emit
`SEEK_INDEX_DEGRADED` with codec, scan kind, and public failure type in typed context.
The occurrence SHALL be aggregate-only on the stream/reader operation.

If policy escalates the code, the stream SHALL halt with `DiagnosticRaisedError` instead
of taking the fallback. Genuine corruption that already makes decoding unsafe remains its
typed read exception rather than a diagnostic.

#### Scenario: recoverable XZ index failure falls back under default policy

- **WHEN** an XZ backward index scan fails but sequential decompression remains valid
- **THEN** `SEEK_INDEX_DEGRADED` is collected/logged and the stream uses sequential fallback

#### Scenario: unsafe corruption remains an error

- **WHEN** the same corruption prevents correct sequential decoding
- **THEN** the appropriate `CorruptionError`/`TruncatedError` is raised rather than converting the failure to a recoverable diagnostic
