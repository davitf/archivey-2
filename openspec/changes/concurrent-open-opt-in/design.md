## Context

Three facts collide:

1. **Concurrency is only dangerous on solid / expensive-seek archives.** For DIRECT + cheap
   -seek backends (ZIP, plain TAR, single-file, directory, ISO, distinct 7z folders, xz/lzip
   or accelerated gzip/bz2 TAR), holding several member streams open and interleaving them is
   cheap and correct. On a single solid block it costs a re-decompress per rewind.
2. **The danger is invisible on a typical test corpus.** A developer testing with ZIP / plain
   TAR sees concurrent open "just work"; the same code silently degrades to O(n²) on a
   production `.tar.gz` or solid 7z. `VISION.md`: an implementation that "re-reads a solid
   block fails the benchmark even if a small test corpus hides it," and a warning is "a
   surprise deferred, not avoided."
3. **The library's contract is format-uniform.** Access-mode enforcement is "uniform … so
   behaviour is deterministic across formats" (`access-mode-and-cost`, `SPEC.md`). Making the
   *legality* of concurrent open depend on the archive's cost would itself be the
   format-dependent surprise the library exists to avoid.

A cost-derived gate (allow on DIRECT, deny on SOLID) was considered and **rejected**: it fires
in production on `.tar.gz` but not in development on ZIP — the exact deferred, format-varying
surprise (2)+(3) forbid. The uniform choice is: multiple simultaneously-open streams are an
**opt-in** capability, and the default gate fires on **every** format so it is hit in
development regardless of the test corpus.

## Goals / Non-Goals

**Goals**
- Multiple simultaneously-open member streams require an explicit opt-in, enforced
  **uniformly across all formats** (including the ones where it would be cheap).
- The default failure is **fast, deterministic, and at the cause site** (the second open),
  not deferred to a later read or to production.
- The ordinary sequential `open → read → close` loop is unaffected on every format.
- Once opted in, interleaving is correct everywhere the byte-range machinery applies; the cost
  receipt tells the caller whether it is cheap or expensive.

**Non-Goals**
- Making concurrency *cheap* on solid archives — it cannot be, and that is what the cost
  receipt and the flag's docs are for.
- Making `ArchiveReader.close()` / forward-pass iteration safe to call from multiple threads
  concurrently with each other (owner-thread lifecycle). Member `open`/`read` under the
  opt-in *is* in scope (see D8).

## Decisions

### D1. Opt-in is a format-uniform gate, not cost-derived

`open_archive(..., allow_multiple_open_streams=False)` by default. Opening a second member
stream whose lifetime overlaps a still-live one raises `ConcurrentAccessError` for **every**
format — ZIP and plain TAR included, even though it would be cheap there. Uniformity is the
point: the developer hits the gate in development on their ZIP fixtures, reads the documented
danger, and sets the flag knowingly, so production `.tar.gz` behaves identically to the tested
ZIP. `AccessCost.SOLID` / `solid_block_count` stay **informational** — they tell an opted-in
caller whether interleaving is cheap or a re-decompression storm; they never gate legality.

### D2. Gate on overlapping stream lifetimes

"Live" spans `open()` → stream `close()` (or context-manager exit). The gate counts
*simultaneously-live* streams, so:

- `open(a); …; close; open(b)` — sequential, **allowed** (one live at a time).
- `s1 = open(a); s2 = open(b)` while `s1` is still open — **raises** (two live).

This is the deterministic, format-independent trigger. The alternatives are worse:

- *Gate on a read that rewinds* — format-dependent again (ZIP never rewinds-expensive), and
  fires far from the `open()` that caused it.
- *Gate on cumulative `open()` count* — would break the normal extract loop.

### D3. Liveness ends at close(), not EOF and not GC

- **Not EOF:** member streams are seekable views (`SlicingStream`); a caller may read to end
  then `seek(0)` and re-read. A fully-read stream is not necessarily done, so EOF cannot end
  liveness.
- **Not GC:** relying on finalizer timing would make the gate non-deterministic (the same code
  raises or not depending on when the collector runs). Liveness ends only at explicit
  `close()` / `with`-exit. A caller who drops a stream without closing it keeps it "live" until
  close; the error message says so and points at `with` / `close()`.

Implementation: `open()` registers the handle it returns and installs a close hook that
deregisters it; the reader keeps a small set/counter of live handles. This is reader-level
lifecycle bookkeeping (which streams are open) — **not** per-open scratch that a second open
could corrupt — so it does not violate the `_open_member` reentrancy invariant.

### D4. Raise, do not auto-close (the resolved design question)

When the gate fires, the second `open()` **raises** and leaves the first stream untouched and
readable. The tempting alternative — mirror the streaming forward-pass behaviour and
**auto-close** the previous stream when a new one opens — is rejected:

- **It is a silent surprise, deferred and displaced.** The failure would not appear at the
  second `open()`; it would surface later as a "read on closed file" on the *first* stream,
  with no hint that a *second* `open()` closed it — precisely the hard-to-debug, action-at-a
  -distance failure "no surprises" forbids.
- **It is a silent guess.** The library cannot know the caller is done with the first stream
  (they may still hold it, or have passed it to another function). Closing it on that
  assumption is exactly the "no silent guesses" anti-pattern; a still-referenced handle should
  never be invalidated behind the caller's back.
- **The streaming analogy does not carry.** In `streaming=True` the previous stream becoming
  invalid on advance is the *documented consequence of the single forward-pass contract*, at a
  point the caller explicitly triggers (asking for the next item). Random-access mode's mental
  model is the opposite — "streams I open are independent handles I control" — so silently
  closing one because another opened would break that model, and would be inconsistent between
  modes.
- **The convenience it buys is one line.** The "I forgot to close and it wasn't GC'd yet" case
  is solved idiomatically by `with reader.open(m) as s:` or an explicit `close()`; the error
  message names both. Turning a forgotten-close into a teaching error is the fail-fast win, not
  a cost.

So: default = raise on the second overlap; opt-in = allow many; **never** auto-close.

### D5. Opt-in behaviour and the correctness contract

With `allow_multiple_open_streams=True`, interleaved member streams MUST stay correct:

- **Archivey-owned byte ranges** (7z/RAR/single-file): each open via a `SharedSource` view
  with per-view position and locked seek+read.
- **Library-owned seek-before-read** (TAR-RA, ISO): keep `extractfile` / pycdlib; wrap each
  member stream so data-path reads hold a per-archive lock for the library's seek+read
  (`tar-concurrent-open`). Avoids reimplementing sparse / extents.
- **ZIP:** stdlib `_SharedFile` already locks; no archivey wrap.

Solid formats still give each open its own logical stream (re-decode / re-seek as already
permitted). The flag changes *whether* you may hold several open at once, not the cost model.

### D6. Error type

A dedicated `ConcurrentAccessError` (an `ArchiveyError`) rather than reusing
`UnsupportedOperationError`: the latter means "not valid in this access mode" (streaming);
this is "valid, but you must opt in," and a distinct type keeps the fix obvious in a traceback.
Its message states that a member stream is already open, that the caller should close it (use
`with`) or pass `allow_multiple_open_streams=True`, and points at the solid-cost note.

### D7. Streaming mode: reject the flag at open; keep `stream_members` auto-release

`streaming=True` is a single forward pass. `allow_multiple_open_streams=True` with
`streaming=True` SHALL raise at `open_archive()` (invalid combination).

Do **not** change `stream_members()` to raise-on-overlap like random-access `open()`.
`stream_members` deliberately invalidates the previous stream on advance so
`for member, stream in reader.stream_members():` stays safe when `stream` is ignored.
Raise-on-overlap there would couple the loop to GC timing. Document that rationale next to
the streaming carve-out; the two APIs have different mental models.

### D8. Opted-in thread-safe member open/read (worker-pool seam)

The blanket "reader is not thread-safe / undefined" stance is too weak for the use case
"hand one reader to N workers after listing members." With the pieces this change and
`tar-concurrent-open` already need, the hard part is small:

| Piece | Already needed? | For threads |
|---|---|---|
| Live-stream gate bookkeeping | Yes (opt-in gate) | Hold a lock around register / check / deregister |
| Member-stream I/O | Yes (SharedSource / lock wrap) | Already locked per read |
| Member cache first build | Materialize-before-fan-out | Init-under-lock when opt-in (cheap) |
| `_open_member` | Reentrancy invariant | No unprotected per-open scratch |
| `ArchiveReader.close()` / `__iter__` | — | Still owner-thread only |

**Contract:** when `allow_multiple_open_streams=True` and the member list is materialized,
concurrent `open()` + member-stream `read`/`close` from multiple threads are supported.
Concurrent reader `close()` / iteration / streaming remain unsupported.

That is enough for a worker pool; full "every method is thread-safe" is not required.

### D9. Reentrancy = no *unsafe* mutations (not "no mutations")

The `_open_member` invariant exists to ban per-open scratch on `self` (e.g. a single
`_pending_stream` replaced by the next open). Synchronized mutations (live-stream set under
a lock) are fine and required for D8. Spec wording: no unprotected mutations of open-critical
shared state; synchronized bookkeeping allowed.

## Risks / Trade-offs

- **[Trade-off] Friction on cheap concurrent use** (e.g. diffing two ZIP members) now needs the
  flag. Accepted: it is a one-time, documented opt-in, and the uniformity it buys is the whole
  point. Fails fast in development, never in production.
- **[Risk] Bookkeeping correctness** — miscounting live streams (e.g. a close hook that does not
  fire) could spuriously raise or spuriously allow. **Mitigation:** register on `open()`,
  deregister in the handle's `close()` (idempotent), lock the bookkeeping, and test
  open/close/re-open, context-manager, and multi-thread open paths.
- **[Trade-off] Narrows the #51 guarantee.** Concurrent open changes from always-on to opt-in.
  This is a deliberate contract change; it is called out in the proposal for the maintainer.
- **[Note] Internal consumers** (e.g. a future `ExtractionCoordinator` fan-out) opt in
  explicitly / use the internal path; the public default stays safe.
- **[Trade-off] Thread-safe open/read, not full reader thread-safety.** Workers can open
  members; they must not call `reader.close()` or iterate the forward pass concurrently.
  Documented; cheaper than locking every reader method.

## Migration Plan

1. Add `ConcurrentAccessError`; add `allow_multiple_open_streams` to `open_archive()` (default
   `False`), carried on the reader; reject `streaming=True` + flag at open.
2. Track live member streams in `BaseArchiveReader.open()` under a lock; raise on the second
   overlap unless opted in; deregister on stream close.
3. When opt-in: member-cache init-under-lock (or equivalent); document thread-safe open/read
   after materialize.
4. Update specs (`archive-reading`, `access-mode-and-cost`) and the ABC docstrings; document
   `stream_members` vs `open()` rationale.
5. Tests: uniform raise (ZIP/plain-TAR/solid), sequential loop allowed, opt-in interleave,
   first stream survives the raise, `with`/close paths, invalid streaming+flag combo,
   two-thread opted-in open after `members()`.
6. `tar-concurrent-open` supplies the TAR/ISO lock-wrapper mechanism under the opt-in.

## Open Questions

- Flag name: `allow_multiple_open_streams` vs `allow_interleaved_streams` vs an
  `AccessMode`-style enum. Leaning toward the boolean to avoid re-expanding the deliberately
  flattened mode axis; final naming is the maintainer's call.
- Whether `ConcurrentAccessError` subclasses the member/usage error base or sits beside
  `UnsupportedOperationError` — resolve against the `error-handling` hierarchy at implementation.
- Exact exception type for the invalid `streaming=True` + flag combination (`ValueError` vs
  archivey typed error) — resolve at implementation against `error-handling`.
