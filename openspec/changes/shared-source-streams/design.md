# Design — Shared-source stream primitive + concurrent-open contract

Decisions locked here so implementation does not start from proposal + tasks alone. Lettering
matches the implementer review.

## A. Exception typing under the `streamtools` boundary

`streamtools` is deliberately archivey-dependency-free (it imports no `archivey.exceptions`;
see the module docstrings). Therefore:

- **The primitive raises stdlib-shaped errors** — `ValueError` / `OSError` /
  `io.UnsupportedOperation`, matching stdlib streams and today's `ArchiveStream`
  (`ValueError("I/O operation on closed file.")`). It does **not** define or raise any
  archivey exception class.
- **Translation happens at the reader boundary** — `ArchiveStream` / the backend maps those
  stdlib-shaped errors to the appropriate `ArchiveyError` with context, exactly as codec
  errors are translated today.

So "fail loudly with a typed error" in the `archive-reading` delta means *typed at the reader
surface*, produced by the existing translation layer — **not** a new exception in
`streamtools`.

## B. Path-source independent handles — dormant

The `SharedSource` API carries the seam for minting a *fresh* `open(path, 'rb')` handle per
view (true parallel I/O for path sources), but for this change it is **dormant / default
off**: every view shares one handle guarded by one lock. Engaging live per-view handles (with
its extra FD pressure and different close semantics) belongs with the parallel-extraction
feature, gated on that change's benchmark. The seam is an API/flag now so engaging it later is
not a retrofit.

## C. ZIP — path vs stream source

- **Path source:** stdlib `zipfile` opens and owns its own handle and already uses
  `_SharedFile` internally, so archivey adds **no wrap** — but a concurrent-open test is still
  required to lock the behavior in.
- **Stream source:** archivey owns the handle it passes into `ZipFile`; that handle is wrapped
  so a second archivey-level `open()` is coordinated by the same contract.

## D. ISO — carved out of this change's retrofit

ISO serves member data through `pycdlib`'s `open_file_from_iso` → `_PyCdlibStream`, which does
its **own** seeking on the shared ISO handle; it is not a byte-range slice we can express as
`SharedSource.view(start, length)` without extracting extent offsets from pycdlib (a different,
larger design — "lock around pycdlib's own IO" vs. "bypass to raw extent reads"). Rather than
force that decision into a *gate*, ISO is **carved out**: it keeps its current single-stream
behavior and is listed as a known-non-compliant backend (see the delta carve-out), tracked for
the `parallel-reader-exploration` audit. The primitive is validated instead against
single-file and ZIP (below), which map cleanly to byte-range views. If we later want ISO
concurrent-open, the cheap route is a `SharedSource.critical_section()` lock wrapper around
pycdlib reads — noted, not built here.

## E. TAR (and solid / single-decoder backends) — carved out

The random-access TAR reader is backed by one `tarfile.TarFile` object (and, for compressed
tar, one decompressor); interleaved `open()` of two members is **not** correct under a single
shared decoder. The concurrent-open requirement is therefore scoped (see the delta) to
**backends that serve members via independent byte-range views over the source**. Backends that
reuse a single shared decoder/parser object — the stdlib-`tarfile`-backed TAR reader today, and
any future format that does likewise — are **exempt** and MUST serve one member stream at a
time (or serialize). Solid 7z/RAR *can* comply in Phase 6 by giving each `open()` its own
decompressor over its own shared-source view (re-decoding from folder/block start, as the
`archive-reading` solid-open scenario already allows); that is a Phase 6 obligation, not a gap
here.

## F. "Fail loudly" (detectable) vs "unsupported" (undefined) — kept distinct

Two different things, not to be conflated in the spec:

- **Detectable primitive misuse → raises**: read/seek after `close()`, or a view whose bounds
  fall outside the source. The primitive *can* see these and raises.
- **Reader-object multi-thread misuse → unsupported, undefined**: driving one
  `BaseArchiveReader` (concurrent `open()` / iteration / `close()`) from several threads is
  **not** detected — the reader has no lock — so the spec says *unsupported*, not *rejected*.
  The wording must not imply we detect it.

## G. No public packaging carve-out (narrower v1 promise)

We do **not** amend `packaging-and-extras` to promise cross-thread reads of already-open
streams. That statement, in the public install contract, would invite callers to rely on
cross-thread stream reading in v1 — which we do not want to commit to. Instead:

- The **supported public contract** — *multiple concurrently-open member streams from one
  reader (single thread) are correct* — lives in `archive-reading` (it is not a threading
  claim at all).
- The "reads are data-correct-under-lock across threads" property is an **implementation
  consequence**, recorded here in design, **not** a spec `SHALL`. `packaging-and-extras` keeps
  its flat "not thread-safe (one per thread)". (This is why this change ships **no**
  `packaging-and-extras` delta.)

## H. Relationship to `SlicingStream` — compose, don't replace

`SlicingStream` already tracks a per-view `_pos`, but its `read()` does **not** re-seek the
underlying to `_pos` before reading — it reads from wherever the shared handle currently sits,
which is exactly the clobber bug when two slices share a handle. `SharedSource.view` is
therefore *"a `SlicingStream` that, under the source lock, re-seeks the underlying to its own
absolute position before every read"*. Implement it by composing/subclassing the slice logic
plus lock+reseek; **existing `SlicingStream` callers are unchanged** (single-stream use never
had the bug).

## Validation scope (what this change actually retrofits)

- **single-file** — routes its member open through the primitive; also removes the
  `_first_stream` eager-stream scratch as part of making open reentrant (coordinated with
  `parallel-reader-exploration`, which owns the invariant).
- **ZIP** — stream-source handle wrap + concurrent-open tests (path source unchanged).
- **ISO, TAR-RA** — carved out (documented non-compliant); not retrofitted here.
