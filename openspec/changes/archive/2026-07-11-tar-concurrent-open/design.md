## Context

> **Depends on `concurrent-member-streams`.** Simultaneous random-access member streams are
> a declared capability (`MemberStreams.CONCURRENT`); once declared, they are safe by
> construction and concurrent `open()` is supported after materialization. That change owns
> the cross-format capability/lifecycle contract. Everything below is the TAR + ISO
> mechanism that satisfies it, instantiated only for `CONCURRENT` readers — the undeclared
> default path takes no shared-handle lock.

| Layer | Shared-handle behavior | Existing lock? |
|---|---|---|
| `tarfile.TarFile` / `_FileInFile` | archive scan/open and member read seek+read use shared `fileobj`; positioning/close also belong to the stream boundary | No |
| `pycdlib.PyCdlib` / `PyCdlibIO` | archive/member open/context entry and member read/seek use `PyCdlib._cdfp` / `PyCdlibIO._fp`; tell/close complete the stream boundary | No |
| `zipfile._SharedFile` | open/read/seek coordination under `ZipFile._lock` | Yes |
| `SharedSource` views | per-view position; seek+read under source lock | Yes |

TAR and ISO share the seek-before-read hazard. Source inspection adds two important details:
TAR `getmembers()` calls `_load()` / `next()` and therefore seeks/tells/reads the shared
`fileobj`, and Archivey's strict EOF check reads that object directly. In pinned pycdlib,
`walk()` / `get_record()` traverse already-parsed in-memory records, while
`open_file_from_iso()` consults shared caches and `PyCdlibIO.__enter__`/I/O seeks the image
handle. Locking only member `read()` is therefore insufficient, but catalog-only calls need
not be mislabeled as handle I/O. Routing TAR through
`SharedSource.view(offset_data, size)` would reimplement sparse — rejected for now.

## Goals / Non-Goals

**Goals:**

- TAR-RA and ISO satisfy the declared-`CONCURRENT` random-access member contract.
- Preserve tarfile sparse / pycdlib extent logic unchanged.
- One small streamtools primitive plus backend helpers reusable by both backends.
- Make lock coverage and callback boundaries auditable.
- Measure the serialization cost before claiming parallel speed or replacing the mechanism.

**Non-Goals:**

- Native TAR reader / SharedSource-at-`offset_data`.
- Streaming TAR concurrent open.
- Concurrent reader-wide mutation/iteration/close (owned and excluded by
  `concurrent-member-streams`).
- Parallel extraction or parallel-throughput guarantees.

## Decisions

### D1. One comprehensive shared-handle critical section

**Choice:** Each reader owns one lock, and every operation that can touch/reposition/close
its library's shared handle uses it. The audited minimum is:

- TAR `tarfile.open()` and ISO `PyCdlib.open()` / `open_fp()` archive initialization;
- TAR `getmembers()` and Archivey's direct strict-EOF `TarFile.fileobj.read()`;
- TAR `extractfile()` and ISO `open_file_from_iso()` plus `PyCdlibIO.__enter__`;
- member `read` and `readinto`, plus `seek`/`tell` when the inner stream supports them;
- member close / ISO context exit;
- archive close;
- any additional `TarFile.fileobj` / `PyCdlib._cdfp` / `PyCdlibIO._fp` operation found by
  implementation audit.

Locking the raw FD's `seek` and `read` separately is insufficient: the whole library call
must be atomic because the library performs its own seek-before-read sequence.

The pinned pycdlib `walk()` and `get_record()` implementations do not touch `_cdfp` in the
read-only reader, so the materialization operation-owner scope is sufficient for them.
Implementation records this version audit and includes a regression probe; if a supported
pycdlib version performs handle I/O there, those complete calls join the backend critical
section.

### D2. One lock per reader instance

**Choice:** `TarReader` and `IsoReader` each own one `threading.Lock` shared by every
member stream and the archive/library close path.

The implementation MAY use an `RLock` only if audit proves same-thread library reentry is
required. It MUST NOT use reentrancy to permit archivey callbacks under the lock.

### D3. Keep using `extractfile` / pycdlib for data

**Choice:** Do not bypass the libraries for member bytes. Sparse TAR stays stdlib's problem.

**Alternative rejected:** SharedSource views at `offset_data` / ISO extents.

### D4. Compliance path under the declared-capability contract

Archivey-owned byte-range backends still use SharedSource views. Library-owned
seek-before-read backends (TAR, ISO) use this lock wrapper. ZIP already has `_SharedFile`.

### D5. Instantiate the lock only for `CONCURRENT` readers

**Choice:** The per-reader shared-handle lock and locked member-stream wrapper are
created only when `MemberStreams.CONCURRENT` is declared. The default single-live-stream
path takes no shared-handle lock — one owner cannot race itself on the library handle.
`MemberStreams.SEEKABLE` alone does not instantiate the lock.

### D6. Streaming TAR

Forward-only ownership is unchanged and does not gain concurrent-open support. The same
per-reader lock still protects any streaming TarFile/shared-handle initialization, iterator,
`extractfile`, yielded-stream I/O, EOF verification, and close calls; these acquisitions are
normally uncontended but preserve the "every shared-handle operation" rule.

### D7. Wrapper placement and initialization

The per-reader lock is created before opening the library object. TarFile / PyCdlib archive
initialization, failure cleanup, member creation, and member enter-time initialization occur
under that same lock. The returned library stream is then placed inside the locked
delegating wrapper, and archivey's buffering/error/lifecycle wrapper sits **outside** it.
Thus every buffer refill enters the locked layer and cannot bypass coordination. The wrapper
delegates positioning only when the inner object supports it; otherwise `seek`/`tell` preserve
normal `io.UnsupportedOperation` behavior.

### D8. No callback or diagnostics under the lock

Providers, selectors/filters, progress callbacks, logging, diagnostic formatting/stamping,
`sys.unraisablehook`, and user-visible finalizer hooks run without the backend lock (or any
other Archivey lock). Catch raw exceptions, release the lock, then translate/stamp/log them.
The critical section may contain library-internal decode when inseparable from the atomic
handle call (notably compressed TAR); that is required backend/source work, not a callback.
Likewise:

- release the lock before releasing the member's reader-lifecycle lease;
- do not run password providers, selectors/filters, progress callbacks, logging handlers,
  diagnostic formatting, or arbitrary finalizer hooks under it;
- member close first claims idempotent-close state, closes the inner library stream under the
  backend lock, releases that lock, then performs outer lifecycle/diagnostic work.

`ArchiveStream` must first release its stream-state claim before member creation or inner
close acquires this backend lock. Lifecycle lease release occurs only after the backend lock
is released. This obeys `concurrent-member-streams`' claim/call/publish protocol and avoids
both stream → backend nesting and backend → lifecycle nesting.

### D9. Reader close

The public lifecycle contract defers archive close until all member leases close. When
backend teardown becomes eligible, `_tar.close()` / `_iso.close()` runs under the same
per-reader lock, outside the reader lifecycle lock. New opens have already been disabled by
the lifecycle state, so archive close cannot race a supported member operation.

Concurrent direct reader close with member operations remains unsupported; the base
operation/lifecycle guard rejects it at the public operation boundary.

### D10. Correctness over parallelism; proportionate measurement

One lock means TAR/ISO coordinated library operations serialize. That is intentional: these
libraries can expose a mutable shared image position (especially for caller-supplied streams),
and preserving sparse/extents is more valuable than speculative parallel I/O.

Record a practical baseline for plain TAR, compressed TAR, and ISO using
interleaved/threaded workloads: wall time and lock wait/hold time, plus seek count and bytes
decompressed/read where instrumentation is practical. There is no speed threshold and this is
not a correctness merge gate. A later independent-handle, raw-extent, or native-reader change
uses targeted before/after measurements (including peak memory only if its buffering strategy
can change it) to support its design and any throughput claim.

## Risks / Trade-offs

- **[Risk] Incomplete coverage** → explicitly audit TAR `getmembers()`/EOF reads and pinned
  pycdlib `walk()`/`get_record()` in addition to open/member I/O/close; regression-probe
  library updates and test handle operations under forced interleavings.
- **[Risk] Buffering bypasses the lock** → place archivey buffers outside the locked layer.
- **[Risk] Catalog I/O vs data** → materialize-before-fan-out; the public contract forbids
  catalog mutation concurrently with member reads.
- **[Risk] Callback deadlock** → translate/log/release leases only after the backend lock is
  released; callback probes lock this behavior in.
- **[Risk] False sense of full reader thread safety** → this lock is one backend mechanism;
  the cross-format spec limits concurrent reader methods.
- **[Trade-off]** Depend on libraries continuing seek-before-read (same class of dependency
  ZIP has on `_SharedFile`).
- **[Trade-off]** Shared-handle operations are serialized; compare targeted measurements
  before making an optimization claim.

## Migration Plan

1. Add comprehensive locked-stream helper and unit tests.
2. Wire member-open initialization, member operations, and archive close for TAR-RA/ISO.
3. Apply format deltas and update parallel-reader audit/lock-order docs.
4. Land cross-format tests with `concurrent-member-streams`.
5. Record the proportionate serialization baseline; use targeted comparisons for later
   optimization claims.

## Open Questions

_(none; implementation audit may add library methods to the lock-coverage list but may not
remove the required operations.)_
