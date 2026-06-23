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

The system SHALL support optional accelerator backends for formats that have no native block index. For gzip, the `rapidgzip` library may be used as a backend to enable random access. For bzip2, the `indexed_bzip2` library may be used. These backends are opt-in (controlled by `use_rapidgzip` and `use_indexed_bzip2` configuration flags, which in v2 will be tri-state `AUTO`/`ON`/`OFF` resolved against the caller's access mode — the `streaming` flag). When neither accelerator is available or enabled, gzip and bzip2 streams stay backed by the stdlib decoders, which still support seeking but service it by re-decompressing from the start (O(n) per rewind). The slow path is permitted — not every format can offer fast random access, and a slow seek beats failing — but it MUST NOT be silent: a seek that rewinds the stream SHALL log a warning naming the `[seekable]` accelerator.

#### Scenario: gzip random access with rapidgzip enabled

- **WHEN** `use_rapidgzip` is enabled and the `rapidgzip` package is installed
- **THEN** a gzip-compressed stream supports seeking to arbitrary uncompressed offsets without decompressing from the start

#### Scenario: bzip2 random access with indexed_bzip2 enabled

- **WHEN** `use_indexed_bzip2` is enabled and the `indexed_bzip2` package is installed
- **THEN** a bzip2-compressed stream supports seeking to arbitrary uncompressed offsets without decompressing from the start

#### Scenario: accelerator backend absent

- **WHEN** neither `rapidgzip` nor `indexed_bzip2` is installed, or the corresponding flag is `OFF`
- **THEN** gzip and bzip2 streams stay backed by the stdlib decoders, which service a seek only by re-decompressing from the start (O(n) per rewind)
- **AND** a seek that rewinds the stream logs a warning naming the `[seekable]` accelerator, rather than degrading silently or failing

### Requirement: Accelerator backends surface corruption and truncation uniformly

An accelerator backend (`rapidgzip`, `indexed_bzip2`) has its own exception taxonomy that
differs from the stdlib decoders' and **varies by platform** (e.g. `rapidgzip` raises a
`RuntimeError` "Failed to parse gzip/zlib header" on Linux but a `ValueError` "… Invalid
gzip magic bytes" on macOS for the same corrupt input). Regardless, corrupt or truncated
input read through an accelerator SHALL surface as the same `compressed-streams` error
types as the stdlib path — `CorruptionError` or `TruncatedError` — never as a raw
third-party exception, per the error-translation contract.

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

The accelerator backends spawn **C++ worker threads** (invisible to Python's `threading`
module). A worker thread still running when the interpreter finalizes trips the library's
own guard and aborts the process with SIGABRT. The thread must therefore be joined while its
owning object is alive — `join_threads()`, then `close()` — and never left to the garbage
collector, which on a reference cycle (e.g. an exception traceback capturing the stream) can
finalize the object in an order that detaches the thread instead of joining it. The system
SHALL guarantee this with a `weakref.finalize` guard per accelerator stream: it runs the join
exactly once — when the wrapper is collected (cyclically or not) or at interpreter exit,
whichever comes first — holding a strong reference to the raw object so the join always
completes before that object is freed.

This deterministic close keeps **Linux** and **Windows** clean even under heavy accelerator
use (including corrupt/truncated reads that raise and form traceback cycles). On **macOS**,
however, the system SHALL NOT select an accelerator under `AUTO`: there the deterministic-close
guarantee did not hold for every stream under real access patterns / GC timing, and the process
still aborted at shutdown — so gzip/bzip2 fall back to the sequential stdlib backend on macOS.
An explicit `ON` is still honoured (the caller's choice, carrying the shutdown-abort risk on
macOS). This is tracked as an upstream issue with a canary test (which measures, in
subprocesses, that a closed+joined object exits cleanly while one finalized by the cyclic GC or
at interpreter shutdown aborts) that flips when a future accelerator release fixes it; see
`docs/known-issues.md`.

#### Scenario: an unclosed accelerator stream does not crash at shutdown (Linux/Windows)

- **WHEN** a process opens an accelerator-backed stream through the library and exits without closing it, on Linux or Windows
- **THEN** the process terminates cleanly, because the `weakref.finalize` guard joins the worker thread before the object is freed, rather than aborting from a thread still running at interpreter finalization

#### Scenario: AUTO does not select an accelerator on macOS

- **WHEN** a gzip or bzip2 stream is opened for random access on macOS with the accelerator mode left at `AUTO`
- **THEN** the sequential stdlib backend is used (no accelerator), so the process cannot abort at shutdown — a rewinding seek is serviced slowly (and warns) rather than crashing

### Requirement: Index-less codecs warn on a rewinding seek

A codec with no random-access index services a backward seek by re-decompressing the
stream from the start — O(n) per rewind. This applies to gzip and bzip2 without an
accelerator (above) and, with no accelerator available at all, to **brotli, lz4, zstd, and
zlib**. zstd's reader cannot seek backward in place, so a backward seek reopens the source
from the start and re-decompresses forward — the same O(n) cost, surfaced the same way,
rather than raising. The slow path is permitted (a slow seek beats failing, and not every
format can offer fast random access), but it SHALL NOT be silent: the first seek that
rewinds such a stream SHALL log a warning via the `archivey` streams logger. Where an
accelerator backend exists (gzip, bzip2) the warning names the `[seekable]` extra; for
brotli/lz4/zstd/zlib, which have no accelerator, it states that the codec re-decompresses
from the start. Forward seeks and no-op seeks do not warn.

Codecs that carry their own index (xz, lzip, unix-compress) seek efficiently and SHALL NOT
warn.

#### Scenario: rewinding an index-less codec warns

- **WHEN** a brotli, lz4, zstd, or zlib stream is read and then seeked backward to an earlier offset
- **THEN** the data is delivered correctly **AND** a warning is logged that the codec re-decompresses from the start (no accelerator is named, because none exists for these codecs)

#### Scenario: a forward-only seek does not warn

- **WHEN** an index-less codec stream is seeked only forward (or to its current position)
- **THEN** no rewind warning is logged
