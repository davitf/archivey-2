# Shared-source stream primitive + concurrent-open contract (Phase 6 gate)

## Why

Phase 6's native 7z/RAR readers will hand out **multiple concurrently-open member streams**
over a single underlying source. A source (an OS file handle or a `BinaryIO`) has exactly
**one file position**, so two open member streams that each `seek()`+`read()` the same handle
will clobber each other's offset and return interleaved/wrong bytes — **even in a single
thread**:

```python
s1 = ar.open("big1.bin")   # region [1000, …)
s2 = ar.open("big2.bin")   # region [5000, …)
s1.read(100)               # seek(1000), read
s2.read(100)               # seek(5000), read  → leaves the handle at 5100
s1.read(100)               # MUST seek back to 1100 first, or it reads big2's bytes
```

Today's seekable backends don't hit this: ZIP rides stdlib `zipfile`'s own `_SharedFile`,
ISO rides `pycdlib`, and single-file archives have one member. The native readers are the
**first** archivey-owned consumers that need it, so `PLAN.md` lands the plumbing **before**
Phase 6 as an entry gate. The `IDEAS.md` parallel-extraction note fixes the shape: a
`streamtools` shared-source view mirroring stdlib `zipfile._SharedFile` — one underlying
handle + a lock + a per-view position, each read seeking under the lock — plus a **decided
concurrency contract**: what is supported vs. what **fails loudly**, never silent
interleaving.

## What Changes

### The `streamtools` shared-source primitive (parallel-*ready*, single-reader *contract*)

Add a `SharedSource` factory to `streamtools` (which stays archivey-dependency-free):

- **`SharedSource.view(start, length) -> BinaryIO`** — mints an independent, seekable,
  **non-owning** view over `[start, start+length)` of the source. Each view carries its own
  `_pos`; every read does `seek(self._pos); data = read(n); self._pos += len(data)` under the
  source's lock, so the seek+read pair is **atomic** and interleaved views never corrupt each
  other. Views do not close the underlying source (the `SharedSource` owns it).
- **Lock included** — a `threading.Lock` guards seek+read, so the primitive is already
  thread-**correct** (concurrent readers are serialized at the I/O boundary, not garbled).
  This is cheap insurance; it does **not** by itself make the `ArchiveReader` thread-safe (the
  reader has other unguarded state — see the exploration change).
- **Parallel-ready seam** — for a **path** source, `SharedSource` can mint a *fresh
  independent handle* (`open(path, 'rb')`) per view for true parallel I/O; for a **stream**
  source (socket / `BytesIO` / pipe) it returns a locked shared view. The seam exists from day
  one so the later parallel-extraction feature adds a *consumer*, not a retrofit. Whether the
  path-source parallel handle is engaged now or left dormant is an implementation detail of
  this change; the **contract** below does not promise parallelism yet.

### The concurrency contract (decided; specced)

- **Supported:** any number of concurrently-open member streams from **one reader**, read in
  interleaved order, all correct.
- **Supported, correct-but-serialized:** reading already-opened member streams from different
  threads does not corrupt data (the lock), but is not parallel I/O and is not a supported
  performance path in v1.
- **Rejected loudly:** using the *reader object itself* from multiple threads (concurrent
  `open()`/iteration/`close`) is unsupported; misuse that the primitive can detect (e.g. a
  read after `close`, or a view over a closed source) raises a typed error rather than
  returning silent garbage. `ArchiveReader` remains one-per-thread.

### Retrofit current backends (validate the primitive before Phase 6)

Route the existing archivey-owned seekable readers through `SharedSource` so the primitive is
exercised by real backends now, not only unit tests:

- **ISO** and **single-file** member opens go through `SharedSource.view(...)` instead of the
  ad-hoc slice/seek they use today.
- **ZIP** continues to rely on stdlib `zipfile` for member decode, but archivey's own handle
  handed to `ZipFile` is wrapped so a second archivey-level open is coordinated by the same
  contract (no regression to zipfile's `_SharedFile` behavior).
- Add a concurrent-open test (two members open and interleaved) for each retrofitted backend.

## Impact

- Affected specs: `archive-reading` (ADDED — concurrent-open member streams), `packaging-and-extras`
  (MODIFIED — the thread-safety statement, clarified to carve out already-open member streams).
- Affected code: `src/archivey/internal/streams/streamtools/` (new `SharedSource`),
  `iso_reader.py`, `single_file_reader.py`, `zip_reader.py`, plus tests.
- Risk: medium — touches working backends. Mitigated by the retrofit being a like-for-like
  swap of the slice/seek mechanism with the primitive plus added concurrent-open coverage; no
  behavior change for single-stream use.
- Relationship: this lands the **primitive + contract**. Making the *whole reader* parallel-safe
  (the `_open_member` reentrancy invariant + parallel extraction) is the separate
  `parallel-reader-exploration` change; this change deliberately keeps that seam open without
  promising it.
