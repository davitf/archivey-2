# Tasks — Phase 4a: TAR forward-only streaming + `strict_eof`

> Run tools through uv: `uv run pytest`, `uv run pyrefly check`, `uv run ty check`,
> `uv run ruff`.
> Prerequisite: Phase 3 complete (TAR random-access reader + detection green).
> Companion change: `phase-4-safe-extraction` (extraction; can merge in either order).
> Clean-slate: override the ABC; port streaming logic from DEV as reference only.

> **DEV source map** (pin commit `730275b…`): `formats/tar_reader.py` — the forward-only /
> non-seekable iteration path (not the `ExtractionHelper`). v2's `_iter_with_data()`
> override replaces DEV's separate streaming reader shape.

## 0. Decisions locked in this change (no code, just honored below)

- [ ] 0.1 **`strict_eof` lands here** — single `strict_eof: bool = False` kwarg on
      `open_archive()`, threaded into `TarReader`; no full public `ReaderConfig` yet.
- [ ] 0.2 **Progressive iteration only** — streaming path MUST NOT call `getmembers()`;
      use `tarfile` forward iteration.
- [ ] 0.3 **`REQUIRES_SEEK` is conditional** — fail fast only when `streaming=False`.
- [ ] 0.4 **Expose `compressed_source_size`** on the reader for
      `phase-4-safe-extraction` (file size when known; `None` otherwise).
- [ ] 0.5 **No extraction work** — `extract_all` stays deferred to
      `phase-4-safe-extraction`.

## 1. Access gating + cost surface

- [ ] 1.1 **`TarReadBackend` / `open_archive` gating** — allow non-seekable TAR when
      `streaming=True`; keep `StreamNotSeekableError` for `streaming=False`. Update
      `REQUIRES_SEEK` handling in the registry/opener (backend declares seek required for
      random access only, or opener checks `streaming` before enforcing seek).
- [ ] 1.2 **`CostReceipt.stream_capability`** — set from `is_seekable(source)`:
      `SEEKABLE` vs `FORWARD_ONLY` for TAR (plain and compressed). Keep
      `listing_cost` / `access_cost` format-driven (unchanged).
- [ ] 1.3 **`compressed_source_size` property** — on `BaseArchiveReader` (or `TarReader`
      only initially): `Path` → `st_size` for compressed formats; `None` for plain tar,
      unknown streams, pipes.
- [ ] 1.4 **Tests** — update `test_non_seekable_tar_fails_fast*` to pass only for
      `streaming=False`; add streaming-mode open succeeds (no members read yet).

## 2. Plain `.tar` forward-only `_iter_with_data()`

- [ ] 2.1 **Override `_iter_with_data()` in `TarReader`** — progressive `tarfile`
      iteration; assign `member_id` as members are yielded; yield `None` stream for
      non-file members; wrap file streams with `_wrap_member_stream`.
- [ ] 2.2 **Do not call `_get_members_registered()`** in the override — verify with a test
      that a deliberately broken `getmembers()` would not run (or spy that incremental path
      is used).
- [ ] 2.3 **`stream_members()` contract** — stream invalid after advance; selector skips
      unselected members without opening streams; late-bound fields visible on original
      member object after read.
- [ ] 2.4 **Tests** — non-seekable plain tar: `stream_members()` + `__iter__` over corpus
      tar fixtures; `members()` / `__getitem__` raise `UnsupportedOperationError` on
      `streaming=True` reader (may already be covered by `test_reader_contract.py` — extend
      with real TAR backend).

## 3. Compressed tar streaming (`.tar.gz` + smoke)

- [ ] 3.1 **Compressed path** — same override over codec-decompressed stream with
      `StreamConfig(streaming=True)`; `PeekableStream` / non-seekable outer source works
      end-to-end.
- [ ] 3.2 **`testing-contract` scenario** — *non-seekable TAR.GZ source*: open with
      `streaming=True` via `NonSeekableBytesIO` (or `tests/streams_util.py` helper);
      iterate all members; read data; no `seek`/`tell` on underlying raw stream.
- [ ] 3.3 **Smoke** — at least one other compressed tar variant (e.g. `.tar.xz` or
      `.tar.bz2`) on non-seekable source via `stream_members()`.
- [ ] 3.4 **Retire / update** — flip comments in `test_tar.py` module docstring; remove
      "Phase 4" deferral notes where implemented.

## 4. `strict_eof` truncation detection

- [ ] 4.1 **`open_archive(..., strict_eof: bool = False)`** — thread into `TarReader`.
- [ ] 4.2 **End-of-archive check** — after last member in streaming pass AND after full
      scan in random-access mode: verify null 512-byte block(s); warn or raise per
      `format-tar` scenarios.
- [ ] 4.3 **Tests** — valid EOF silent; truncated warns by default; truncated +
      `strict_eof=True` raises `TruncatedError` (streaming and random-access paths).

## 5. Gates

- [ ] 5.1 `uv run pyrefly check` + `uv run ty check` + `uv run ruff` clean.
- [ ] 5.2 All new / updated tests green.
- [ ] 5.3 No `pending_*` / extraction code introduced (out of scope).
