> **Grab-bag / exploration.** Much of this was superseded by `MemberStreams` / `reader-concurrency`. Index: [grab-bag/index.md](index.md).

# Parallel-safe reader — exploration notes

> **Current status (2026-07-11):** `concurrent-member-streams` supersedes
> the archived exploration's one-reader-per-thread/deferred-cache conclusion — and, after
> maintainer review, both earlier public-contract drafts. Member-stream capabilities are
> **declared** at `open_archive(member_streams=MemberStreams.CONCURRENT | SEEKABLE)`; the
> uniform default on every format (directory included) is forward-only, one live stream,
> no locks, no seek machinery. Once `CONCURRENT` is declared: concurrent first-touch
> materialization is coordinated (one build; waiters share the published snapshot), then
> concurrent `open()` and independent member-stream read/readinto/close (plus positioning
> under `SEEKABLE`) are safe by construction. `close()` drains in-flight worker calls;
> escaped streams keep the lifecycle-lease contract. Free-threaded correctness for core
> backends is exercised by the Linux CPython `3.13t` `free-threaded-concurrency` CI job
> (`pytest -m concurrent_reader`). An undeclared second overlapping open raises
> `ConcurrentAccessError` (an `ArchiveyUsageError`, outside the `ArchiveyError` hierarchy).
> Distinct reader-wide passes (`__iter__` / `stream_members` / `extract_all`) and
> same-stream access remain single-owner / caller-synchronized.
> `tar-concurrent-open` supplies comprehensive TAR/ISO shared-handle locking for declared
> readers. The gate covers stream capabilities only — solid open-*order* cost stays with
> `AccessCost`/`stream_members()`. Parallel extraction scheduling remains future; speed
> claims require proportionate measurements. See `benchmarks/tar_iso_lock_baseline.py` for
> a non-gating TAR/ISO lock timing recipe.

## Glossary

- **TAR-RA** — the **random-access TAR** reader (`TarReader` with `streaming=False`),
  backed by one shared `tarfile.TarFile` (and, for compressed tar, one decompressor).
  Distinct from streaming TAR (`streaming=True` / `r|` mode). Same seek-before-read
  shape as ISO/`pycdlib` (no library lock); concurrent-open via a locked member-stream
  wrapper is proposed in `tar-concurrent-open` (not SharedSource-at-`offset_data`, which
  would reimplement sparse).
- **SharedSource** — `streamtools` primitive: locked, per-view-position byte-range
  views over one seekable source (`shared-source-streams`).

---

## 1. Backend audit (`_open_member` vs. the reentrancy invariant)

The archived exploration stated the invariant as "`_open_member` is a function of
`(member, shared source)`." The active proposal refines that wording: `_open_member` must
perform no **unsynchronized** open-critical mutation and keep no per-open scratch on `self`
that another call can overwrite. Coordinated lifecycle/password/cache bookkeeping is allowed;
archivey-owned byte ranges still go through `SharedSource.view`.

| Backend | Regime | Compliant? | Notes |
|---|---|---|---|
| **directory** | RA, independent | **Yes** | Opens `self._root / member.name` — independent FD per open; no reader scratch. Outside SharedSource (filesystem paths, not a shared byte source). |
| **ZIP** | RA, independent | **Yes** | `_open_zip_entry` → `ZipFile.open(info)`; stdlib `_SharedFile` coordinates seek/read. Under `CONCURRENT`, archivey also serializes `open`/`close`/`ZipFile.close` so free-threaded `_fileRefCnt` updates cannot race. No per-open scratch on `ZipReader`. |
| **single-file** | RA when seekable | **Yes** (post-`shared-source-streams`) | Seekable stream: `SharedSource.view(0)` + fresh codec per open. Path: independent codec FD per open. `_first_stream` scratch **removed**. Non-seekable: one-shot `_pending_stream` (streaming / single-pass — out of invariant scope). |
| **TAR-RA** | RA, library seek-before-read | **Gap → `tar-concurrent-open`** | `tarfile._FileInFile` re-seeks on each `read()`, **no lock** (same shape as pycdlib). Keep `extractfile` (sparse); one per-reader lock covers `tarfile.open`, `getmembers()` scan I/O, strict EOF reads, member creation, read/readinto/supported positioning/close, and archive close. |
| **ISO** | RA, library seek-before-read | **Gap → `tar-concurrent-open`** | `pycdlib.PyCdlibIO` re-seeks on each `read()`, **no lock**. One per-reader lock covers `PyCdlib.open` / `open_fp`, member creation/context entry, read/readinto/supported positioning/close, and archive close. Pinned `walk()` / `get_record()` are verified in-memory catalog paths and remain version-regression audit items. |

**Fix ownership (no overlap with the exploration itself):**

- `single_file._first_stream` → fixed by **`shared-source-streams`** (landed).
- TAR-RA + ISO lock wrapper → **`tar-concurrent-open`** (proposed; supersedes the
  exploration's TAR carve-out and the "ISO leave-alone" note).
- This exploration only **recorded** the audit; it did not ship those fixes.

---

## 2. Member-cache one-time-build safety

`BaseArchiveReader._materialize_members` publishes a single `_Materialized`
holder (`MemberListReport` + name index) once. After population the report is
read-mostly; the race is only the first build.

**Active design (`reader-concurrency-coordination`):** under `MemberStreams.CONCURRENT`,
overlapping first-touch callers block on a condition while exactly one owner builds
members/name indexes locally, completes link resolution, then publishes the holder
atomically. Waiters proceed against the published snapshot (no `ArchiveyUsageError`
for the overlap). A failed attempt (limits / interrupts — not terminal archive
damage) returns to `UNMATERIALIZED`, wakes waiters, and never publishes. Terminal
listing damage publishes an incomplete report (`error` set) instead. Heavy work
runs outside the reader-state lock. Default (non-`CONCURRENT`) and
uncontended paths stay unchanged. Distinct passes and shared streams remain single-owner.

---

## 3. Measurement design (substantiate performance decisions)

Per `VISION.md`: no perf claim without a benchmark. Home: `benchmarks/` (not built
here).

**Workloads**

| ID | Shape | Why |
|---|---|---|
| `zip-direct-many` | Many independent deflate members | Best case for per-member fan-out (C codec releases GIL). |
| `zip-stored-many` | Many stored members | I/O-bound baseline (little decode). |
| `7z-nonsolid` | Multi-folder / non-solid 7z | Folder-granularity parallelism. |
| `7z-solid-one-folder` | One solid folder, many members | Negative control — should *not* speed up under per-member fan-out. |
| `tar.gz-ra` | Compressed TAR, random-access open | Correct but shared-handle-serialized — expect no win. |
| `iso-direct-many` | Many ISO extents through one pycdlib image handle | Correctness-lock serialization baseline. |
| `single-file-large` | One large `.gz` / `.xz` | Sanity: one member, no fan-out opportunity. |

**Metrics:** use the subset relevant to the mechanism: wall and lock wait/hold time for the
TAR/ISO correctness baseline; bytes decompressed/read and seek counts where practical; peak
RSS only for changes that alter buffering/materialization; CPU utilization for decode
scheduling claims.

**Runtimes:** CPython GIL build (3.11/3.12) **and** `3.13t` free-threaded. Same
corpus, same scripts.

**Decision rule:** correctness changes have no speed threshold. A future
parallel-extraction consumer or handle/decode optimization must supply targeted before/after
measurements for its claimed benefit and relevant costs. Otherwise keep the invariant and
make no throughput claim.

---

## 4. Free-threading position (`threat-model.md` C4)

**Target stance (`concurrent-member-streams` + `reader-concurrency-coordination`):**

- Under `CONCURRENT`, first-touch materialization is coordinated and one reader supports
  concurrent `open()` plus independent operations on different returned streams.
  `close()` drains in-flight worker calls. Distinct passes (`__iter__` /
  `stream_members` / `extract_all`) and same-stream access remain single-owner /
  caller-synchronized.
- Single-owner APIs use explicit root tokens and private child scopes, so `extract_all()` may
  drive `stream_members()` and yielded-stream I/O without admitting unrelated reentry.
- This correctness seam must use real synchronization. A required Linux CPython `3.13t`
  `free-threaded-concurrency` job covers the zero-dependency core; optional backends are not
  claimed free-threaded-safe until a dedicated job can execute them.
- Parallel **decode/extract scheduling** across independent work units (members / folders /
  blocks) remains a separate future feature whose speed claims need the targeted measurements
  in §3 and §5.
- **C++ accelerator caveat:** `rapidgzip` (and similar) spawn `std::thread`s
  invisible to Python's threading. Free-threading does not remove the
  close-before-finalize requirement (`docs/internal/known-issues.md` Bugs 1–3). A parallel
  consumer must still never kill a source under a live accelerator stream, and
  must not assume more accelerator objects ⇒ linear speedup (FD / memory / thread
  pressure). Lifecycle leases therefore keep backend resources alive until all member
  streams close.

---

## 5. Work partitioning per format

| Format | Parallelizable unit | Constraint |
|---|---|---|
| ZIP / stored / deflate | **Per member** | Independent local headers; stdlib `_SharedFile` or path FDs. |
| Single-file | **N/A** (one member) | Re-open is fine; no fan-out. |
| 7z | **Per folder** (coders chain) | Returned streams need independent logical position/state. They may use per-open decode or synchronized bounded/spooled shared materialization; no guarantee eliminates re-decoding. |
| RAR | **Per solid block** | Same shape as 7z folders; `unrar` for data. |
| TAR-RA | **Per member logically** | One comprehensive per-reader lock covers archive initialization, `getmembers()`/EOF handle reads, member-open initialization, read/readinto/supported positioning, member close, and archive close; correctness is guaranteed but handle operations serialize. |
| ISO | **Per extent logically** | The same lock covers pycdlib archive open, member open/context entry, stream operations, and close. `walk()`/`get_record()` are currently in-memory catalog operations under the materialization owner. |
| Directory | **Per file** | Independent paths. |

A future `ExtractionCoordinator` fan-out must schedule at this granularity — not
blindly "one task per member" on solid archives.

---

## 6. "N readers over one path" vs. "one reader + shared source"

| Model | Pros | Cons |
|---|---|---|
| **N readers / one path** | Isolated caches/lifecycle and independent FDs. | N× header parse/list cost and FD pressure; password/config duplication; harder coordinator accounting. |
| **One reader + coordinated sources** | Single published member list/config/password context; the supported post-materialization worker seam. | Reader-wide mutation/close stays single-owner; shared handles may serialize; solid formats need independent logical stream state. |

**Recommendation:** use one materialized reader + coordinated sources for the public worker
seam. `open()` registration/lifecycle/password state is synchronized; workers operate on
distinct streams. Keep N readers as an isolation/multiprocess escape hatch. Engaging
SharedSource independent handles remains a measurement-informed future optimization.

Interaction with `safe-extraction`: archive-wide limits (`max_entries`,
`max_extracted_bytes`, ratio guard) must stay on a single coordinator thread (or a
locked counter); workers only produce bytes / paths.

---

## 7. Active recommendation before Phase 6

The archived exploration's "no further ABC change" conclusion is superseded. Before Phase 6:

1. Keep the `_open_member` no-unsynchronized-per-open-scratch invariant.
2. Keep `UNMATERIALIZED` / `MATERIALIZING` / `MATERIALIZED` cache state separate from
   lifecycle and publish atomically.
3. Add explicit operation-owner tokens/private child scopes and release them on generator
   exhaustion, error, close, or abandonment.
4. Add lifecycle leases/failure/finalizer/source-ownership semantics so live streams survive
   reader close and teardown is attempted once.
5. Refactor `ArchiveStream` lazy open/close to claim/call/publish so stream state is not held
   across backend/source or lifecycle acquisition.
6. Synchronize password known-good/provider state: callbacks are lock-free; validation may
   use a required backend/source lock but no lifecycle/cache/password lock.
7. Use SharedSource for archivey-owned ranges and comprehensive one-lock coordination for
   TAR/ISO.

Phase 6 native 7z/RAR should:

- Give each returned stream independent logical position/state using either a per-open
  decoder or synchronized bounded/spooled shared materialization.
- Hold no per-open scratch on the reader.
- Use the synchronized password/key and lifecycle mechanisms.
- Treat folder/block as the parallel unit in any later coordinator.

**Still deferred:**

- Parallel extraction feature / ExtractionCoordinator fan-out.
- Engaging SharedSource `independent_handles`.
- Native TAR reader / SharedSource-at-`offset_data` (lower priority than the lock
  wrapper in `tar-concurrent-open`).

If a Phase 6 design review discovers a backend that cannot honor the worker seam, add the
narrow coordination hook before implementation rather than adding a format carve-out.
