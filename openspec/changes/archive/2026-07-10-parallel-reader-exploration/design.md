# Design — Parallel-safe reader exploration

> **Historical record — partly superseded (2026-07-10).**
> The active `concurrent-member-streams` proposal now commits the post-materialization
> worker seam and cache/lifecycle/password synchronization; `tar-concurrent-open` removes
> the TAR exemption and ISO leave-alone disposition. Statements below describing one reader
> per thread, deferred cache locking, or no further ABC/runtime work are preserved as the
> exploration's original conclusion, not current guidance.

This change has **one committed outcome** (the scoped `_open_member` reentrancy invariant + an
ABC docstring) and an **analysis** that seeds a future `parallel-extraction` change. The
durable analysis lives in `docs/parallel-reader.md` (audit table + §§2–7 below summarized).

## Committed now

- **Invariant (spec delta):** for a random-access backend advertising independent member open,
  `_open_member` is a function of `(member, shared source)` — no shared-reader-state mutation,
  no per-open scratch on `self`; byte access via a shared-source view. Scoped **out**:
  streaming passes and single-shared-decoder backends (TAR-RA = random-access TAR /
  `tarfile.TarFile`).
- **Materialize-before-fan-out precondition:** documented on the ABC now (task 1.4). A future
  concurrent consumer must complete the random-access member pass before opening members
  concurrently; the one-time cache build is not itself concurrency-safe.
- **No fixes here that another change owns:** `single_file._first_stream` is fixed by
  `shared-source-streams` (landed). **ISO** is not a gap under this invariant: like ZIP
  path-source, member addressing is owned by an external library (`pycdlib`);
  `shared-source-streams` leaves a design note and does not retrofit it. This change only
  records that disposition.

## Audit summary (task 1.2)

| Backend | Verdict |
|---|---|
| directory | Compliant (independent path opens) |
| ZIP | Compliant (stdlib `_SharedFile`; no archivey scratch) |
| single-file | Compliant after `shared-source-streams` (`_first_stream` gone) |
| TAR-RA | Exempt (single shared `tarfile`) |
| ISO | Compliant on archivey state; pycdlib-owned addressing (not non-compliant) |

Full table: `docs/parallel-reader.md` §1.

## Analysis conclusions (tasks 2.1–2.6)

1. **Member-cache safety** — recommend the materialize-before-fan-out precondition for v1;
   optional init-under-lock only if a parallel feature wants defense-in-depth.
2. **Benchmark design** — workloads/metrics/runtimes sketched in `docs/parallel-reader.md` §3;
   home `benchmarks/`; decide the feature with numbers per `VISION.md`.
3. **Free-threading (C4 draft)** — keep "one reader per thread" public; parallel work is
   across independent units; accelerator close-before-finalize still applies. Promote into
   `threat-model.md` when parallel-extraction is proposed.
4. **Work partitioning** — ZIP/directory per-member; 7z per folder; RAR per solid block;
   TAR-RA none; ISO library-owned.
5. **N readers vs SharedSource** — prefer one reader + SharedSource (materialized list) for
   a library-primary parallel API; N readers as an isolation escape hatch.
6. **ABC recommendation** — **no further ABC change before Phase 6.** The invariant +
   SharedSource + documented precondition are enough; defer the feature itself.

## Explicitly out of scope

The parallel-extraction *feature* (a consumer that fans work across streams/readers) — deferred
per `openspec/project.md` (v1 out-of-scope), gated on the benchmark and free-threading stance
above.
