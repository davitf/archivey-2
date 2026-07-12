# Theme 1 — Concurrency

The concurrency design here is unusually disciplined for a Python library: opt-in via
`MemberStreams.CONCURRENT`, a single `RLock` per reader, explicit token ownership, and a
free-threaded CI job (`3.13t`). Most of what I looked for isn't here to find. The findings
below are the exceptions, ranked by severity.

## Invariants (as I reconstructed them from the code)

- **I1.** At most one reader-wide *root* pass at a time (`__iter__`/`stream_members`/
  `extract_all`/`members`/`scan_members`), unless nested as a child under an internal owner.
- **I2.** Without CONCURRENT, at most one live public member stream (`_live_streams`).
- **I3.** The member snapshot (`_members_cache`/`_members_by_name_lists`) is published exactly
  once and is immutable after publication; readers past that point never mutate it.
- **I4.** `_close_archive` runs at most once, after the reader lease and every escaped-stream
  lease drop (`_lease_count == 0`), guarded by `_teardown_claimed`.
- **I5.** A streaming reader is single-owner: its progressive pass never fans out.
- **I6.** The backend shared handle (zipfile fp, tarfile fileobj, pycdlib `_cdfp`,
  `SharedSource._handle`) is only touched under the backend handle lock when CONCURRENT.

## Findings

### C1 — BaseException during materialization wedges the reader (VERIFIED, medium)

`base_reader.py:503` — `_get_members_registered` elects a materialization owner via
`begin_materialization()` (sets `cache_state = MATERIALIZING`), then:

```python
try:
    members = list(self._iter_members())
    ...
except Exception:
    self._state.fail_materialization()
    raise
```

`except Exception` does **not** catch `KeyboardInterrupt`, `SystemExit`, or `MemoryError`.
If any of those fires between `begin_materialization()` and `complete_materialization()` —
e.g. Ctrl-C during a large TAR header scan or 7z folder decode, both of which run inside
`_iter_members()`/`_build_members` — `fail_materialization()` never runs and `cache_state`
is left `MATERIALIZING` permanently.

Consequences, both on the same live reader object after the interrupt is caught:
- **Non-concurrent:** the next `members()`/`get()`/`open(name)` calls `begin_materialization()`,
  sees `MATERIALIZING`, and raises `ArchiveyUsageError("another materialization is already in
  progress")` — a misleading error for what is actually a wedged reader.
- **CONCURRENT:** a second caller blocks on `_materialization_cv.wait()` forever — no owner will
  ever notify, because the owner unwound past the `except`. This is a latent deadlock.

Failure scenario: `KeyboardInterrupt` (or `MemoryError` on a bomb) mid-`members()`, caught by
the application, reader reused → wedged/hung.

Why not a direct fix: this touches a concurrency mechanism (the maintainer rule says don't).
The fix is small — `except BaseException:` for the state-cleanup (re-raising), leaving the
error-translation `except Exception` where it is — but I want it decided. See QUESTIONS Q1.

### C2 — `get_members_if_available()` races per-reader caches on directory (SUSPECTED, low-medium)

The public `get_members_if_available()` is documented (`reader.py:89`, `base_reader.py:872`) as
scan-free and "safe to call on any reader", and it runs **outside** the materialization
election — it goes straight to `_get_members_index_only()` when `_MEMBER_LIST_UPFRONT` is set
(`base_reader.py:885`). For the directory backend that path calls `_iter_members()` →
`_scan()` → `_make_member()` → `_lookup_uname/_lookup_gname`, which mutate the plain dicts
`_uname_cache`/`_gname_cache` (`directory_reader.py:230-248`).

Under CONCURRENT, two threads may call `get_members_if_available()` simultaneously (nothing
serializes it — it takes no pass/worker token, only `require_open`). Both then mutate the same
`dict` concurrently. Under the GIL this is benign; under free-threading (`3.13t`) concurrent
`dict.__setitem__` on the same dict is a data race with no lock. The `3.13t` CI job exercises
concurrent `open`/`read`, but I don't see it exercising concurrent `get_members_if_available()`
on a directory (see tests.md T3).

This is entangled with a design smell (C3) — the real question is whether directory's
`get_members_if_available()` should be doing a full walk at all.

### C3 — directory `get_members_if_available()` does a full filesystem walk (VERIFIED discrepancy, low)

`DirectoryReader` sets `_MEMBER_LIST_UPFRONT = True` (`directory_reader.py:58`). But a directory
has no upfront index: `get_members_if_available()` → `_get_members_index_only()` →
`list(self._iter_members())` walks the entire tree with `os.scandir` recursion, uncached, on
**every** call. The method's contract is "available *without scanning* … never triggers a
forward scan" (`base_reader.py:872-879`). A recursive filesystem walk is exactly a scan.

Same tension in the cost model: `_get_archive_info` reports `ListingCost.INDEXED`
(`directory_reader.py:261`), whose docstring says "listing is O(1) regardless of archive size"
(`cost.py:19`). A directory walk is O(entries). `REQUIRES_SCANNING` is the honest value.

Neither is a crash, but both mislead a cost-aware caller (the founding dedupe use case reasons
off exactly these signals). This is a design decision for the maintainer — see QUESTIONS Q2.

### C4 — `SharedSource.view()` past-EOF clamp under a shrinking source (SUSPECTED, very low)

`shared.py:108-117` caches `self._size` once at construction. If the underlying file is
truncated by another process after open, `view(start, length)` clamps against the stale size.
This only matters for a live-mutated on-disk archive (already outside archivey's trust model),
and reads would surface as short reads / `TruncatedError` downstream. Noting for completeness;
not worth action.

## What I checked and found correct

- **Draining close** (`mark_reader_closed`, `reader_state.py:261-301`): sets `_closing` before
  waiting, new admissions rejected via `_require_admissible_locked`, `except BaseException`
  correctly resets `_closing` and re-notifies so an interrupted closer doesn't wedge *other*
  closers. This one *does* handle BaseException — which makes C1's omission look like an
  oversight rather than a policy.
- **Teardown lease accounting** (`_lease_count`, `claim_teardown`): reader starts at lease 1,
  each live stream +1; teardown fires exactly once when it reaches 0 and READER_CLOSED. Escaped
  streams correctly keep the archive alive. `_maybe_teardown`'s `ExceptionGroup` combination of
  stream-close + teardown failures is correct.
- **`ArchiveStream._ensure_open`** (`archive_stream.py:182-219`): claims `open_fn` under the lock,
  runs it *outside* the lock (so a backend handle lock never nests under stream-state — correct
  lock ordering), re-checks `closed` after. The close-races-open window is handled (opened stream
  closed if `self.closed`).
- **`_AcceleratorStream`** finalizer: `staticmethod` callback holds only the raw inner (not
  `self`), so GC-time close works. This is the load-bearing fix for the rapidgzip SIGABRT and is
  correctly built.
- **`DiagnosticCollector`** per-thread reentrancy set: concurrent emits on different threads don't
  read as reentrancy; a callback re-entering on its own thread trips the guard. Delivery happens
  outside the lock. Correct.
- **`_PasswordCandidates`**: provider invoked with no archivey lock held; `_provider_depth`
  reentry guard; known-good promotion under `_state_lock`. Convergent under concurrent first-touch.
- **ZIP `CloseLockedStream`** vs TAR/ISO `LockedStream`: the distinction is deliberate and correct
  — zipfile's `_SharedFile` already serializes reads, only `_fileRefCnt` on open/close races, so
  ZIP serializes just open/close and lets independent decompressors run in parallel; tarfile/pycdlib
  have no internal per-view locking so every op is serialized.

## Lock-ordering / deadlock check

Two lock classes can be held simultaneously: `ReaderState._lock` and a backend handle lock.
The ordering is always ReaderState-outer, handle-inner (e.g. `open()` acquires a worker token
under ReaderState, then `_open_member` takes the handle lock). I found no path that takes them
in the opposite order. `ArchiveStream._ensure_open` deliberately runs `open_fn` (which may take
the handle lock) *outside* its own `_open_lock`, avoiding a third nesting. No deadlock cycle found.
