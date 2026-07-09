# Tasks — Shared-source stream primitive + concurrent-open contract

> Run tools through uv: `uv run pytest`, `uv run pyrefly check`, `uv run ty check`,
> `uv run ruff`. Prerequisite: none (lands ahead of Phase 6 as an entry gate).
> `streamtools` stays archivey-dependency-free — the new primitive imports only stdlib +
> other `streamtools` modules, and raises **stdlib-shaped** errors (no `archivey.exceptions`).
> Read `design.md` first — it locks the decisions (A–H) this task list implements.
> Module paths: primitive under `src/archivey/internal/streams/streamtools/`; backends under
> `src/archivey/internal/backends/{single_file_reader,zip_reader}.py`.

## 0. Decisions locked (see design.md)

- [ ] 0.1 **Parallel-*ready* primitive, single-reader *contract*** — per-view positions + a
      lock (thread-correct) + a path-source independent-handle seam.
- [ ] 0.2 **Path-source independent handles are DORMANT** (default off; one shared handle +
      lock). Live per-view handles ship with parallel extraction, not here. (design §B)
- [ ] 0.3 **Primitive raises stdlib-shaped errors** (`ValueError`/`OSError`/
      `io.UnsupportedOperation`); the reader boundary translates to `ArchiveyError`. (design §A)
- [ ] 0.4 **Retrofit = single-file + ZIP(stream-source wrap)**; **ISO and TAR-RA are carved
      out** (documented non-compliant, tracked for the parallel-reader audit). (design §D–E)
- [ ] 0.5 **No `packaging-and-extras` delta** — public contract stays flat "not thread-safe";
      supported contract lives in `archive-reading`. (design §G)
- [ ] 0.6 **Reader stays one-per-thread** — this change does NOT make `BaseArchiveReader`
      parallel-safe (that is `parallel-reader-exploration`).

## 1. The `SharedSource` primitive

- [ ] 1.1 Add `SharedSource` to `streamtools` (e.g. `streamtools/shared.py`), constructed from
      either a `Path` or an already-open seekable `BinaryIO`. Holds the source handle, a
      `threading.Lock`, and closed-state. Imports only stdlib + `streamtools`.
- [ ] 1.2 `SharedSource.view(start, length) -> BinaryIO` — a non-owning, seekable view with its
      own `_pos`. **Compose the `SlicingStream` bound/tell logic, but re-seek the underlying to
      the view's own absolute position under the lock before every read** (`SlicingStream`
      today does NOT re-seek — that is the clobber bug). `read`:
      `with lock: underlying.seek(start + _pos); data = underlying.read(n)`; `_pos +=
      len(data)`. Views MUST NOT close the source. Existing `SlicingStream` callers unchanged.
      (design §H)
- [ ] 1.3 Path-source seam (dormant): design `view()` so a fresh `open(path,'rb')` backing can
      be swapped in later behind a flag; do not engage it now. Document it as the parallel-I/O
      entry point. (design §B)
- [ ] 1.4 Misuse detection (stdlib-shaped): read/seek after `close()` raises
      `ValueError`/`OSError`; a view whose bounds exceed the source raises `ValueError` at
      construction. No silent short/garbage reads.
- [ ] 1.5 Re-export `SharedSource` from `streamtools/__init__.py`.

## 2. Primitive tests (unit + property)

- [ ] 2.1 Interleaved-read test: open two overlapping/adjacent views, read them in a shuffled
      partial-read interleaving, assert each returns exactly its region's bytes.
- [ ] 2.2 Thread-correctness test: two threads each read a distinct view to completion; assert
      byte-exact output (data-correct under the lock). Keep it deterministic/non-flaky.
- [ ] 2.3 Misuse tests: read-after-close raises `ValueError`/`OSError`; out-of-bounds view
      raises `ValueError` at construction.
- [ ] 2.4 (Optional, non-blocking) a Hypothesis property: random non-overlapping regions ×
      random interleavings → every view's concatenated reads equal its region. Use it **only if
      `hypothesis-property-tests` has landed**; otherwise a plain parametrized test. Do not
      block this change on Hypothesis.

## 3. Backend retrofit (single-file + ZIP)

- [ ] 3.1 **single-file**: route the member open through `SharedSource.view(...)`; **remove the
      `_first_stream` eager-stream scratch** so `_open_member` is reentrant (coordinates with
      the `parallel-reader-exploration` invariant). Confirm the single-member path and
      non-seekable behavior are unchanged.
- [ ] 3.2 **ZIP path source**: no wrap (stdlib `zipfile` already correct); add a concurrent-open
      test (two members interleaved) to lock the behavior in.
- [ ] 3.3 **ZIP stream source**: wrap the archivey-owned handle passed to `zipfile.ZipFile` so a
      second archivey-level `open()` is coordinated by the contract; verify no regression vs.
      stdlib `_SharedFile` (existing ZIP tests stay green) + a concurrent-open test.
- [ ] 3.4 Confirm cost/stream-capability reporting for the touched backends is unchanged.
- [ ] 3.5 **ISO + TAR-RA**: no code change; confirm the `archive-reading` carve-out names them
      and that their current single-stream behavior is unaffected.

## 4. Spec + gate

- [ ] 4.1 `archive-reading` delta (concurrent-open member streams + solid/single-decoder
      carve-out) covered by tests in §2–§3.
- [ ] 4.2 `openspec validate --strict shared-source-streams` passes.
- [ ] 4.3 Full suite green in all three dependency configs (`[all]`, `[all-lowest]`,
      `[core-only]`); Pyrefly + ty + ruff clean.
