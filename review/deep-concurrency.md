# Deep pass 1 — Concurrency correctness

Second-pass review on branch `claude/deep-review-concurrency-structure-ldkxms`, HEAD `7786fda`
(post-PR-#73, so C1/C2/C3 fixes are in the tree — confirmed: `base_reader.py:519-528` catches
`BaseException`, `directory_reader.py:63` sets `_MEMBER_LIST_UPFRONT = False`).

Method: every mechanism gets (a) the guarantee it is meant to provide, (b) either a proof
naming the ordering/atomicity/exclusion property and where it is enforced, or (c) a concrete
violating execution. "Appears correct" is not used. Findings **N1–N7** are new; corrections to
`review/concurrency.md` and `review/00-recon-map.md` are called out inline and collected at
the end.

## Corrected invariants and shared-state map

The first review's invariants I1–I6 need three corrections:

- **I1/I2 (single root pass / single live stream) are not enforced during library-internal
  open windows.** The exemption that admits extract_all's own internal opens is a
  reader-wide *depth counter*, not a thread- or token-scoped grant, so it admits **any**
  thread's `open()` during those windows (finding N3). I1/I2 as stated hold only outside
  `extract_all()` and outside first-touch link resolution.
- **I3 (snapshot "published exactly once … immutable after publication")** is right about
  once-ness but wrong about atomicity: the snapshot is two separate attribute stores with an
  unlocked fast path keyed on only the first (finding N2). Also, the code comment's claim of
  "tuple + frozen name map copy" (`base_reader.py:515`) is inaccurate — the *mutable* list
  and dict are published directly; immutability is by convention (callers get copies via
  `list(...)`, but internal readers share the mutable objects).
- **I5 (streaming reader is single-owner)** holds, but not by anything in `ReaderState` —
  it holds because `core.py:167-172` rejects `streaming=True` + `CONCURRENT` at open. The
  enforcement lives two modules away from the state it protects; a future entry point that
  skips `open_archive` (a backend constructed directly, as tests do) gets no protection.

Shared-state additions the recon map missed:

- `SevenZipKeyCache._cache` (`crypto.py:240-253`) — unlocked dict, written from worker
  threads under CONCURRENT.
- `ArchiveMember._member_id` / `_archive_id` are stamped from **two** unsynchronized paths
  when `_MEMBER_LIST_UPFRONT` is set: `_get_members_registered` (under the materialization
  election) and `get_members_if_available()` → `_get_members_index_only`
  (`base_reader.py:905-910` — **no token, no election**). For 7z/single-file, which yield
  cached member objects, both paths mutate the *same* objects concurrently. Benign because
  both write identical positional values (see the CPython-reliance section), but it is a
  data race in the ISO-C sense on free-threaded builds, and it double-runs
  `_warn_for_bidirectional_controls`.
- The finalizer capture in `_register_public_stream` (`base_reader.py:272-282`): the
  finalizer closure captures `self._on_close` **at attach time** (`archive_stream.py:111`),
  so correctness depends on the assignment at `base_reader.py:280` preceding
  `_attach_finalizer()` at `:281`. Currently ordered correctly; nothing enforces it.

## Mechanism-by-mechanism verdicts

### M1. Operation tokens (`ReaderState.acquire_pass/acquire_worker/…`)

**Guarantee (from docstrings `reader_state.py:41-50,154-160`):** without CONCURRENT, a
reader-wide pass excludes other passes and workers, and workers exclude passes; overlap is
rejected loudly (`ArchiveyUsageError`/`ConcurrentAccessError`), never served silently.

**Verdict: does NOT hold — the internal-opens exemption is a hole (N3, VERIFIED, medium-high).**

`begin_internal_opens()` (`reader_state.py:93-96`) increments two plain reader-wide counters.
While `extract_all()` holds them (`base_reader.py:1108-1110`) — or while first-touch
materialization resolves link targets (`base_reader.py:504-513`) — the admission rules change
for **every thread**:

- `acquire_worker` (`reader_state.py:170-173`): root active + `_internal_open_depth > 0` →
  the foreign thread's `open()` is admitted as a *child of extract_all's root* instead of
  raising.
- `acquire_live_stream` (`reader_state.py:236-239`): `_gate_exempt_depth > 0` → the
  single-live-stream gate is bypassed, so the foreign stream is registered as live.

Concrete execution, plain GIL build, **no CONCURRENT declared**:

1. Thread A: `reader.extract_all(dest)` on a random-access TAR (no `_handle_lock`:
   `tar_reader.py:170-175` takes a lock only under CONCURRENT or streaming).
2. Thread B (a caller bug the gate exists to catch): `reader.open("x").read()`.
3. B is admitted (child token + gate exemption). Both A's coordinator stream and B's stream
   are tarfile `ExFileObject`s over the **same** `fileobj`. `tarfile._FileInFile.read` is a
   separate `seek()` then `read()`; the GIL can switch threads between them. A seeks to
   offset₁ → switch → B seeks to offset₂, reads → switch → A reads **at offset₂**.
4. Observable consequence: **wrong member bytes written into A's extraction output** (or
   returned from B), silently — the exact failure the gate promises to convert into a loud
   `ConcurrentAccessError`. ISO under the same conditions is equally exposed (pycdlib has no
   internal fp lock); ZIP survives *by accident* because stdlib `_SharedFile` takes
   `ZipFile._lock` per read.

The exemption is meant for the coordinator's *own* opens (hardlink recovery through public
`open()`, link-target reads), and those all run on the owning thread. Scoping the exemption
to the thread that called `begin_internal_opens()` (store `threading.get_ident()`; exempt
only matching threads) closes the hole without changing any supported behavior. I did not
apply this (maintainer rule: concurrency mechanisms get a decision first).

**Where the guarantee otherwise holds:** every admission decision and token release runs
under the single `self._lock`; release is idempotent via `token._released`
(`reader_state.py:125-135`); a root release clears stale children. The check-then-act shape
of `open()` (token acquired, released, *then* backend work) is safe because the token is a
reservation recorded in state, not a held lock — overlap detection only needs the
membership sets, which are lock-protected.

### M2. Materialization election (`begin/complete/fail_materialization`)

**Guarantee:** first-touch member-list construction runs at most once at a time; under
CONCURRENT, overlapping callers block and then observe the fully published snapshot; a
failed attempt re-opens the election. (`reader_state.py:190-230`.)

**Verdict: the election itself holds; the *publication* does not (N2, VERIFIED, medium).**

The election is a correct monitor: the wait loop `begin_materialization` re-checks its
predicate after every wake (`reader_state.py:205-219`), `complete/fail` notify under the
same lock, and the C1 fix (`base_reader.py:519-528`) resets the election on `BaseException`.
Waiters that return `False` from `begin_materialization` are ordered after
`complete_materialization` by the lock, which is after both publication stores — that path
is sound.

The unlocked **fast path** is not. `_get_members_registered` publishes two fields in
sequence (`base_reader.py:516-517`):

```python
self._members_cache = members            # (1)
self._members_by_name_lists = by_name_lists  # (2)
```

and the fast path (`base_reader.py:481-482`) returns as soon as (1) is visible, **without
the lock**. Two consumers then immediately dereference the *second* field:

- `get()` — `base_reader.py:933`: `assert self._members_by_name_lists is not None`
- `open(name)` — `base_reader.py:960-961`: same assert.

Concrete execution (CONCURRENT reader, plain GIL build — no free-threading needed):
thread W (owner, holding a *worker* token per `members()` at `base_reader.py:864-869`)
executes store (1); the GIL switches before (2); thread B calls `reader.get("x")`, acquires
its own worker token (workers don't exclude workers), hits the fast path, returns, and the
assert fires → **`AssertionError` from a public API** (with `python -O`: `AttributeError:
'NoneType' object has no attribute 'get'` inside `_last_by_exact_name`). Both are
untranslated internal errors leaking from a correct program.

Fix is one line: swap (1) and (2) so the sentinel everything keys on is stored last (plus a
comment making the ordering load-bearing). On free-threaded builds the fast path then
additionally relies on attribute store/load atomicity with acquire/release ordering — see
the CPython-reliance section.

**Re-entrancy: the election can self-deadlock through a diagnostic callback (N4a, VERIFIED,
low-medium).** Materialization emits diagnostics with no lock held (correct), but a user
`on_diagnostic` callback that calls back into the same reader deadlocks *its own thread* on
a CONCURRENT reader:

1. Owner thread, inside `_iter_members()` during first-touch, emits (say)
   `MEMBER_TIMESTAMP_INVALID`; the collector delivers the callback outside its lock
   (`diagnostics_collector.py:224-234`).
2. Callback calls `reader.members()` → worker token admitted (no root) →
   `_get_members_registered` → cache still `None` → `begin_materialization` → state is
   MATERIALIZING → CONCURRENT branch → `self._materialization_cv.wait()`
   (`reader_state.py:215`) — waiting for a notify that only *this same thread* can issue.
   Permanent single-thread hang; no second thread required.

The collector's `_emitting_threads` guard (`diagnostics_collector.py:184-189`) is aimed at
exactly this class but only trips when the re-entered operation *itself emits*; a
non-emitting `members()` sails past it. Non-CONCURRENT readers get the (misleading but loud)
"another materialization is already in progress" error instead. Cheap fix: record the
electing thread id in `ReaderState`; `begin_materialization` raises `ArchiveyUsageError` when
the waiter *is* the owner. Same pattern closes the sibling deadlock N4b below.

### M3. Progressive pass (`_ProgressivePassIterator`, `_begin_forward_pass`)

**Guarantee:** one instance-held forward pass; early exit survivable so `scan_members()` can
finish it; on completion the resolved list is published. Single-owner (caller-synchronized
per stream; cross-thread overlap excluded by pass tokens).

**Verdict: does NOT hold under exception unwind — a failed pass converts to silent partial
success (N1, VERIFIED with repro, medium-high).**

`_ProgressivePassIterator.__next__` (`base_reader.py:1177-1189`) treats `StopIteration` from
`self._members_source` as clean EOF and publishes the cache via `_finalize_pass_links()`.
But a generator that *raised* is thereafter **closed**, and PEP-479 semantics make every
subsequent `next()` on it raise `StopIteration`. So:

1. Streaming pass raises mid-scan (any error: codec `CorruptionError`, `TruncatedError`,
   a RAISE-disposition diagnostic). The exception propagates to the caller — correct so far.
2. Caller catches it and calls `scan_members()` (the method the error messages themselves
   recommend: "Call scan_members() for the resolved member list").
3. `scan_members` drains the same iterator (`base_reader.py:887-891`); the first `next()`
   hits the closed generator → `StopIteration` → `_exhausted = True` →
   `_finalize_pass_links()` **publishes the partial `_pass_scanned` as the complete,
   resolved `_members_cache`** — and returns it with no error.

Reproduced deterministically (TAR, `MEMBER_TIMESTAMP_INVALID` set to RAISE on the third of
six members):

```
iterated: ['a.bin', 'b.bin'] | error: DiagnosticRaisedError
scan_members: ['a.bin', 'b.bin'] -- NO ERROR (published as resolved cache)
get_members_if_available: ['a.bin', 'b.bin']
```

The same happens for a corrupt `.tar.gz` whose decode error surfaces mid-walk. Observable
consequence: after an error the reader **lies** — `scan_members()`/`get_members_if_available()`
present a truncated listing as complete and resolved, and a subsequent `extract_all` on a
non-streaming reader materialized the same way would extract the subset "successfully". For
the founding dedupe/inventory use case, a silent partial listing is the worst failure shape.
Fix direction: record the escaped exception on the iterator (wrap `next(self._members_source)`
in `except BaseException: self._broken = exc; raise`) and have subsequent `__next__` re-raise
a `ReadError` ("pass previously failed") instead of finalizing; `fail`-not-`finalize` on any
non-StopIteration unwind.

(Related but distinct, filed in deep-unknown-unknowns.md: `tarfile` itself treats a corrupt
*non-first* header as clean end-of-archive, so some corruption never raises at all — the
listing just ends early with only the EOF-marker diagnostic as a WARNING.)

**Where the mechanism otherwise holds:** the instance-held iterator surviving `break` is
correct (the `__iter__` generator's `finally` releases the pass token on early exit via
generator close; the *iterator* keeps its position); token release on abandonment without
close relies on refcounting GC — see CPython-reliance R4.

### M4. Live-stream gate + lifecycle leases + teardown

**Guarantee:** without CONCURRENT at most one live public stream (modulo N3); reader starts
with one lease, each live stream holds one; `_close_archive` runs exactly once, only after
READER_CLOSED and the last lease drop; double close and stream-close/finalizer races are
idempotent.

**Verdict: holds (with one BaseException caveat).** Argument:

- Lease accounting is single-writer-per-transition: every mutation of `_lease_count`,
  `_live_streams`, `_teardown_claimed`, `lifecycle` happens inside `ReaderState._lock`
  (`reader_state.py:252-337`). The teardown trigger (`_release_lease_locked`) computes
  "should run teardown" *in the same lock hold* as the decrement, so exactly one releaser
  observes the 0-and-closed-and-unclaimed conjunction.
- `claim_teardown` re-checks all three conditions and flips `_teardown_claimed` atomically
  (`reader_state.py:303-316`), so even if two callers somehow both got `True` (they cannot,
  per the previous point) only one runs `_close_archive`.
- Double release of one stream is idempotent through set membership: the second
  `release_live_stream` finds `id(stream)` absent and returns `False`
  (`reader_state.py:255-258`). This is what makes the explicit-close vs GC-finalizer race
  benign: both funnel into the same idempotent release. The finalizer never runs while a
  strong reference exists, so it cannot race a *concurrent* `close()` on the same object;
  and after `close()`, `_detach_finalizer` disarms it (`archive_stream.py:348`).
- Caveat (accepted): `_maybe_teardown` catches `Exception`, not `BaseException`
  (`base_reader.py:312`). A `KeyboardInterrupt` inside `_close_archive` leaves lifecycle at
  TEARDOWN_RUNNING with `_teardown_claimed=True`; teardown is never retried (by design) and
  never marked complete, so backend handles leak silently after a Ctrl-C-during-teardown.
  For KI that is a reasonable trade; noting so it is a choice, not an oversight.
- Dead mechanism: `take_teardown_error` (`reader_state.py:324-328`) has **zero callers**
  (grepped src + tests). Either wire it up (e.g. surface a deferred-teardown failure on the
  next public call) or delete it; a stored-but-never-read error is a silent-swallow path.

### M5. Draining close (`mark_reader_closed`)

**Guarantee:** under CONCURRENT, `close()` blocks new admissions, waits for in-flight
workers, and transitions once; concurrent double-close is idempotent; an interrupted closer
doesn't wedge others.

**Verdict: holds, with the same re-entrancy exception as M2 (N4b, VERIFIED, low).**
The monitor is correct: `_closing` is set under the lock before waiting; admissions check it
(`reader_state.py:346-351`); the drain loop's predicate is re-checked per wake; the
`except BaseException` arm resets `_closing` and re-notifies (`reader_state.py:298-301`) so
an interrupted closer leaves the door open for others. Waiting closers use a proper
predicate loop on `_close_cv` (`reader_state.py:277-280`).

The exception: `close()` called *from the same thread that holds a worker token* — the
realistic route is again a diagnostic/progress callback fired during a worker operation —
finds `_workers` non-empty and waits on `_workers_cv` for its own token: single-thread
deadlock on a CONCURRENT reader (`reader_state.py:293-294`). Non-CONCURRENT raises instead
(`:286-290`). Same owner-thread-check fix as N4a.

### M6. Backend shared-handle locks

**Guarantee:** all positioned operations on one shared OS-level handle are mutually
exclusive, so seek→read pairs are atomic per consumer.

**Verdicts:**

- **TAR `LockedStream` — holds under CONCURRENT/streaming.** Every public op holds the lock
  across the whole call (`locked.py:32-68`); crucially tarfile's internal
  `_FileInFile.read` (seek+read) executes entirely *inside* `LockedStream.read`'s lock
  hold, so the pair is atomic. Open/EOF-check/close/progressive-`next()` all take the same
  lock (`tar_reader.py:178,270,296,324,361,501,551`). Holds **only when the lock exists** —
  the default random-access reader has none and depends on single-owner usage, which N3
  punctures.
- **ZIP `CloseLockedStream` — holds, but by pinned-stdlib audit, not by construction.**
  Reads are deliberately not serialized by archivey; data integrity rests on
  `zipfile._SharedFile.read` taking `ZipFile._lock` around its seek+read (verified in
  CPython 3.11–3.13 sources), and the archivey lock covers only open/close where
  `_fileRefCnt` is unlocked. The ZipCrypto side-channel reads (`_ciphertext_body_stream`,
  `_read_zipcrypto_header`, `zip_reader.py:526-579`) hold `ZipFile._lock` across their whole
  save/seek/read/restore window, which is the *same* lock `_SharedFile` uses — so they
  mutually exclude member reads correctly. This is reliance R2 below.
- **ISO — holds under CONCURRENT modulo the pinned-pycdlib audit.** `open_file_from_iso` +
  `_PyCdlibStream` construction under the lock, all subsequent ops via `LockedStream`
  (`iso_reader.py:477-484`); `walk()`/`get_record()` are audited as not touching `_cdfp`
  (`iso_reader.py:346-350`). Reliance R3.
- **`SharedSource`/`SlicingStream` re-seek views — holds.** The seek+read pair runs under
  `_io_guard` (`slice.py:124-134`); `seek()` only moves the private `_pos`; construction
  never moves the shared handle in re-seek mode (`slice.py:93-97`); the SEEK_END probe is
  also guarded (`slice.py:156-158`). One near-miss: `SlicingStream.size`
  (`slice.py:193-208`) calls `source_byte_size(self._stream)`, which for whitelisted types
  does an **unguarded** tell/SEEK_END/restore on the shared handle
  (`binaryio.py:210-217`) — that would corrupt a concurrent locked read. It is unreachable
  today only because `SharedSource.view()` resolves `length` to a concrete int whenever the
  source size is knowable (`shared.py:108-117`), and when it is *not* knowable
  `source_byte_size` returns `None` for the same object without seeking. Correct by
  coincidence of the two call sites sharing one function; nothing local to
  `SlicingStream.size` prevents a future caller from creating an open-ended locked view.
  Cheap hardening: take `_io_guard` around the probe, or forbid `length=None` when a lock is
  supplied.

### M7. `ArchiveStream` one-shot open + close

**Guarantee:** `open_fn` runs at most once, never under the stream lock; open-vs-close race
never leaks an inner stream; close is idempotent and translates inner-close errors.

**Verdict: holds.** The claim is an ownership transfer under `_open_lock`
(`archive_stream.py:190-204`): exactly one caller swaps `_open_fn` to `None`; losers get a
typed error rather than blocking (documented caller-synchronization contract). `open_fn`
runs lock-free (so a backend handle lock never nests under stream state), and the publish
step re-checks `closed` and closes the freshly opened inner if a close won the race
(`archive_stream.py:211-219`). Concurrent `close()`+`close()` on one stream can both enter
the body (the `closed` check at `:328` is unlocked), can both invoke `_on_close`, and can
double-release the lease at the *stream* layer — but M4's set-membership idempotence absorbs
it, and same-stream concurrency is explicitly the caller's to synchronize. No violation of
the reader's invariants is reachable through this window.

### M8. `DiagnosticCollector`

**Guarantee:** counters/retention consistent under concurrent emits; delivery (log +
callback) outside the lock; same-thread reentrancy through emit detected loudly.

**Verdict: holds for what it claims; the claim is narrower than the docstring implies.**
All counter/retention mutations are under the RLock; delivery happens after the `with`
block exits (`diagnostics_collector.py:184-238`); the per-thread reentrancy set correctly
distinguishes parallel emits from same-thread re-entry, and the `finally` clears the id even
when the callback raises. But the error message ("cannot drive another operation on the same
reader/stream while a diagnostic is being emitted") promises more than is enforced — only
*emitting* re-entry is caught; non-emitting re-entry reaches the reader and can hit N4a/N4b.
`_attach_diagnostic`'s tuple read-modify-write (`:242-245`) is exclusive because each member
belongs to one reader with one collector, and the replacement is a single reference store —
readers of `member.diagnostics` see the old or new tuple, never a torn one.

### M9. `_PasswordCandidates` / `SevenZipKeyCache` / `_folder_passwords`

**Guarantee:** provider invoked with no archivey lock held; same-reader provider re-entry
raises; concurrent first-touch may duplicate attempts but converges; known-good promotion
is synchronized.

**Verdict: holds as documented.** Promotion and snapshots take `_state_lock`
(`password.py:139-153`); the provider depth guard increments/decrements under
`_provider_lock` with the callback outside both (`password.py:117-137`); `_provider` is
immutable after construction so its unlocked reads in `attempt` are safe. Duplicated
expensive work is explicitly documented as the accepted cost (`password.py:39-44`), and 7z's
`_folder_passwords` check-then-act (`sevenzip_reader.py:794-825`) can at worst run the full
folder-confirm decode once per racing thread and call the provider more than once —
convergent because all writers store the same confirmed value. The dict writes themselves
are reliance R1 (per-object dict atomicity), not corruption under either build. The one
sharp edge: `iter_candidates` in ZIP's stored-password path plus a *stateful* provider can
interleave with another thread's attempt so the provider is asked with non-monotonic
`attempt` numbers — cosmetic, the API makes no ordering promise across threads.

### M10. Single-file reader's `_pending_stream` (non-seekable one-shot)

**Guarantee:** the single forward pass is handed out exactly once; a second open fails
loudly (`single_file_reader.py:279-290`).

**Verdict: holds, but only via a distant invariant.** The `if/read/None` sequence is a
textbook check-then-act; two concurrent `open()` calls would both receive the *same* stream.
It is unreachable because a non-seekable source forces `streaming=True`
(`core.py:219-227`), and `streaming=True` + CONCURRENT is rejected (`core.py:167-172`), so
`_open_member` on a non-seekable source can never be entered from two threads through the
public API. The proof spans three files; a one-line comment at the check-then-act site (or
an assert `self._handle_lock is None`-style guard) would keep a future
non-seekable-random-access change from silently landing on the race.

### M11. Process-global installs (pycdlib cycle guard, codec module sentinels)

**Verdict: holds.** Both are performed at module import under the import lock; the deque
subclass keeps its visited-set per *instance*, so concurrent ISO walks don't share state
(`iso_reader.py:115-178`); codec sentinels (`codecs.py:95-107`) are written once at import
and only read thereafter.

## Lock ordering — correction to the first review

`review/concurrency.md` ("Lock-ordering / deadlock check") claims ReaderState's lock and a
backend handle lock "can be held simultaneously … ReaderState-outer, handle-inner". **That is
wrong, and the truth is stronger:** no code path holds `ReaderState._lock` while acquiring
any other lock. Every `ReaderState` method takes and releases the lock internally (the CV
waits release it), and token "holding" is a state entry, not a held lock — by the time
`_open_member` takes a handle lock, the state lock is long released.

The actual nesting pairs in the tree, each one-directional and therefore acyclic:

| Outer | Inner | Where |
|---|---|---|
| backend `_handle_lock` | `ZipFile._lock` | `_zip_open_raw` → `zipfile.open` → `_SharedFile` (`zip_reader.py:635-640`) |
| backend `_handle_lock` | (tarfile/pycdlib internals, lock-free) | `tar_reader.py:501`, `iso_reader.py:477` |
| `SharedSource._lock` | — (leaf; raw file/BytesIO/ConcatenatedFile ops) | `slice.py:124` |
| `DiagnosticCollector._lock` | — (leaf; delivery runs outside it) | `diagnostics_collector.py:224` |
| `_provider_lock` / `_state_lock` | — (leaves; callback runs outside) | `password.py:121-134` |
| `ArchiveStream._open_lock` | — (leaf; `open_fn` runs outside) | `archive_stream.py:190-205` |

TAR's `_open_member` additionally keeps `emit()` outside the handle lock on purpose
(`tar_reader.py:495-513`), so collector-lock-under-handle-lock doesn't occur either. With no
lock ever taken while another archivey lock is held, deadlock requires a *condition wait*
whose fulfiller is blocked — which is exactly the N4 self-deadlocks, the only deadlocks
found.

## Interpreter shutdown

- `_AcceleratorStream`'s `weakref.finalize` guard is the load-bearing one and is built
  correctly (staticmethod callback, strong ref to the raw inner only) — it runs via
  finalize's atexit hook before module teardown, closing rapidgzip's C++ threads. Holds.
- **N5 (SUSPECTED, exotic): the `ArchiveStream` lease finalizer can hang shutdown.** Its
  callback calls `release_live_stream` → `ReaderState._lock`
  (`archive_stream.py:113-117` → `reader_state.py:253`). At interpreter shutdown, daemon
  threads are frozen (3.9–3.11: hang on GIL; 3.12+: exit at next GIL acquire) — a daemon
  thread parked *inside* a `ReaderState` critical section never releases the lock, and the
  atexit-driven finalizer then blocks forever → the process hangs instead of exiting. The
  window is a handful of bytecodes wide and requires daemon threads driving a reader at
  exit; standard for lock-taking finalizers, but worth a line in the concurrency docs since
  the library otherwise makes strong shutdown claims (the rapidgzip SIGABRT work).
- The finalizer's `sys.unraisablehook` usage is itself guarded against raising. Holds.

## Where correctness relies on CPython implementation details (inventory)

The task asked for *every* such place. These are the ones that survive scrutiny as real
load-bearing reliances (each is fine today; the point is they are guarantees the code
assumes rather than enforces):

- **R1 — per-object dict/attribute atomicity for benign-race caches.** `SevenZipKeyCache._cache`,
  `_folder_passwords`, and the double-stamping of `_member_id` are unlocked concurrent
  writes that are safe under the GIL and, on 3.13t, safe only because PEP 703 gives
  individual dict/attribute operations per-object locking. Any port (PyPy without such
  guarantees, subinterpreters with shared objects) or any refactor to a fancier cache
  structure inherits a real race. One `threading.Lock` per cache would make it explicit for
  ~0 cost.
- **R2 — `zipfile` internals.** The entire CONCURRENT-ZIP design rests on the audited claims
  that `_SharedFile.read` holds `ZipFile._lock` and that only `_fileRefCnt` open/close is
  unlocked (`zip_reader.py:306-312`, `locked.py:71-78`); plus direct use of private
  `ZipFile._lock` (`zip_reader.py:522-524`, loud `AttributeError` if renamed) and private
  `ZipInfo._raw_time` (`zip_reader.py:514-520`, **silent** `getattr(..., 0)` fallback —
  filed in deep-unknown-unknowns as a should-fail-loud).
- **R3 — pycdlib walk/get_record not touching the shared fp** — pinned-version audit,
  documented at the call site (`iso_reader.py:346-350`). Good hygiene; still a reliance.
- **R4 — refcounting promptness for pass-token release.** An abandoned `__iter__`/
  `stream_members` generator releases its pass token in a `finally` that runs at generator
  close — immediate under refcounting, arbitrarily delayed under 3.13t's deferred refcounts
  / any tracing GC. Until then the reader rejects all other operations ("another reader
  operation is already active") with no hint that the owner is garbage. Not a corruption,
  but a usability trap unique to non-refcounted builds.
- **R5 — GIL switch granularity for the unlocked fast paths.** `_members_cache` (N2),
  `BaseArchiveReader._closed`, `ArchiveStream.closed` are read unlocked; on GIL builds these
  are single-reference reads (atomic by bytecode granularity), on 3.13t they are atomic by
  PEP 703 attribute semantics. Fine — but N2 shows that *pairs* of such fields need explicit
  ordering, which no build guarantees across two separate stores.
- **R6 — `lzma._decode_filter_properties`** — already converted to a loud import-time bind
  (`sevenzip_reader.py:169-184`) after the first review; the model to copy for R2's
  `_raw_time`.

## Findings summary

| # | Sev | Status | What | Where |
|---|-----|--------|------|-------|
| N1 | Med-High | VERIFIED (repro) | Exception mid streaming pass → `scan_members()`/`get_members_if_available()` silently publish the partial list as complete+resolved | `base_reader.py:1177-1189`, `:692-703` |
| N2 | Med | VERIFIED (interleaving) | Two-store snapshot publication + unlocked fast path → `AssertionError`/`AttributeError` from `get()`/`open(name)` racing first-touch under CONCURRENT | `base_reader.py:481,516-517,933,960` |
| N3 | Med-High | VERIFIED (code) | Internal-opens exemption is depth-scoped, not thread-scoped → foreign-thread `open()` silently admitted during `extract_all`/link-reads on a non-CONCURRENT reader; wrong bytes on unlocked TAR/ISO handles instead of the promised loud error | `reader_state.py:93-101,170-173,236-239` |
| N4 | Low-Med | VERIFIED (argument) | Same-thread CV deadlocks via non-emitting reader calls from `on_diagnostic`: (a) `begin_materialization` waits on own election, (b) `close()` drains own worker | `reader_state.py:215,293` |
| N5 | Very low | SUSPECTED | Lease finalizer takes `ReaderState._lock` at shutdown → hang if a daemon thread died holding it | `archive_stream.py:113`, `reader_state.py:253` |
| N6 | Trivial | VERIFIED | `take_teardown_error` is dead code; deferred-teardown errors stored, never read | `reader_state.py:324-328` |
| N7 | Trivial | VERIFIED | "Publish an immutable snapshot (tuple + frozen …)" comment describes code that publishes the mutable list/dict | `base_reader.py:516` |

## Corrections to the first review

1. **Lock-ordering section of `concurrency.md` is wrong** — ReaderState's lock and handle
   locks are never held simultaneously; see the table above. The conclusion (no deadlock
   cycle) stands, for a stronger reason; the deadlocks that do exist are CV self-waits (N4),
   which that section's method (lock-pair enumeration) could not find.
2. **Invariant I2/I3/I5 as stated are inaccurate** — see the corrected map (N3, N2, and the
   core.py-enforced I5).
3. **"DiagnosticCollector … Correct"** — correct for emit-vs-emit, but its guard does not
   deliver the promise in its own error message; N4 passes through it.
4. **C1/C2/C3 post-review statuses confirmed accurate** in the current tree.
