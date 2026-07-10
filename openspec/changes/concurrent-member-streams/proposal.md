## Why

`shared-source-streams` established independent byte-range views, and
`tar-concurrent-open` supplies the equivalent coordination for TAR and ISO. The remaining
question is the public contract. The earlier opt-in draft proposed
`allow_multiple_open_streams=False`, but that makes a correctness property conditional,
adds permanent API state to avoid a pre-release behavior change, and prevents the natural
"materialize once, then hand members to workers" use case unless every caller knows a
format-independent escape hatch.

Archivey is pre-1.0 and has no compatibility obligation to that draft. The better long-term
contract is: **random-access readers make independent member streams safe by construction**.
The cost receipt still tells callers when doing so is expensive (`AccessCost.SOLID`), but
cost does not weaken correctness or add a gate. This preserves the vision's no-silent-O(n²)
rule through queryable cost data and documentation rather than making safe stream ownership
an opt-in.

The guarantee must stay narrow. It is not "the reader is thread-safe": after one completed
member materialization, concurrent `open()` calls and operations on the independent streams
they return are supported. Iteration, materialization, extraction coordination, reader
`close()`, and streaming-mode forward passes remain single-owner operations.

## What Changes

- Replace the abandoned `allow_multiple_open_streams` design with an unconditional
  random-access guarantee: any number of member streams may coexist, and after member
  materialization workers may call `open()` concurrently and independently
  `read`/`readinto`/`close` their streams, plus `seek`/`tell` when supported.
- Keep `AccessCost` / `solid_block_count` informational. Concurrent opens on a solid archive
  remain correct but may create independent re-decode work; `stream_members()` remains the
  efficient one-pass API.
- Make member materialization an explicit phase boundary. The completed member list and name
  index are published as one immutable snapshot under a cache state that is separate from
  lifecycle; materialization itself, iteration,
  `scan_members()`, `stream_members()`, `extract_all()`, and reader `close()` may not overlap
  with actively executing worker calls (an idle stream lease is not active overlap).
- Represent single-owner work with explicit operation tokens and private child scopes:
  materialization may read link data, `extract_all()` may drive `stream_members()`, and a pass
  may use its yielded stream without being rejected as unrelated reentry. Generator close,
  exhaustion, error, or abandonment releases pass ownership exactly once.
- Preserve `stream_members()` as a distinct one-pass API: advancing invalidates/closes the
  previous yielded stream. A `stream_members()` pass cannot overlap another forward pass,
  random `open()` work, materialization, or reader close.
- Use `UnsupportedOperationError` for library-detected unsupported overlap and closed-reader
  operations. Do not add `ConcurrentAccessError`: simultaneous random-access member streams
  are valid, not exceptional.
- Define lifecycle leases: `reader.close()` stops new reader operations, while already-open
  member streams remain usable and keep backend resources alive until the final stream
  closes. Reader close is idempotent; backend teardown occurs exactly once. Concurrent
  `reader.close()` with `open()` or stream I/O is outside the supported contract.
- Define lazy-open failure, one-shot teardown failure/finalizer behavior, caller-owned source
  non-ownership, and the exact post-close method/property matrix.
- Synchronize password state. Known-good promotion and per-unit attempt state are protected;
  provider calls are serialized, but provider invocation executes with no Archivey lock held.
  Static candidates remain immutable and ordered.
- Require providers/callbacks/diagnostics to run outside all Archivey locks. Decode and
  candidate validation run without lifecycle/cache/password locks but may use narrowly
  required backend/source locking. Refactor `ArchiveStream` lazy-open/close into
  claim/call/publish phases so stream state is never held while acquiring backend/source or
  lifecycle locks.
- Require the supported seam to remain data-race-free on claimed free-threaded
  backend/runtime combinations; the implementation MUST NOT rely on the GIL for publication,
  counters, or cache/password mutation. Add a concrete required CPython `3.13t` core-backend
  CI job; formats not exercised there are not claimed covered merely because their
  ordinary-build tests pass. This is a correctness promise, not a parallel-speed promise.
- Fold TAR/ISO into the guarantee through `tar-concurrent-open`. One lock per reader covers
  **every operation on the shared library handle**, including archive initialization/failure
  cleanup, TAR `getmembers()`/EOF reads, TAR/ISO member-open initialization, ISO context entry,
  read/readinto, supported seek/tell, member close, and archive close. The pinned pycdlib
  `walk()`/`get_record()` paths are documented as in-memory catalog operations and remain
  version-audit items. This favors correctness over parallelism; proportionate measurements
  describe serialization cost without becoming a correctness merge threshold.
- Replace the old blanket prose declarations ("readers are not thread-safe; one per thread")
  in `packaging-and-extras`, `openspec/project.md`, `SPEC.md`, and `ARCHITECTURE.md` with the
  narrow supported/unsupported matrix. These are explicit implementation tasks, not a silent
  contradiction.
- Mark the archived `parallel-reader-exploration` conclusions as historical and superseded
  where they said one reader per thread or deferred cache synchronization. Keep its original
  analysis intact otherwise.

## Capabilities

### New Capabilities

_(none)_

### Modified Capabilities

- `archive-reading`: unconditional concurrent member-stream correctness; the
  materialize-before-fan-out boundary; supported worker operations; unsupported reader-wide
  overlap; lifecycle leases; `stream_members()` cross-API behavior; password synchronization;
  callback/lock rules.
- `access-mode-and-cost`: random-access concurrency is part of the mode contract, streaming
  remains one pass, and cost is informational rather than a legality gate.
- `error-handling`: `UnsupportedOperationError` covers detected overlap/closed-reader misuse;
  no `ConcurrentAccessError`.
- `packaging-and-extras`: replace the blanket thread-safety declaration with the narrow
  random-access member-stream guarantee, including free-threaded Python expectations.
- `testing-contract`: require concurrency stress coverage on regular and free-threaded
  CPython through a concrete `3.13t` core job, plus proportionate measurements for changed
  performance mechanisms rather than a blanket merge gate.

## Impact

- Code to be implemented later: `BaseArchiveReader` publication/lifecycle state,
  `_PasswordCandidates`, `ArchiveStream` leases, backend `_open_member` implementations,
  `SharedSource`, and the TAR/ISO mechanism owned by `tar-concurrent-open`.
- Specs/docs to update during implementation: `archive-reading`, `access-mode-and-cost`,
  `error-handling`, `packaging-and-extras`, `testing-contract`, `openspec/project.md`,
  `SPEC.md`, `ARCHITECTURE.md`, `PLAN.md`, `IDEAS.md`, `docs/parallel-reader.md`,
  `docs/threat-model.md`, API/ABC docstrings.
- Tests to add later: interleaved and multi-thread open/read/close plus supported positioning
  across representative backends; materialization publication; owner-child scopes; overlap
  rejection; lifecycle leases/failures; password provider serialization/reentrancy; callback
  lock safety; required free-threaded stress.
- Relationship to `tar-concurrent-open`: this change owns the cross-format public contract;
  that change owns the TAR/ISO shared-handle mechanism.
- This is an **API replacement**, not a compatibility migration. The unpublished
  `allow_multiple_open_streams` keyword and `ConcurrentAccessError` are removed from the
  proposal rather than deprecated.
- Out of scope: parallel extraction scheduling, async APIs, promises of speedup, concurrent
  iteration/materialization/extraction/reader-close, and implementing the task list in this
  proposal-only change.
