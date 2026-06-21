# Tasks — Phase 2: Stream layer (compressed + seekable)

> Run tools through uv: `uv run pytest`, `uv run pyrefly check`, `uv run ty check`,
> `uv run ruff`.
> Prerequisite: Phase 1 complete (spine + harness green).
> Clean-slate: build the package fresh; no `io_helpers.py` shim, no method-swap.

## 0. Carry-forward from the Phase-1 review (PR #5)

- [x] 0.1 **Wire the exception-translation spine.** Phase 1 left
      `BaseArchiveReader._stamp_error_context()` defined but **never called**, and there
      is no per-library translator hook or stream wrapper yet. The `ArchiveStream` built
      here is the carrier: route exceptions raised while reading a member stream through
      the backend's `_translate_exception()` (→ `ArchiveyError` subclass) and then through
      `_stamp_error_context()` so format/archive/member context is attached. Add a test
      that a raw decode error surfaces as a stamped `ArchiveyError`.
- [ ] 0.2 **(Reminder for the streaming readers, Phase 5+)** the base
      `_iter_with_data()` default eagerly registers all members and is for indexed
      backends only; forward-only/solid readers MUST override it (correctness, not
      efficiency) and must not call `_get_members_registered()`. See the docstring in
      `internal/reader.py`. Nothing to do in Phase 2 — just don't build on the default
      when the first streaming backend lands.
- [ ] 0.3 **(Reminder for Phase 3) open-time fail-fast on non-seekable sources.** The
      method-level access-mode enforcement is done (`streaming=True` is forward-only; see
      the access-mode × method table in `access-mode-and-cost`). What remains is the
      *open-time* rule: `open_archive(..., streaming=False)` (the random-access default)
      MUST fail fast when the source is non-seekable and the format requires seek — using
      `ReadBackend.REQUIRES_SEEK` / `_SUPPORTS_RANDOM_ACCESS`. Needs the real
      source/format detection that lands in Phase 3.

## 1. internal/streams package

> **DEV source map** (port-as-unit references; pin commit `730275b…`): the primitives
> are scattered, not all in `io_helpers.py` —
> `SlicingStream`, `BinaryIOWrapper` → `internal/io_helpers.py`;
> `is_seekable`/`is_stream`/`is_filename`/`ensure_binaryio`/`ensure_bufferedio`/
> `fix_stream_start_position` → `core.py`;
> `read_exact` → `formats/single_file_reader.py`;
> `DecompressorStream` → `formats/decompressor_stream.py`;
> `XzStream` → `formats/xz_stream.py`; `LzipStream` → `formats/lzip_stream.py`.
> Treat these as references to rewrite cleanly into the new layout, not files to copy.
>
> **Out of scope here:** DEV's `RecordableStream` / `RewindableStreamWrapper` (the
> detection peek/rewind primitive, used only inside the opener) are **not** ported into
> this phase — they are replaced by `PeekableStream`, built with `format-detection` in
> **Phase 3**.

- [x] 1.1 Create `src/archivey/internal/streams/` (with `__init__.py`).
- [x] 1.2 `slice.py` — port `SlicingStream`. (The detection peek/rewind primitive —
      DEV's `RecordableStream`/`RewindableStreamWrapper` — is **not** built here; it
      becomes `PeekableStream` in Phase 3 with `format-detection`.)
- [x] 1.3 `compat.py` — port `is_seekable`, `is_stream`, `is_filename`,
      `ensure_binaryio`, `ensure_bufferedio`, `fix_stream_start_position`,
      `read_exact`; write a **simplified `BinaryIOWrapper`** fresh (plain delegation
      + `readinto` fallback; **no** `self.read = self._raw.read`).
- [x] 1.4 Port the decompressor streams as `decompress.py` / `xz.py` / `lzip.py`;
      leave `archive_stream.py` where it is.
- [x] 1.5 **Exhaustive primitive/helper tests** — these streams and helpers are the
      building blocks every later format depends on, so test them hard *as units*, not
      just via the codec layer. First **mine DEV's stream tests** (pinned `730275b…`,
      e.g. `tests/test_io*.py` / stream-helper tests) and carry over every scenario that
      still applies, then add the corner cases the new layout introduces. Cover at least:
      short/zero-length reads and `read(0)`; `read()`-to-EOF vs sized reads; `readinto`
      (incl. the fallback path in the simplified `BinaryIOWrapper`); reads spanning the
      a slice boundary; `seek`/`tell` on seekable wrappers and `StreamNotSeekableError`
      on non-seekable ones; `SlicingStream` bounds (start/length, reads past the slice end,
      empty slice); `ensure_binaryio`/
      `fix_stream_start_position` on already-positioned streams; truncated/empty input;
      double `close()` / use-after-close; and large-member reads (the perf path in 5.6).

## 2. compressed-streams codec layer

- [x] 2.1 Codec registry with one default backend per codec (gzip/bz2/xz/raw-LZMA2/
      zlib/STORED, …); resolve-a-backend-without-opening.
- [x] 2.2 A single wrapped AES crypto stage, reachable only through the wrapper
      (compose decrypt → decompress for encrypted folders).
      *Interface-only this phase (maintainer decision): `internal/streams/crypto.py` provides
      the `CryptoBackend` wrapper, the single `get_crypto_backend()` access point, and the
      missing-backend gating. The concrete AES-CBC decryptor and the decrypt→decompress
      composition land with the native 7z/RAR readers in Phase 7, where they're exercised
      end-to-end.*
- [x] 2.3 Missing optional backend raises `PackageNotInstalledError` (e.g. PPMd
      without `pyppmd`, AES without the crypto backend).
- [x] 2.4 Translate decompression errors for corrupt and truncated input.
- [x] 2.5 Verify decompressed output against expected digests on a full read; leave
      partial reads unverified; skip unverifiable algorithms with a warning.

## 3. seekable-decompressor-streams

- [x] 3.1 XZ block-index seeking; lzip trailer-scan seeking.
- [x] 3.2 `rapidgzip` / `indexed_bzip2` accelerators behind `[seekable]`; clean
      behavior when the accelerator backend is absent.

## 4. Tests added (new suite)

- [x] 4.1 `compressed-streams` scenarios: default gzip backend, raw LZMA2 for a 7z
      folder, crypto wrapper reachability, missing-backend errors, corrupt/truncated
      translation, digest mismatch / partial / unverifiable, resolve-without-open.
- [x] 4.2 `seekable-decompressor-streams` scenarios: XZ and lzip seeking, accelerator
      present and absent.
- [ ] 4.3 Retire the matching `tests/_dev_oracle/` coverage as it transfers.
      *N/A this phase: the frozen oracle is fully quarantined (excluded from collection via
      `norecursedirs`), so there is no separately-running stream coverage to remove. The
      whole tree is deleted in Phase 10.*

## 5. Verify — acceptance criteria

**Spec scenarios covered**
- [x] 5.1 All of `compressed-streams`.
- [x] 5.2 All of `seekable-decompressor-streams`.

**Gates**
- [x] 5.3 `uv run pyrefly check` and `uv run ty check` both clean (strict).
- [x] 5.4 `uv run ruff check` clean.
- [x] 5.5 New stream tests green; frozen oracle no worse.
- [ ] 5.6 (If a perf regression is suspected) benchmark the simplified
      `BinaryIOWrapper` against DEV's method-swap on a hot read loop.
      *Not triggered: plain delegation is straightforward and no regression is suspected;
      the conditional benchmark was not run.*
