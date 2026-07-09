# Design — Parallel-safe reader exploration

This change has **one committed outcome** (the scoped `_open_member` reentrancy invariant + an
ABC docstring) and an **analysis** that seeds a future `parallel-extraction` change. This file
is the analysis home; the durable version lands as `docs/parallel-reader.md`.

## Committed now

- **Invariant (spec delta):** for a random-access backend advertising independent member open,
  `_open_member` is a function of `(member, shared source)` — no shared-reader-state mutation,
  no per-open scratch on `self`; byte access via a shared-source view. Scoped **out**:
  streaming passes and single-shared-decoder backends (TAR-RA).
- **Materialize-before-fan-out precondition:** documented on the ABC now (task 1.4). A future
  concurrent consumer must complete the random-access member pass before opening members
  concurrently; the one-time cache build is not itself concurrency-safe.
- **No fixes here that another change owns:** `single_file._first_stream` is fixed by
  `shared-source-streams`. **ISO** is not a gap under this invariant: like ZIP path-source,
  member addressing is owned by an external library (`pycdlib`); `shared-source-streams`
  leaves a design note and does not retrofit it. This change records that disposition and
  does not re-fix.

## To analyse (no code; feeds the future feature)

1. **Does parallelism pay?** Benchmark design — workloads (DIRECT non-solid vs. multi-folder
   solid 7z; C-codec vs. pure-Python decode) × metrics (wall time, bytes-decompressed, seek
   counts) × runtime (GIL build vs. 3.13t free-threaded). Home: `benchmarks/`. Decide with
   numbers per `VISION.md`.
2. **Free-threading position** (`docs/threat-model.md` C4) — what parallel decode/extract looks
   like under 3.13t; the C++-accelerator-thread caveat.
3. **Work partitioning per format** — parallelizable unit: 7z folder / RAR solid block /
   ZIP-DIRECT per-member. A future coordinator needs the granularity.
4. **"N readers over one path" vs. "one reader + shared source"** — independent handles +
   independent reader state, vs. shared source + lock; how each interacts with
   `ExtractionCoordinator` and `safe-extraction`.
5. **Recommendation** — if the ABC needs more than this invariant to avoid a retrofit, state
   exactly what, before Phase 6 locks the ABC.

## Explicitly out of scope

The parallel-extraction *feature* (a consumer that fans work across streams/readers) — deferred
per `openspec/project.md` (v1 out-of-scope), gated on items 1–2 above.
