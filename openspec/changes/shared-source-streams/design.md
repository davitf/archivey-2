## Context

`streamtools` today offers `BinaryIOWrapper`, `DelegatingStream`, `SlicingStream`,
and seek/size helpers — enough to adapt and bound a single view of a stream.
It does **not** provide a shared underlying handle with independent cursors.
Stdlib `zipfile` solves multi-open with `_SharedFile`: one file object, one
lock, per-view `_pos`; every `read`/`seek` does `seek(view_pos)` under the lock
then updates the view. `PLAN.md` Phase 6 entry criteria and `IDEAS.md`
("Parallel extraction / concurrent member streams") require the same shape
before native 7z/RAR (and any native ZIP) land, plus an explicit contract:
supported interleaved single-threaded opens work by construction; unsupported
misuse fails loudly.

Today ZIP member opens go through `zipfile.ZipFile.open`, which already uses
`_SharedFile` internally — so ZIP callers already get interleaved opens "for
free." Native readers will own the file handle themselves and must not invent a
per-format lock. Putting the primitive in `streamtools` keeps it codec-agnostic
and reusable.

## Goals / Non-Goals

**Goals:**

- Ship a `SharedSource` (owner) + view stream API under `streamtools`.
- Lock the reader-level concurrency contract in specs.
- Prove interleaved single-threaded reads with tests; prove loud failure on
  cross-thread use and on a second view over a non-seekable source.
- Adopt the primitive in at least one in-tree path (or a thin adapter test
  double) so Phase 6 has a worked example — prefer a real ZIP-adjacent or
  single-file slice path if natural; otherwise a dedicated unit test over a
  temp file is enough for *this* change, with native readers as first
  production consumers.

**Non-Goals:**

- Multi-threaded parallel extraction / free-threading (3.13t) support.
- Making `ArchiveReader` instances thread-safe (project rule stays: one reader
  per thread).
- Rewriting TAR/ISO/directory backends to use shared views (TAR streaming is
  forward-only; ISO already documents its own constraints).
- Exporting the primitive from the public `archivey` package.

## Decisions

### 1. API shape — owner + views (zipfile model)

```text
SharedSource(fileobj, *, lock=None)
  .view(start: int = 0, length: int | None = None) -> SharedSourceView
  .close() / context manager
```

- `SharedSource` owns the underlying `BinaryIO` and the lock (`threading.RLock`
  by default so a view method can re-enter if needed).
- `SharedSourceView` is a `ReadOnlyIOStream` / `BinaryIO`: holds `_pos` (and
  optional end bound, composing cleanly with today's `SlicingStream` role for
  member ranges). `read`/`seek`/`tell` take the lock, reposition the underlying
  file, transfer, update `_pos`.
- Closing a **view** does not close the owner; closing the **owner** invalidates
  views (subsequent I/O raises). Mirror zipfile's refcount/`_fileRefCnt`
  pattern lightly — an open-view count that delays underlying close until the
  last view releases is acceptable if it simplifies ZIP-like use; document the
  chosen rule in the module docstring.

**Alternative considered:** only a `SlicingStream` over a locked wrapper without
an explicit owner type — rejected; the owner is where close/refcount and
"create view" live, matching zipfile and keeping lock lifetime obvious.

### 2. Concurrency contract (decided here, specced in deltas)

| Situation | Behavior |
|-----------|----------|
| Multiple views, one thread, seekable source | Supported — interleaved reads return correct bytes |
| Second `view()` on a non-seekable / non-`seek`-capable source | Fail at view creation (`UnsupportedOperationError` or `ValueError` at the streamtools layer; archivey translates if needed) |
| Same `ArchiveReader` used from two threads | Unsupported — detect and raise loudly when a second thread enters reader I/O while another holds/uses it (**or** document "undefined / not detected" only if detection is unreliable — prefer detection via an owning-thread id check on entry to `open`/`read`/`stream_members`) |
| Two `ArchiveReader` instances on the same path | Supported (separate handles) — already true |

**Owning-thread check:** store `threading.get_ident()` at reader open (or first
I/O); on subsequent public I/O entry, if the ident differs, raise
`UnsupportedOperationError` with a clear message. This is best-effort
misuse detection, not a lock that makes the reader safe.

**Alternative considered:** a real `threading.Lock` around all reader methods to
"make it safe" — rejected; the project concurrency model is one-reader-per-thread,
and locking would hide bugs while inviting false confidence about parallel
extraction.

### 3. Stay inside `streamtools`' independence rule

`streamtools` MUST NOT import archivey exceptions. Raise `OSError` /
`ValueError` / a small local error if needed; `base_reader` / backends translate
to `UnsupportedOperationError` when exposing the failure on the public reader
API. Same pattern as other streamtools boundaries.

### 4. Adoption scope for this change

1. Implement + unit-test the primitive thoroughly (interleave, bounds, close).
2. Add the reader owning-thread guard on `BaseArchiveReader` public I/O entry
   points (or a single `_check_owner_thread()` helper called from them).
3. Optional stretch: use `SharedSource` for a backend that today opens
   independent slices over one file without going through zipfile — only if a
   clean call site exists; otherwise leave adoption to Phase 6.

### 5. Spec split

- Behavioral stream requirements → `compressed-streams` (home of streamtools).
- Reader-visible concurrency rules → `archive-reading`.
- Non-seekable / `FORWARD_ONLY` interaction → `access-mode-and-cost` (short
  ADDED requirement, not a rewrite of CostReceipt).

## Risks / Trade-offs

- **[Lock contention on tiny reads]** → Acceptable; matches zipfile. Native
  solid formats already dominate cost with decompression.
- **[False positive on owning-thread check with legitimate handoff]** → Rare;
  document that readers must not migrate threads. No public "detach" API in v1.
- **[Double-locking if a view is used under an outer lock]** → Use `RLock`.
- **[Phase 6 still reinvents something]** → Mitigation: design review of native
  reader proposals must reference this primitive; tasks call that out.

## Migration Plan

Additive. No public API break. Rollback = revert; ZIP behavior unchanged if
adoption is optional.

## Open Questions

- Exact exception type at the streamtools boundary for non-seekable
  `view()` — lean `ValueError("underlying stream is not seekable")` unless an
  existing streamtools precedent says otherwise.
- Whether the owning-thread guard belongs in this change or a tiny follow-up —
  **include it here**; the contract is incomplete without loud failure.
