## 1. API surface

- [ ] 1.1 Add `ConcurrentAccessError` to `src/archivey/exceptions.py` (message names the open
      stream, `with`/`close()`, and the `allow_multiple_open_streams=True` opt-in + solid cost)
- [ ] 1.2 Add `allow_multiple_open_streams: bool = False` to `open_archive()` (and any reader
      factory it flows through); carry it on the reader
- [ ] 1.3 Reject `streaming=True` together with `allow_multiple_open_streams=True` at
      `open_archive()` with a typed error

## 2. Live-stream gate in the reader

- [ ] 2.1 In `BaseArchiveReader.open()`, register the returned handle as a live member stream
      under a lock
- [ ] 2.2 Install an (idempotent) close hook on the handle that deregisters it on
      `close()` / context-manager exit
- [ ] 2.3 Raise `ConcurrentAccessError` when a second stream would be live while another still is,
      unless `allow_multiple_open_streams` is set — uniformly for every backend
- [ ] 2.4 Ensure liveness ends only at close (not EOF, not GC); confirm a re-opened member after
      close is allowed
- [ ] 2.5 When opt-in: member-cache init-under-lock (or equivalent) so multi-thread first
      `members()` is safe; document thread-safe `open`/stream read after materialize
- [ ] 2.6 Confirm gate bookkeeping is synchronized shared state (allowed) and not unprotected
      per-open scratch

## 3. Specs, ABC, docs

- [ ] 3.1 Apply the `archive-reading` delta (open signature; opt-in concurrent-open rewrite;
      reentrancy unsafe-mutation wording; streaming+flag reject; stream_members rationale;
      thread-safe open/read contract)
- [ ] 3.2 Apply the `access-mode-and-cost` delta (flag composes with `streaming`; cost is
      informational, not a gate)
- [ ] 3.3 Update `BaseArchiveReader.open()` / `_open_member` docstrings and `docs/parallel-reader.md`

## 4. Tests

- [ ] 4.1 Second overlapping `open()` raises `ConcurrentAccessError` by default — one test each for
      a DIRECT format (ZIP), plain TAR, and a solid format (`.tar.gz` or 7z), asserting the
      **same** behaviour across all
- [ ] 4.2 Sequential `open → read → close → open next` is allowed by default on all formats
- [ ] 4.3 After the second open raises, the first stream is still readable (no auto-close)
- [ ] 4.4 With `allow_multiple_open_streams=True`, two members open at once read interleaved,
      bytes correct (byte-range backend)
- [ ] 4.5 `with reader.open(m)` / explicit `close()` release liveness so a subsequent open succeeds
- [ ] 4.6 `streaming=True, allow_multiple_open_streams=True` raises at `open_archive()`
- [ ] 4.7 Two threads, opted-in, after `members()`: concurrent `open`+read return correct bytes

## 5. Verification

- [ ] 5.1 `uv run --no-sync ruff check` on touched paths
- [ ] 5.2 `uv run --no-sync pyrefly check` and `uv run --no-sync ty check` clean
- [ ] 5.3 `uv run --no-sync pytest` for reader-contract / concurrent-open tests (full suite +
      three-config gate before push per CONTRIBUTING)
