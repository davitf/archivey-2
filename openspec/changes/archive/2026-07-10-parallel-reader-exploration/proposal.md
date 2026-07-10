# Exploration: parallel-safe reader — lock the `_open_member` interface now, defer the feature

> **Historical record — partly superseded (2026-07-10).**
> `concurrent-member-streams` replaces this exploration's "one reader per thread,"
> unsynchronized-cache, TAR carve-out, and ISO leave-alone conclusions with a narrow
> supported contract: after materialization, concurrent `open()` and independent member
> stream operations are safe by construction. `tar-concurrent-open` supplies comprehensive
> TAR/ISO shared-handle locking. The audit/reentrancy/benchmark analysis below records what
> was concluded at archive time; it is not the current target contract.

## Why

The `shared-source-streams` change makes the *underlying source* safe for multiple open
member streams. It deliberately does **not** make the `ArchiveReader` object parallel-safe —
and that is the harder, higher-unknown question this exploration owns. The maintainer's
concern is the right one: **if the ABC-to-backend interface needs to change to enable
parallelism, change it now — before Phase 6 writes three new backends (7z, RAR, and later a
native ZIP) against it — so we never pay a retrofit.**

### How hard is "parallel-safe base reader", concretely

`BaseArchiveReader` carries mutable state that is **not** concurrency-safe today
(`src/archivey/internal/base_reader.py`):

- **Forward-pass / streaming state** — `_forward_pass_started`, `_progressive_gen`,
  `_pass_scanned`, `_pass_by_name_lists`. This regime is *inherently sequential* (one forward
  pass, one consumer); parallelism here is meaningless. **Not a target.**
- **Random-access caches** — `_members_cache`, `_members_by_name_lists`, populated once,
  lazily, by a read-modify-write that is unguarded. After population they are read-mostly.
- **Lifecycle** — `_closed`, `_source`, `_compressed_input_counter`.

So "parallel-safe reader" reduces to a **narrow regime**: a random-access reader whose member
list is already materialized, serving independent `open()`/`read()` calls concurrently. Two
things must hold, and only one is cheap:

1. **The member cache is built exactly once, safely** — a one-time init (lock or
   "materialize-before-fan-out" precondition). *Low unknown; small.*
2. **`_open_member` is a pure function of `(member, shared source)` — it mutates no shared
   reader state.** This is the **interface invariant** worth locking now: every backend's
   `_open_member` must derive its stream from the member plus a `SharedSource` view, holding
   no per-open scratch on `self`. If that is a documented ABC contract before Phase 6, the new
   backends are born parallel-ready and there is **no retrofit**. *This is the "change the
   interface now" answer.*

Everything **beyond** that carries real unknowns and is why this is an exploration, not a
finished spec:

- **Does parallelism even pay?** Under the GIL, parallel *pure-Python* decode barely helps;
  the win is C-codec decode (`lzma`/`bz2`/`zlib` release the GIL) and I/O overlap.
  `VISION.md` requires a benchmark before any perf claim — decide with numbers, per the
  cross-cutting benchmark gate.
- **Free-threaded Python (3.13t)** — `docs/threat-model.md` C4 flags this as needing a
  position statement; it changes the payoff math and the accelerator-thread story.
- **Solid formats partition work unevenly** — 7z members *within one folder* share
  decompressor state (sequential); only *separate folders* are independent. RAR solid blocks
  likewise. The parallelizable unit is format-specific.
- **The consumer** — real parallel *extraction* needs the `ExtractionCoordinator` to fan out
  across independent streams/readers (interacts with `safe-extraction`), and a decision
  between "N readers over one path" vs. "one reader + shared source". This is the deferred
  v1 feature (`openspec/project.md` — parallel extraction is out of scope for v1).

## What Changes

**Committed runtime deliverable = the spec delta + an ABC docstring + an audit.** No runtime
code change is required for correctness today; the only code that lands is any *cheap* fix the
audit surfaces (and the ABC docstring documenting the invariant + the materialize-before-fan-out
precondition). Anything larger is recorded, not built.

- **Committed now (small spec delta):** the **`_open_member` reentrancy invariant**, scoped to
  random-access backends that advertise independent member open (not streaming, not
  single-decoder TAR), as an `archive-reading` backend contract, so Phase 6 backends honor it.
  Audit directory / ZIP / TAR-RA / ISO / single-file against it and record gaps. Known today:
  directory and ZIP largely comply; **single-file's `_first_stream` scratch** is the real
  archivey-owned gap; **TAR-RA** is the hard/exempt case (single shared decoder). **ISO** is
  not treated as non-compliant under this invariant — `pycdlib` owns member addressing (same
  shape as ZIP path-source / stdlib). The single-file fix is **owned by
  `shared-source-streams`**; ISO's "leave alone + design note" disposition lives there too —
  this change only records that so the two don't fight.
- **Explored, not committed (written analysis, lives in this change's `design.md` and a seed
  `docs/parallel-reader.md`):** the benchmark design (wall time, bytes-decompressed, seek
  counts, across GIL and 3.13t), the free-threading position (`docs/threat-model.md` C4), the
  solid-format work partitioning, the member-cache one-time-build safety, and the "N readers vs.
  shared source" decision — feeding a **future** parallel-extraction change. If the analysis
  finds the ABC needs *more* than this invariant to avoid a retrofit, that becomes an explicit
  recommendation before Phase 6 locks the ABC.

## Sequencing / dependency

The invariant's delta references the *Multiple concurrently-open member streams* requirement
introduced by **`shared-source-streams`**; land/sync that change **first** (or in the same
batch), then this one. The parallel-extraction *feature* is a separate future change, gated on
the benchmark outcome and a free-threading position.

## Impact

- Affected specs: `archive-reading` (ADDED — the `_open_member` reentrancy invariant).
- Affected code: none required for correctness now; an audit of the five existing backends'
  `_open_member` against the invariant, with any tightening tracked as tasks.
- Risk: low for the committed part (a contract the backends already largely satisfy); the
  feature itself stays deferred.
- Sequencing: land the invariant **before** Phase 6 (alongside `shared-source-streams`); the
  parallel-extraction feature is a separate future change gated on the benchmark outcome and a
  free-threading position.
