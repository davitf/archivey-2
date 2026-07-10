# Parallel-safe reader — exploration notes

> Seed for a future `parallel-extraction` change. The **committed** outcome of
> `openspec/changes/parallel-reader-exploration` is the `_open_member` reentrancy
> invariant (spec delta + ABC docstring). Everything below is analysis: no feature
> code. See also `shared-source-streams` (the concurrent-open source primitive this
> builds on) and `docs/threat-model.md` C4 (free-threading).

## Glossary

- **TAR-RA** — the **random-access TAR** reader (`TarReader` with `streaming=False`),
  backed by one shared `tarfile.TarFile` (and, for compressed tar, one decompressor).
  Distinct from streaming TAR (`streaming=True` / `r|` mode). TAR-RA is the hard
  single-decoder carve-out for concurrent-open and for this invariant.
- **SharedSource** — `streamtools` primitive: locked, per-view-position byte-range
  views over one seekable source (`shared-source-streams`).

---

## 1. Backend audit (`_open_member` vs. the reentrancy invariant)

Invariant (random-access, independent-open backends): `_open_member` is a function of
`(member, shared source)` — no shared-reader-state mutation, no per-open scratch on
`self`; archivey-owned byte ranges go through `SharedSource.view`.

| Backend | Regime | Compliant? | Notes |
|---|---|---|---|
| **directory** | RA, independent | **Yes** | Opens `self._root / member.name` — independent FD per open; no reader scratch. Outside SharedSource (filesystem paths, not a shared byte source). |
| **ZIP** | RA, independent | **Yes** | `_open_zip_entry` → `ZipFile.open(info)`; stdlib `_SharedFile` coordinates the handle (path *and* stream sources). No per-open scratch on `ZipReader`. Outside archivey SharedSource retrofit (library-owned addressing). |
| **single-file** | RA when seekable | **Yes** (post-`shared-source-streams`) | Seekable stream: `SharedSource.view(0)` + fresh codec per open. Path: independent codec FD per open. `_first_stream` scratch **removed**. Non-seekable: one-shot `_pending_stream` (streaming / single-pass — out of invariant scope). |
| **TAR-RA** | RA, **single decoder** | **Exempt** | One `self._tar` (`tarfile.TarFile`); `extractfile` is not safe for interleaved concurrent opens. Carved out by the concurrent-open SHALL and by this invariant. |
| **ISO** | RA, library-owned | **Yes** (archivey state) | `pycdlib.open_file_from_iso` owns seeking on the shared ISO handle. No per-open scratch on `IsoReader`. Outside SharedSource retrofit (same shape as ZIP / stdlib); not listed as non-compliant. |

**Fix ownership (no overlap with this change):**

- `single_file._first_stream` → fixed by **`shared-source-streams`** (landed).
- ISO leave-alone + design note → also **`shared-source-streams`**.
- This change only **records** the audit; it does not re-fix those items.

---

## 2. Member-cache one-time-build safety

`BaseArchiveReader._get_members_registered` does an unguarded read-modify-write into
`_members_cache` / `_members_by_name_lists`. After population the caches are
read-mostly; the race is only the first build.

**Options:**

1. **Materialize-before-fan-out precondition** (recommended for v1) — a concurrent
   consumer MUST finish a random-access member pass before opening members
   concurrently. Documented on the ABC now; zero runtime cost; matches how
   `ExtractionCoordinator` already prefers an indexed list when available.
2. **Init-under-lock** — wrap the cache build in a `threading.Lock` (or
   `call_once`-style guard). Small, correct if someone ignores the precondition;
   still does not make the *rest* of the reader thread-safe.

**Recommendation:** keep (1) as the public contract for any future parallel
consumer; consider (2) only if/when a parallel-extraction feature lands and wants
defense-in-depth. Do **not** pretend the whole reader is thread-safe.

---

## 3. Benchmark design (decide the feature with numbers)

Per `VISION.md`: no perf claim without a benchmark. Home: `benchmarks/` (not built
here).

**Workloads**

| ID | Shape | Why |
|---|---|---|
| `zip-direct-many` | Many independent deflate members | Best case for per-member fan-out (C codec releases GIL). |
| `zip-stored-many` | Many stored members | I/O-bound baseline (little decode). |
| `7z-nonsolid` | Multi-folder / non-solid 7z | Folder-granularity parallelism. |
| `7z-solid-one-folder` | One solid folder, many members | Negative control — should *not* speed up under per-member fan-out. |
| `tar.gz-ra` | Compressed TAR, random-access open | Exempt / single-decoder — expect no win (or serialization). |
| `single-file-large` | One large `.gz` / `.xz` | Sanity: one member, no fan-out opportunity. |

**Metrics:** wall time; bytes decompressed (avoid "faster" that re-decodes more);
seek counts / lock hold time on SharedSource; peak RSS; CPU utilization.

**Runtimes:** CPython GIL build (3.11/3.12) **and** `3.13t` free-threaded. Same
corpus, same scripts.

**Decision rule:** ship a parallel-extraction consumer only if at least one
realistic DIRECT / multi-folder workload shows a clear wall-time win on GIL *or*
3.13t without inflating bytes-decompressed on solid archives. Otherwise keep the
invariant and leave the feature deferred.

---

## 4. Free-threading position (`threat-model.md` C4) — draft

**Stance (draft for C4):**

- Archivey's public contract stays **"one `ArchiveReader` per thread"** for v1.
  Concurrent `open()` / iteration / `close()` on one reader remains unsupported /
  undefined (`packaging-and-extras`).
- What *can* become useful under `3.13t` is parallel **decode/extract across
  independent work units** (members / folders / blocks), either via N readers over
  one path or one reader + SharedSource — decided by the benchmark (§3) and §5.
- **C++ accelerator caveat:** `rapidgzip` (and similar) spawn `std::thread`s
  invisible to Python's threading. Free-threading does not remove the
  close-before-finalize requirement (`docs/known-issues.md` Bugs 1–3). A parallel
  consumer must still never kill a source under a live accelerator stream, and
  must not assume more accelerator objects ⇒ linear speedup (FD / memory / thread
  pressure).
- Update C4 from "Backlog" to this stance when the parallel-extraction change is
  proposed; until then this doc is the draft.

---

## 5. Work partitioning per format

| Format | Parallelizable unit | Constraint |
|---|---|---|
| ZIP / stored / deflate | **Per member** | Independent local headers; stdlib `_SharedFile` or path FDs. |
| Single-file | **N/A** (one member) | Re-open is fine; no fan-out. |
| 7z | **Per folder** (coders chain) | Members *within* one folder share decompressor state — sequential. Separate folders are independent; each `open()` may re-decode from folder start (already allowed by solid-open scenarios). |
| RAR | **Per solid block** | Same shape as 7z folders; `unrar` for data. |
| TAR-RA | **None** (single `tarfile`) | Exempt; serialize or one stream at a time. |
| ISO | **Per extent** (pycdlib) | Library-owned; treat like ZIP path-source unless archivey wraps later. |
| Directory | **Per file** | Independent paths. |

A future `ExtractionCoordinator` fan-out must schedule at this granularity — not
blindly "one task per member" on solid archives.

---

## 6. "N readers over one path" vs. "one reader + shared source"

| Model | Pros | Cons |
|---|---|---|
| **N readers / one path** | Each reader has isolated state (caches, lifecycle); true parallel I/O via independent FDs; matches today's "one reader per thread" public rule. | N× header parse / member-list cost; N× FD; passwords/config must be duplicated; harder to share extraction progress / limits in one coordinator. |
| **One reader + SharedSource** | Single member list; one config/password context; SharedSource already serializes seek+read safely; fits ExtractionCoordinator as one pull sink with worker threads on open streams. | Reader object itself still not thread-safe (open/iter/close); needs materialize-before-fan-out; path-source independent handles still dormant; solid formats need per-unit decompressors. |

**Recommendation for a future feature:** prefer **one reader + SharedSource** for
path and stream sources once the member list is materialized, with worker threads
only driving already-opened (or about-to-open) **member streams** — never
concurrent `reader.open` registration without a lock. For path sources under heavy
I/O, optionally engage SharedSource's dormant `independent_handles` seam (benchmark
gate). Keep **N readers** as an escape hatch for callers who already multiprocess
and want isolation, not as the library's primary parallel API.

Interaction with `safe-extraction`: archive-wide limits (`max_entries`,
`max_extracted_bytes`, ratio guard) must stay on a single coordinator thread (or a
locked counter); workers only produce bytes / paths.

---

## 7. Recommendation — does the ABC need more before Phase 6?

**No further ABC change is required for Phase 6** beyond what this exploration
commits:

1. The `_open_member` reentrancy invariant (spec + docstring) — **done**.
2. The materialize-before-fan-out precondition — **documented** on the ABC.
3. SharedSource for archivey-owned byte ranges — **landed** in
   `shared-source-streams`.

Phase 6 native 7z/RAR should:

- Give each `open()` its own decompressor over its own `SharedSource` view
  (re-decode from folder/block start when solid).
- Hold no per-open scratch on the reader.
- Treat folder/block as the parallel unit in any later coordinator.

**Explicitly deferred (do not block Phase 6):**

- Parallel extraction feature / ExtractionCoordinator fan-out.
- Member-cache init-under-lock.
- Engaging SharedSource `independent_handles`.
- Free-threading public API changes.

If a Phase 6 design review discovers a backend that cannot honor the invariant
without an ABC hook (e.g. a mandatory shared decoder), carve it out like TAR-RA
rather than weakening the contract.
