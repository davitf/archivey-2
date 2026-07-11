# Declared member-stream capabilities (concurrent + seekable)

## Why

`shared-source-streams` established independent byte-range views, and
`tar-concurrent-open` supplies the equivalent coordination for TAR and ISO. The remaining
question is the public contract. Two drafts preceded this one:

- an **opt-in flag** (`allow_multiple_open_streams=False`) gating concurrent streams; and
- an **unconditional guarantee** (no flag; simultaneous streams always legal, cost
  informational).

This revision replaces both with a **declared-capabilities** contract, decided with the
maintainer. The default member-stream contract is the lowest common denominator every
format serves efficiently — **forward-only, one live stream at a time** — and the two
capabilities beyond it are declared at `open_archive()`:

```python
open_archive(src)                                               # forward-only, single stream
open_archive(src, member_streams=MemberStreams.SEEKABLE)        # e.g. parquet-in-zip
open_archive(src, member_streams=MemberStreams.CONCURRENT)      # worker fan-out
open_archive(src, member_streams=MemberStreams.CONCURRENT | MemberStreams.SEEKABLE)
```

The reasons, in order of weight:

1. **Fail fast in development, uniformly.** The expensive patterns are format-dependent
   (decoder thrash on interleaved compressed-TAR streams; O(n)-per-rewind seeks), so a
   developer who tests only with ZIP ships the trap. Gating **every** format — the
   directory reader included — surfaces the constraint on the first format the developer
   tries, in development. Cost receipts and diagnostics are passive; per `VISION.md`, a
   warning most applications never see is a surprise deferred, not avoided.
2. **Pre-1.0 reversibility.** Relaxing a strict default later breaks nobody; adding a
   gate after shipping permissive behavior is a breaking change.
3. **The default path gets faster.** With no capability declared, the reader takes no
   shared-handle locks, keeps no lease accounting beyond the single stream, and never
   builds seek-point tables or instantiates seek accelerators. Declared capability is
   what pays for its own machinery.
4. **Honest scope.** The gate governs what the *streams* can do. What a caller's member
   *open order* costs on a solid archive is a property of their algorithm, governed as
   before by `AccessCost`/`solid_block_count` and `stream_members()` — stated explicitly
   so the flag is not mistaken for total O(n²) protection.

The guarantee behind `CONCURRENT` stays narrow. It is not "the reader is thread-safe":
after one completed member materialization, concurrent `open()` calls and operations on
the independent streams they return are supported. Iteration, materialization, extraction
coordination, reader `close()`, and streaming-mode forward passes remain single-owner
operations.

## What Changes

- Add `member_streams: MemberStreams` to `open_archive()` (a flags enum;
  default: no capability). `MemberStreams.CONCURRENT` permits any number of member
  streams to be open simultaneously (interleaved in one thread, or across threads after
  member materialization). `MemberStreams.SEEKABLE` makes member streams seekable where
  the backend can provide it. Capabilities are per-archive intent, not ambient policy: no
  `ArchiveyConfig` field, and no per-`open()` argument (overlap is a property of a *pair*
  of streams, so per-open acknowledgment has no coherent owner).
- **Default contract:** member streams report `seekable() is False` and any `seek()`
  raises `io.UnsupportedOperation` (`tell()` works; forward skip = read-and-discard).
  Opening a second member stream while one is still open raises `ConcurrentAccessError`
  — uniformly on every format, directory included. `open_archive()` records its caller's
  `file:line` cheaply and the error message includes it ("this archive was opened without
  MemberStreams.CONCURRENT at app.py:42"); the full open-site stack is retained on the
  reader unconditionally (no config knob).
- **Uniform strictness is documented as a principle:** the directory reader is never more
  lenient than archive readers — it exists to make archive-vs-directory code uniform, to
  exercise the API, and to serve future dir↔archive piping (`format-directory` delta).
- **A new usage-error hierarchy**, separate from `ArchiveyError`: `ArchiveyUsageError`
  (root, not an `ArchiveyError`) with `ConcurrentAccessError` as its first subclass.
  `except ArchiveyError` means "the archive or environment did something"; caller misuse
  — undeclared capability, operations on a closed reader, detected single-owner overlap,
  provider reentry, wrong-reader member identity, early-closed caller source — indicates a
  bug in the calling code and must not be swallowed by blanket archive-error handlers.
  `UnsupportedOperationError` remains an `ArchiveyError` for archive/mode/feature
  limitations.
- **`open_stream()` (compressed-streams) follows the same rule:** non-seekable by
  default, with a `seekable: bool = False` parameter. One story everywhere: no archivey
  stream is seekable unless asked.
- **Seek machinery becomes demand-driven** (`seekable-decompressor-streams` delta): the
  `use_rapidgzip`/`use_indexed_bzip2` `AUTO` resolution and native XZ/lzip index parsing
  activate only for declared-seekable streams; undeclared streams skip index construction
  entirely. The "slow seek beats failing, but loudly" rule continues to apply to
  *declared-seekable* streams on the non-accelerated path.
- `MemberStreams.CONCURRENT` unlocks both interleaved single-thread use and the
  post-materialization multi-thread worker seam — one bit, one capability. All the
  machinery this change specifies (operation ownership, child scopes, stream leases,
  atomic member-cache publication, password/provider synchronization, callback lock
  rules, free-threaded coverage) is the *implementation* of that declared capability;
  the undeclared default path takes none of those locks.
- **Internal library operations never require caller flags**: `extract_all()` (including
  hardlink recovery), symlink-target reads, and password candidate confirmation open
  members under library-internal scopes exempt from the gate.
- Keep `AccessCost` / `solid_block_count` informational. Declared concurrency on a solid
  archive remains correct but may repeat decode work; `stream_members()` remains the
  efficient one-pass API. Random open-order cost on solid archives is explicitly out of
  the gate's scope.
- Make member materialization an explicit phase boundary (unchanged from the prior
  draft): the completed member list and name index are published once as an immutable
  snapshot; materialization, iteration, `scan_members()`, `stream_members()`,
  `extract_all()`, and reader `close()` may not overlap actively executing worker calls.
- Define lifecycle leases (unchanged): `reader.close()` stops new reader operations while
  already-open member streams remain usable until closed; teardown runs exactly once.
  Leases apply to the default single stream as well — one escaped stream can outlive its
  reader regardless of declared capabilities.
- Synchronize password state and require providers/callbacks/diagnostics to run outside
  all Archivey locks (active under `CONCURRENT`). Provider calls are serialized by a
  simple lock released around the callback, with same-reader provider reentry rejected;
  the resolution-turn condition protocol is dropped as unnecessary (design D10).
- **`CONCURRENT` ships provisional in v1 (design D15):** the correctness machinery and the
  cooperative-use guarantee land now; the free-threaded/adversarial hardening — the
  data-race-free free-threaded seam and its required CPython `3.13t` core-backend CI job —
  is a documented post-v1 promotion, not a v1 merge gate. When it lands it is a correctness
  promise, not a parallel-speed promise.
- Fold TAR/ISO into the declared capability through `tar-concurrent-open`: its
  one-lock-per-reader mechanism is instantiated only for `CONCURRENT` readers.
- Replace the blanket prose declarations ("readers are not thread-safe; one per thread")
  in `packaging-and-extras`, `openspec/project.md`, `SPEC.md`, and `ARCHITECTURE.md` with
  the declared-capabilities matrix.

## Capabilities

### New Capabilities

_(none — the gate and hierarchy live in existing capabilities)_

### Modified Capabilities

- `archive-reading`: declared member-stream capabilities; default forward-only
  single-stream contract; the gated concurrent seam; materialize-before-fan-out;
  lifecycle leases; `stream_members()` cross-API behavior; password synchronization;
  callback lock rules; internal-scope exemption.
- `access-mode-and-cost`: capabilities compose with the two access modes; cost stays
  informational and never gates legality; open-order cost explicitly out of gate scope.
- `error-handling`: new `ArchiveyUsageError` hierarchy (outside `ArchiveyError`) with
  `ConcurrentAccessError`; detected misuse moves there; `UnsupportedOperationError`
  remains for archive/mode/feature limitations.
- `compressed-streams`: `open_stream()` is non-seekable by default with a `seekable`
  parameter.
- `seekable-decompressor-streams`: index/accelerator work is demand-driven by declared
  seekability.
- `format-directory`: uniform-strictness principle documented.
- `packaging-and-extras`: thread-safety statement replaced by the declared-capabilities
  matrix, including free-threaded expectations for `CONCURRENT`.
- `testing-contract`: gate behavior across every format (directory included), breadcrumb
  surfacing, default non-seekability, demand-driven accelerator activation, concurrency
  stress on regular and free-threaded CPython, proportionate measurements.

## Impact

- Public API: `MemberStreams` flags enum, `member_streams=` on `open_archive()`,
  `seekable=` on `open_stream()`, `ArchiveyUsageError`, `ConcurrentAccessError`.
- Code to be implemented later: gate + breadcrumb in `open_archive`/`BaseArchiveReader`,
  `MemberStreams` plumbing, the conditional machinery (`BaseArchiveReader` publication/
  lifecycle state, `_PasswordCandidates`, `ArchiveStream` leases, backend `_open_member`
  implementations, `SharedSource`, the TAR/ISO mechanism owned by `tar-concurrent-open`),
  demand-driven accelerator resolution.
- Implementation sequencing: the `CONCURRENT` gate + machinery lands first; the
  `SEEKABLE` flip through `seekable-decompressor-streams`/`open_stream` lands second.
  The API shape (the flags parameter) lands whole in the first step.
- Relationship to `tar-concurrent-open`: this change owns the cross-format public
  contract; that change owns the TAR/ISO shared-handle mechanism, active under
  `CONCURRENT`.
- This replaces both earlier drafts. Neither `allow_multiple_open_streams` nor the
  unconditional no-flag contract ships; pre-1.0, no deprecation path is added.
- Out of scope: parallel extraction scheduling, async APIs, speedup promises, gating of
  member *open order* (governed by `AccessCost`/`stream_members()`), and implementing the
  task list in this proposal-only change.
