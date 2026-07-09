# Tasks — Shared-source stream primitive + concurrent-open contract

> Run tools through uv: `uv run pytest`, `uv run pyrefly check`, `uv run ty check`,
> `uv run ruff`. Prerequisite: none (lands ahead of Phase 6 as an entry gate).
> `streamtools` stays archivey-dependency-free — the new primitive imports only stdlib +
> other `streamtools` modules.
> Module paths: primitive under `src/archivey/internal/streams/streamtools/`; backends under
> `src/archivey/internal/backends/{iso_reader,single_file_reader,zip_reader}.py`.

## 0. Decisions locked in this change

- [ ] 0.1 **Parallel-*ready* primitive, single-reader *contract*** — per-view positions + a
      lock (thread-correct) + a path-source independent-handle seam; but the spec promises
      only interleaved single-reader use, not parallelism.
- [ ] 0.2 **Fail loudly, never silently interleave** — detectable misuse (read-after-close,
      out-of-bounds view) raises a typed error.
- [ ] 0.3 **Retrofit ISO + single-file now; wrap ZIP's archivey handle** — validate the
      primitive against real backends before Phase 6.
- [ ] 0.4 **Reader stays one-per-thread** — this change does NOT make `BaseArchiveReader`
      parallel-safe (that is `parallel-reader-exploration`).

## 1. The `SharedSource` primitive

- [ ] 1.1 Add `SharedSource` to `streamtools` (e.g. `streamtools/shared.py`), constructed from
      either a `Path` or an already-open seekable `BinaryIO`. Holds the source handle, a
      `threading.Lock`, and closed-state.
- [ ] 1.2 `SharedSource.view(start, length) -> BinaryIO` — a non-owning, seekable view with its
      own `_pos`; `read` does `with lock: seek(_pos); data = read(n)`; `_pos += len(data)`.
      Views MUST NOT close the source. Reuse/compose `SlicingStream` semantics where possible
      (per-view bounds + zero-origin `tell()`), but ownership of seeking moves under the lock.
- [ ] 1.3 Path-source seam: allow a view to be backed by a *fresh* `open(path, 'rb')` handle
      (independent position, no lock contention) — implement the seam even if dormant; document
      it as the parallel-I/O entry point. Stream sources always use the locked shared handle.
- [ ] 1.4 Misuse detection: read/seek after `close()` raises a typed error; a view whose bounds
      exceed the source raises at construction. No silent short/garbage reads.
- [ ] 1.5 Re-export `SharedSource` from `streamtools/__init__.py`.

## 2. Primitive tests (unit + property)

- [ ] 2.1 Interleaved-read test: open two overlapping/adjacent views, read them in a shuffled
      partial-read interleaving, assert each returns exactly its region's bytes.
- [ ] 2.2 Thread-correctness test: two threads each read a distinct view to completion; assert
      byte-exact output (data-correct under the lock). Keep it deterministic/non-flaky.
- [ ] 2.3 Misuse tests: read-after-close and out-of-bounds view each raise the typed error.
- [ ] 2.4 (Optional) a Hypothesis property: for random non-overlapping regions and random
      interleavings, every view's concatenated reads equal its region. (Coordinates with the
      `hypothesis-property-tests` change if it lands first; otherwise a plain parametrized test.)

## 3. Backend retrofit

- [ ] 3.1 **single-file**: route the member open through `SharedSource.view(...)`; confirm the
      single-member path is unchanged and non-seekable sources still behave per spec.
- [ ] 3.2 **ISO**: replace the ad-hoc slice/seek in `_open_member` with `SharedSource.view(...)`;
      keep the zero-origin wrapping ISO needs. Add a two-members-open-interleaved test.
- [ ] 3.3 **ZIP**: wrap the archivey-owned handle passed to `zipfile.ZipFile` so a second
      archivey-level `open()` is coordinated by the same contract; verify no regression vs.
      stdlib `_SharedFile` (existing ZIP tests stay green) and add a concurrent-open test.
- [ ] 3.4 Confirm cost/stream-capability reporting for all three backends is unchanged.

## 4. Spec + gate

- [ ] 4.1 `archive-reading` delta (concurrent-open member streams) covered by tests in §2–§3.
- [ ] 4.2 `packaging-and-extras` thread-safety statement updated (open-streams carve-out).
- [ ] 4.3 `openspec validate --strict shared-source-streams` passes.
- [ ] 4.4 Full suite green in all three dependency configs (`[all]`, `[all-lowest]`,
      `[core-only]`); Pyrefly + ty + ruff clean.
