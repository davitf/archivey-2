# Tasks — Unify the read-only stream wrappers behind a shared base

> Behavior-preserving internal refactor. Implemented on top of `codec-descriptor-refactor`
> (PR #16, merged). Run tools through `uv` (`uv run pytest`, `uv run pyrefly check`,
> `uv run ty check`, `uv run ruff`). The existing stream tests are the regression net — they
> pass unchanged (482 passed / 7 skipped).

## 1. The base classes

- [x] 1.1 Add `ReadOnlyIOStream(io.RawIOBase, BinaryIO)` in `internal/streams/streamtools/base.py`:
      `readable()→True`, `writable()→False`, `write()` raises `io.UnsupportedOperation`;
      `readinto(b)` implemented once via `self.read(len(b))`; `readall()` as the standard
      read-loop; `read()` raises `NotImplementedError` (subclass must provide it). (The
      non-blocking `None` guard stays in `BinaryIOWrapper.read`, which is the only place a raw
      object can return `None`; the base's `readinto` delegates to the subclass `read`.)
- [x] 1.2 Add `DelegatingStream(ReadOnlyIOStream)`: store `self._inner`; forward
      `read/seek/tell/seekable/close`; forward `readinto` straight to `inner.readinto` (zero-copy)
      when available, else fall back to the base.
- [x] 1.3 Unit-test the bases directly (`tests/test_stream_bases.py`): read-only subclass yields
      working `readinto`/`readall`; delegating subclass forwards seek/tell/close and zero-copy
      `readinto` (+ a no-`readinto` inner falls back); `write()` raises; `read()` is abstract.

## 2. Migrate the delegating wrappers (each: drop boilerplate, keep its one method)

- [x] 2.1 Removed `_SlowSeekWarningStream`; folded its warn-once-on-rewind into `ArchiveStream`
      (see 3.2). Added `StreamCodec.rewind_warning(config) -> RewindWarning | None` (config-aware,
      like `translator(config)`); `resolve_codec`/`CodecBackend` carry it and `open_codec_stream`
      passes it to the `ArchiveStream`. gzip/bzip2 return `None` when their accelerator is active.
- [x] 2.2 `_ZstdReopenStream` → `DelegatingStream`, overrides `seek` (reopen) + `seekable` only.
- [x] 2.3 `_GzipTruncationCheckStream` → `DelegatingStream`, overrides `read` + `seek`, and keeps
      a `read`-based `readinto` (NOT the zero-copy passthrough) so the byte-total tracking and the
      EOF truncation check still run on `readinto`-driven reads.
- [x] 2.4 `_AcceleratorStream` → `DelegatingStream`, keeps the `weakref.finalize` close guard and
      `close()`; the shutdown canary still passes (close-on-finalize unchanged).
- [x] 2.5 `VerifyingStream` → **`ReadOnlyIOStream`** (deviation from "DelegatingStream"): a
      verifier is sequential, and `DelegatingStream` would forward `seek` to the inner — wrong
      when `seekable()` is `False`. So it sits on `ReadOnlyIOStream`, overrides `read` (hash) +
      `seekable()→False` + `close`; `readinto`/`readall` come from the base (built on its `read`,
      so they hash too). EOF-verify semantics unchanged.

## 3. Migrate the read-only-base-only wrappers

- [x] 3.1 `SlicingStream`, `PeekableStream` → `ReadOnlyIOStream`; kept their offset-remap /
      prefix-buffer `read`/`seek` and explicit `seekable()` (Peekable stays non-seekable). Dropped
      `readinto`/`readable`/`writable`.
- [x] 3.2 `ArchiveStream` → `ReadOnlyIOStream`; kept per-call exception translation + lazy open
      (does NOT use `DelegatingStream` — translation wraps every call) and its translating
      `readinto`. Added the rewind warning: an optional `RewindWarning` and a warn-once when a
      `seek` lands before the current position (absorbing `_SlowSeekWarningStream`). This is the
      public surface where seek cost / seek-point metadata will later live.
- [x] 3.3 Confirmed the accelerator close-guard stays at creation (`_AcceleratorStream`), NOT in
      `ArchiveStream`: `backend.open()` can create a rapidgzip object with no `ArchiveStream` (the
      size probe; future TAR/7z), so the guard must attach where the object is born.
- [x] 3.4 `DecompressorStream` → `ReadOnlyIOStream`; kept its decompressor ownership, custom
      `readall`, and seek-by-redecompress. Dropped `readinto`/`readable`/`writable`.

## 4. `BinaryIOWrapper`

- [x] 4.1 **Kept as-is on `(io.RawIOBase, BinaryIO)`** (deviation): on inspection it is *read/write
      adaptive* — its `readable`/`writable`/`write` delegate to the wrapped (possibly writable,
      possibly non-`io.IOBase`) object, so the read-only base's fixed `writable()→False` /
      `write()`-raises would change its contract. It stays a distinct adapter (the read-only base
      is for read-only streams).

## 5. Verify

- [x] 5.1 Full suite green unchanged (482 passed / 7 skipped); `uv run ruff/pyrefly/ty` clean.
- [x] 5.2 Grep confirmed no migrated class still hand-defines `readable`/`writable`/`write`; the
      only remaining `readinto` overrides are intentional (`_GzipTruncationCheckStream`'s tracking
      `read`-based one; `ArchiveStream`'s translating one). Only `BinaryIOWrapper` keeps the old base.
- [x] 5.3 Net line reduction across the migrated wrappers; each is now essentially its one or two
      real methods plus the behavior it adds.
