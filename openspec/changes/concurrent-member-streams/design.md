## Context

The repository contains several layers of concurrency history:

1. `shared-source-streams` made archivey-owned byte ranges independently positioned and
   serialized their seek+read pairs.
2. The archived `parallel-reader-exploration` added a reentrant `_open_member` invariant but
   retained a blanket one-reader-per-thread statement and deferred cache synchronization.
3. A first draft added a public `allow_multiple_open_streams` opt-in gating concurrent
   streams.
4. A second draft removed the gate and made simultaneous streams unconditional, with cost
   informational.

Maintainer review settled the question between drafts 3 and 4 — and widened it. The
performance hazards are stream-*capability*-shaped, not only concurrency-shaped: a
backward seek inside one member stream can cost an O(n) re-decode exactly like an
interleaved second stream can. Both capabilities are therefore governed the same way, by
one declared-capabilities parameter, and the same review introduced a usage-error
hierarchy separate from `ArchiveyError`.

## Goals / Non-Goals

**Goals**

- A default member-stream contract every format serves efficiently: forward-only, one
  live stream, no locks, no seek machinery.
- Uniform gating on every format — the directory reader included — so undeclared use
  fails fast in development regardless of the developer's test corpus.
- Correct simultaneous member streams on every random-access backend once `CONCURRENT`
  is declared, including a narrow cross-thread seam valid on GIL and free-threaded
  CPython.
- Seekable member streams (and `open_stream()` streams) only on declared demand, keying
  the accelerator/index machinery on that demand.
- A precise materialization/publication boundary and deterministic resource lifetime.
- Caller-misuse errors distinguishable from archive/content errors by exception
  hierarchy.

**Non-Goals**

- Full thread safety for the `ArchiveReader` object.
- Concurrent iteration, materialization, extraction coordination, or reader close.
- Parallel extraction or a speed guarantee.
- Gating member *open order*: random opens on a solid archive stay legal and are costed
  by `AccessCost`/`solid_block_count`; `stream_members()` remains the steer.
- Making a single solid block or one TAR/ISO handle execute in parallel.
- Implementing the proposal tasks in this proposal-only change.

## Decisions

### D1. Declared capabilities, uniform default

`open_archive()` gains `member_streams: MemberStreams` (a `Flag` enum) with two bits:

- `MemberStreams.CONCURRENT` — any number of member streams may be open at once.
- `MemberStreams.SEEKABLE` — member streams are seekable where the backend can provide
  it.

Default (no bits): member streams are forward-only and at most one may be live per
reader. "Live" spans `open()` to that stream's `close()`/context exit — not EOF (a
stream may be re-read under `SEEKABLE`), not garbage collection (non-deterministic).
Opening a second overlapping stream raises `ConcurrentAccessError` and leaves the first
stream untouched; the library never silently closes a stream the caller still holds.

The rationale trades a small, deliberate friction for guaranteed discovery:

- **Uniformity is the mechanism.** Gating only the formats where the capability is
  expensive would defer the surprise to production on the unlucky format. Gating every
  format surfaces it in development on whatever format the developer tests first. The
  directory reader is gated too, deliberately: it exists to keep archive-vs-directory
  code uniform, to exercise the API and internals, and to serve future
  directory↔archive piping — it is never more lenient than archive readers
  (`format-directory` documents this as a standing principle).
- **Pre-1.0 asymmetry.** Loosening a strict default later is painless; retrofitting a
  gate onto shipped permissive behavior is a breaking change.
- **The default pays nothing.** No shared-handle lock (TAR/ISO), no SharedSource view
  accounting beyond one stream, no seek-point tables, no accelerator instantiation.
  Declared capability activates its own machinery.
- **Scope honesty.** The gate governs stream capabilities. It does not, and cannot,
  gate the cost of a caller's member open *order* on a solid archive — a sequential
  `open→close` loop in the wrong order re-decodes just as much with zero overlapping
  lifetimes. That remains `AccessCost`/`stream_members()` territory and the
  documentation says so in the same breath as the flag.

`AccessCost.SOLID` and `solid_block_count` remain informational for declared-`CONCURRENT`
readers: a simultaneous schedule on a solid archive is correct but may repeat decode
work.

### D2. One parameter on `open_archive`; no config field; no per-open argument

The archive opener is the party that knows the usage pattern, so the declaration lives
on `open_archive()` alongside `streaming=`. It is not an `ArchiveyConfig` field: config
is ambient policy, capability is per-archive intent. It is not a per-`open()` argument:
overlap is a property of a *pair* of streams, so per-open acknowledgment has no coherent
owner (if stream A was opened without the flag and B with it, the error would blame
whichever call happened to come second, and library-internal opens would need flags of
their own).

**Debugging breadcrumb.** `open_archive()` always records its caller's stack (one
capture at open). `ConcurrentAccessError` includes the caller's `file:line`:
`"…this archive was opened without MemberStreams.CONCURRENT at app.py:42"`. The full
captured stack is retained on the reader for diagnostics; there is no separate
config/debug knob — open-time capture is cheap enough to keep unconditionally.

**`open_stream()` (compressed-streams) matches.** The single-stream entry point takes
`seekable: bool = False` (concurrency is meaningless for one stream; a boolean, not the
flags enum). One rule everywhere: no archivey stream is seekable unless asked. A future
options object can supersede the boolean if `open_stream` grows more knobs.

### D3. `SEEKABLE` semantics and demand-driven seek machinery

Without `SEEKABLE`: every member stream reports `seekable() is False`; any `seek()`
raises `io.UnsupportedOperation` (the standard file-protocol signal that seek-probing
consumers already understand); `tell()` works; forward skip is read-and-discard. This
applies to random `open()` streams and `stream_members()` yields alike, on every format
— a directory member stream is a real file yet still reports non-seekable, per the
uniformity principle.

With `SEEKABLE`: streams are seekable where the backend can provide it (ZIP re-reads,
SharedSource views, native XZ/lzip indexes, rapidgzip-accelerated gzip/bz2, plain file
handles). The existing `seekable-decompressor-streams` rule — a slow O(n)-per-rewind
seek is permitted but MUST NOT be silent — continues to govern the declared-seekable
non-accelerated path.

Demand-driven machinery: the `use_rapidgzip` / `use_indexed_bzip2` `AUTO` resolution
now keys on declared seek demand instead of the access-mode proxy; XZ footer/lzip
trailer index parsing and accelerator instantiation are skipped entirely for undeclared
streams. This is the "skip the seek-point tables" optimization, now driven by an
explicit signal.

### D4. Usage errors are not `ArchiveyError`s

New hierarchy, root `ArchiveyUsageError(Exception)` — deliberately **not** an
`ArchiveyError` subclass:

- `ConcurrentAccessError(ArchiveyUsageError)` — a second overlapping member stream
  without `CONCURRENT` (carries the open-site breadcrumb).
- `ArchiveyUsageError` itself for other detected misuse: reader operations after
  `reader.close()`, detected single-owner overlap (materialization/pass/extraction/
  close), password-provider reentry into a password-requiring operation, and I/O on a
  stream whose caller-owned source was closed early.

The dividing line: `except ArchiveyError` means "the archive, environment, or a
supported limitation did something" and is what applications wrap archive handling in;
a usage error means "the calling code has a bug" and must not be swallowed by that
blanket handler. `UnsupportedOperationError` remains an `ArchiveyError` for
archive/mode/feature limitations (e.g. an operation a format or access mode cannot
provide). Stream-level conventions stay stdlib-shaped: closed-*stream* I/O raises
`ValueError`, unsupported positioning raises `io.UnsupportedOperation` — those are file
protocol, not archivey taxonomy.

### D5. `CONCURRENT` is one capability: interleaved and cross-thread

The bit unlocks both single-thread interleaving and the cross-thread worker seam; the
machinery does not distinguish them and a separate THREADS bit would force users to
reason about a distinction with no behavioral difference. The supported cross-thread
contract (unchanged in substance from the prior draft):

1. One owner completes `members()` / `scan_members()` in random-access mode.
2. The reader atomically publishes the completed internal member tuple and immutable name
   index; list-returning public APIs return copies that cannot mutate those containers.
3. While that snapshot remains published and the reader remains open, any number of
   workers may call `open(member_or_name)` concurrently.
4. Returned streams have independent logical position/state. Different threads may
   operate on different streams via `read`, `readinto`, and `close`, plus `seek`/`tell`
   under `SEEKABLE` where the stream supports it. Each individual stream follows the
   normal Python file rule: simultaneous operations on the **same** stream object
   require caller synchronization.

The guarantee includes link following and name lookup (both consult the immutable
published snapshot). Reader metadata properties may be read before fan-out, but no
blanket concurrent guarantee is made for arbitrary reader methods.

**Internal scopes are exempt from the gate.** `extract_all()` (including its hardlink
recovery pass), symlink-target reads, and password candidate confirmation open members
under library-internal child scopes; no caller flag is ever required to extract. The
gate applies to public `open()` only.

### D6. Materialization is exclusive, lifecycle-independent, and publishes once

Unchanged from the prior draft, condensed: the member cache has its own state machine
(`UNMATERIALIZED`, `MATERIALIZING`, `MATERIALIZED`) protected by a short-lived
materialization lock; one owner builds members/name index locally (link completion
included) and publishes atomically, exactly once; published containers are never
mutated; a failed build discards local state and returns to `UNMATERIALIZED`; a second
operation overlapping the build is rejected as usage error without observing a partial
cache. No callback, decoder, member scan, link-data read, or logging call runs while the
materialization lock is held. `ArchiveMember` late-bound fields keep their existing
semantics with idempotent, synchronized updates.

On an undeclared (default) reader these states still exist but are exercised by one
caller at a time; the synchronization is only load-bearing under `CONCURRENT`.

### D7. Unsupported reader-wide overlap is explicit (operation tokens)

Unchanged in substance from the prior draft: the base reader uses explicit, unforgeable
operation-owner tokens rather than thread identity or a reentrant lock. Public
single-owner entry points (`__iter__`, `members`, `scan_members`,
`get_members_if_available` when initializing, `stream_members`, `extract_all`, reader
`close`) acquire a root token; private helpers enter named child scopes only by
receiving that token (materialization link reads; worker-open name lookup/link
following; extraction's member/counter peeks, `stream_members` child passes, and
yielded-stream I/O). An unrelated public call — even from the same thread or a callback
— has no token and is rejected with a usage error at the later operation, leaving the
earlier operation intact.

Under `CONCURRENT`, random `open()` and each random-stream call hold a short-lived
worker token only for that call; an idle open stream holds a lifecycle lease, not
active ownership, plus a private lease-bound entry capability so its later I/O remains
admissible after `reader.close()`. On a default reader the same states degenerate to
the single live stream.

Backends claimed for a free-threaded runtime must be data-race-free under accidental
overlap (no memory corruption or crash), but behavior beyond the stated detected errors
is not a supported scheduling contract.

### D8. `stream_members()` is a separate one-pass ownership model

Unchanged: `stream_members()` owns the reader's forward/data pass for its lifetime in
either access mode; it cannot overlap another pass, random `open()` work,
materialization, or reader close. The previous yielded stream is closed/invalidated
before advancing (iterator-owned lifecycle, unlike random `open()`). `extract_all()`
may drive it as a child pass. Exhaustion, exception, explicit generator close, or
abandonment/finalization releases pass ownership exactly once. Yielded streams follow
the same `SEEKABLE` declaration where the backend can serve it, but their validity
still ends at iterator advance. A caller needing simultaneous streams materializes and
uses random `open()` under `CONCURRENT`.

### D9. Reader close uses stream leases

Unchanged from the prior draft (lifecycle states `OPEN`, `READER_CLOSED`,
`TEARDOWN_RUNNING`, `TEARDOWN_COMPLETE`; guarded lease count; one-shot teardown claim;
escaped streams keep backend resources alive until the final lease closes; lazy-open
failure semantics; `ExceptionGroup` on dual close failure; finalizer etiquette;
caller-owned sources never closed by archivey). Note leases are **not** conditional on
`CONCURRENT`: even the default single stream may escape its reader's `with` block, so
lease-deferred teardown applies to every reader. Post-close reader operations raise
`ArchiveyUsageError` (D4); repeated `close()`/`__exit__` stay idempotent no-ops.

### D10. Password resolution is concurrency-safe and callback-safe

Unchanged from the prior draft: immutable ordered static candidates; synchronized
known-good snapshot/promotion; per-unit tried/attempt state; expensive key
derivation/decrypt outside all reader-state locks (a required backend/source lock only
around atomic decode/handle work); one provider-driven resolution turn per reader via a
claim/call/validate/publish condition protocol with the provider invoked under no
Archivey lock; waiters re-snapshot known-good before claiming the next turn; same-reader
provider reentry into a password-requiring operation raises a usage error rather than
deadlocking. Only load-bearing under `CONCURRENT`; the default path runs the same code
single-owner.

### D11. Lock scope, callback rule, and ordering

Unchanged: nested reader-state acquisition follows lifecycle/operation-state →
materialization → password; backend/source locks are leaves; `ArchiveStream` lazy
open/close is refactored to claim/call/publish (`UNOPENED`, `OPENING`, `OPEN`, `FAILED`,
`CLOSED`) so stream state is never held while acquiring backend/source or lifecycle
locks. Password providers, progress callbacks, selectors/filters, logging handlers,
diagnostic formatting/stamping, `sys.unraisablehook`, and user-visible close/finalizer
hooks execute with no Archivey lock held.

### D12. Backend compliance paths

Unchanged in substance; all rows apply to declared-`CONCURRENT` readers (the default
path takes none of these locks):

- **Directory:** independent file descriptor per member (still gated by D1).
- **ZIP:** stdlib `_SharedFile` has independent positions and a `ZipFile` lock; verify
  member open/close and archive close obey D9.
- **Archivey-owned byte ranges (single-file, native 7z/RAR):** `SharedSource` views with
  per-view positions; per-open decoders or synchronized bounded shared decode; no
  promise about redundant decompression.
- **TAR-RA and ISO:** the comprehensive one-lock-per-reader mechanism in
  `tar-concurrent-open`, instantiated only for `CONCURRENT` readers.

The `_open_member` rule stays "no unsynchronized open-critical mutation", not
mathematical purity.

### D13. TAR/ISO lock correctness is intentionally serialized and measured

Unchanged from the prior draft: one per-reader lock covers every operation on the shared
library handle (initialization/failure cleanup, `getmembers()` scan I/O, strict-EOF
reads, member open/context entry, read/readinto, supported seek/tell, member close,
archive close); pinned pycdlib `walk()`/`get_record()` remain audited in-memory paths
and version-regression items; proportionate serialization baselines are recorded without
becoming a correctness merge threshold.

### D14. Free-threaded Python is a correctness target for `CONCURRENT`

Unchanged: for every claimed backend/runtime combination the D5 seam must behave
identically on regular and free-threaded CPython, with real synchronization rather than
incidental GIL serialization; a required Linux `free-threaded-concurrency` CI job runs
`3.13t` against `concurrent_reader`-marked core-backend tests; backends not exercised
there are not claimed covered. No speedup is promised.

## Risks / Trade-offs

- **Friction on safe patterns:** diffing two ZIP members now requires declaring
  `CONCURRENT`; seeking a member requires `SEEKABLE`. Accepted deliberately: one
  informed line per integration is the mechanism, not a defect. Expect default
  non-seekability to be the most commonly hit gate (many parsers call `seek(0)`
  reflexively); the error is the standard `io.UnsupportedOperation` those parsers
  already probe for via `seekable()`.
- **Wrapper erosion:** libraries built on archivey may declare capabilities themselves
  and pass permissiveness through. The gate still forces *that library's author* to
  read the caveat once, which is the goal.
- **Contract flip for seekable streams:** `seekable-decompressor-streams` moves from
  "seekable by default, slow rewinds are loud" to "seekable on demand"; its accelerator
  AUTO resolution changes meaning. This ripples into that spec, `ArchiveStream`'s
  seekable hint, and tests that seek member streams.
- **Resource retention:** escaped streams defer backend teardown (unchanged).
- **Operation-state complexity:** lifecycle, materialization, and operation-owner state
  remain separate machines; the default path exercises them trivially, which keeps the
  complexity mostly behind the `CONCURRENT` declaration.
- **Two-step landing:** the API shape lands whole, but `SEEKABLE`'s machinery flip
  lands after the `CONCURRENT` gate; until then declared-`SEEKABLE` simply preserves
  today's behavior.

## Migration Plan

Not a user compatibility migration (pre-1.0). During implementation:

1. Add `MemberStreams`, the `open_archive` parameter, the gate + breadcrumb, and the
   `ArchiveyUsageError` hierarchy; route detected-misuse errors to it.
2. Add operation/materialization publication and lifecycle leases in the base reader;
   activate shared-handle machinery only under `CONCURRENT`.
3. Synchronize password resolution and member late-bound updates.
4. Audit/adjust each backend; land `tar-concurrent-open` for TAR/ISO under the flag.
5. Flip member-stream/`open_stream` seekability to declared-demand; key accelerator
   AUTO resolution on it.
6. Update authoritative specs and prose docs listed in the tasks.
7. Add behavior/stress tests, the `3.13t` CI job, and proportionate TAR/ISO
   serialization measurements.

## Open Questions

_(none remaining — wrong-reader identity and related misuse migrate to
`ArchiveyUsageError`; `MemberStreams` spelling is `CONCURRENT` / `SEEKABLE`; full
open-site stack is retained unconditionally.)_
