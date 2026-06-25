# Tasks — Unify the read-only stream wrappers behind a shared base

> Behavior-preserving internal refactor. **Sequence after `codec-descriptor-refactor` (PR #16)
> merges**, since that PR rewrites `codecs.py` where several of these wrappers live. Run tools
> through `uv` (`uv run pytest`, `uv run pyrefly check`, `uv run ty check`, `uv run ruff`). The
> existing stream tests are the regression net — they must pass unchanged.

## 1. The base classes

- [ ] 1.1 Add `ReadOnlyIOStream(io.RawIOBase, BinaryIO)` in
      `internal/streams/streamtools/` : `readable()→True`, `writable()→False`, `write()` raises
      `io.UnsupportedOperation`; `readinto(b)` implemented once via `self.read(len(b))`
      (including the non-blocking `None` guard from `BinaryIOWrapper`); `readall()` as the
      standard read-loop; `read()` raises `NotImplementedError` (subclass must provide it).
- [ ] 1.2 Add `DelegatingStream(ReadOnlyIOStream)`: store `self._inner: BinaryIO`; forward
      `read/seek/tell/seekable/close`; forward `readinto` straight to `inner.readinto` (zero-copy)
      when available, else fall back to the base. Document that subclasses override only the
      method whose behavior they change.
- [ ] 1.3 Unit-test the bases directly: a minimal `read`-only subclass yields working
      `readinto`/`readall`; a delegating subclass forwards seek/tell/close and the zero-copy
      `readinto`; `write()` raises.

## 2. Migrate the delegating wrappers (each: drop boilerplate, keep its one method)

- [ ] 2.1 Remove `_SlowSeekWarningStream`; fold its warn-once-on-rewind into `ArchiveStream`
      (see 3.2). `open_codec_stream` passes the per-codec "rewind is O(n)" signal (codec name +
      accelerator name, sourced from the descriptor) through to the `ArchiveStream` it builds.
- [ ] 2.2 `_ZstdReopenStream` → `DelegatingStream`, override `seek` only.
- [ ] 2.3 `_GzipTruncationCheckStream` → `DelegatingStream`, override `read` + `seek`.
- [ ] 2.4 `_AcceleratorStream` → `DelegatingStream`, keep the `weakref.finalize` close guard
      and `close()`; confirm the shutdown canary still passes (close-on-finalize unchanged).
- [ ] 2.5 `VerifyingStream` → `DelegatingStream`, override `read` (hash) + keep `seekable()→False`
      and the EOF-verify semantics; confirm `readall` still verifies once.

## 3. Migrate the read-only-base-only wrappers

- [ ] 3.1 `SlicingStream`, `PeekableStream` → `ReadOnlyIOStream`; keep their offset-remap /
      prefix-buffer `read`/`seek` and their explicit `seekable()` (Peekable stays non-seekable).
- [ ] 3.2 `ArchiveStream` → `ReadOnlyIOStream`; keep per-call exception translation and lazy
      open (does NOT use `DelegatingStream` — translation must wrap every call). Add the
      rewind warning here: accept an optional slow-seek signal and warn once when a `seek` lands
      before the current position (absorbing `_SlowSeekWarningStream`). This is the public
      surface where seek cost / seek-point metadata will later live.
- [ ] 3.3 Confirm the accelerator close-guard stays at creation (codec level), NOT in
      `ArchiveStream`: `backend.open()` can create a rapidgzip object with no `ArchiveStream`
      (the size probe; future TAR/7z), so the guard must attach where the object is born or the
      macOS shutdown abort returns for those paths.
- [ ] 3.4 `DecompressorStream` → `ReadOnlyIOStream`; keep its decompressor ownership and
      seek-by-redecompress.

## 4. `BinaryIOWrapper`

- [ ] 4.1 Inherit `ReadOnlyIOStream` for `readable/writable/write` only; keep its specialized
      `read`/`readinto` (the `None`/non-blocking and `readinto`-raises handling) and its
      no-close-of-the-wrapped-object behavior. Add a class-doc note on why it stays distinct
      (adapts a duck-typed, possibly non-`BinaryIO` object).

## 5. Verify

- [ ] 5.1 Full suite green unchanged; `uv run ruff/pyrefly/ty` clean.
- [ ] 5.2 Sanity-grep that no migrated class still hand-defines `readable`/`writable`/`write`
      or a bespoke `readinto` that the base now provides (catch leftover boilerplate).
- [ ] 5.3 Confirm net line reduction and that each migrated class body is now essentially its
      one or two real methods.
