# Tasks — Phase 2: Stream layer (compressed + seekable)

> Run tools through uv: `uv run pytest`, `uv run pyrefly check`, `uv run ty check`,
> `uv run ruff`.
> Prerequisite: Phase 1 complete (spine + harness green).
> Clean-slate: build the package fresh; no `io_helpers.py` shim, no method-swap.

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

- [ ] 1.1 Create `src/archivey/internal/streams/` (with `__init__.py`).
- [ ] 1.2 `slice.py` — port `SlicingStream`. (The detection peek/rewind primitive —
      DEV's `RecordableStream`/`RewindableStreamWrapper` — is **not** built here; it
      becomes `PeekableStream` in Phase 3 with `format-detection`.)
- [ ] 1.3 `compat.py` — port `is_seekable`, `is_stream`, `is_filename`,
      `ensure_binaryio`, `ensure_bufferedio`, `fix_stream_start_position`,
      `read_exact`; write a **simplified `BinaryIOWrapper`** fresh (plain delegation
      + `readinto` fallback; **no** `self.read = self._raw.read`).
- [ ] 1.4 Port the decompressor streams as `decompress.py` / `xz.py` / `lzip.py`;
      leave `archive_stream.py` where it is.
- [ ] 1.5 **Exhaustive primitive/helper tests** — these streams and helpers are the
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

- [ ] 2.1 Codec registry with one default backend per codec (gzip/bz2/xz/raw-LZMA2/
      zlib/STORED, …); resolve-a-backend-without-opening.
- [ ] 2.2 A single wrapped AES crypto stage, reachable only through the wrapper
      (compose decrypt → decompress for encrypted folders).
- [ ] 2.3 Missing optional backend raises `PackageNotInstalledError` (e.g. PPMd
      without `pyppmd`, AES without the crypto backend).
- [ ] 2.4 Translate decompression errors for corrupt and truncated input.
- [ ] 2.5 Verify decompressed output against expected digests on a full read; leave
      partial reads unverified; skip unverifiable algorithms with a warning.

## 3. seekable-decompressor-streams

- [ ] 3.1 XZ block-index seeking; lzip trailer-scan seeking.
- [ ] 3.2 `rapidgzip` / `indexed_bzip2` accelerators behind `[seekable]`; clean
      behavior when the accelerator backend is absent.

## 4. Tests added (new suite)

- [ ] 4.1 `compressed-streams` scenarios: default gzip backend, raw LZMA2 for a 7z
      folder, crypto wrapper reachability, missing-backend errors, corrupt/truncated
      translation, digest mismatch / partial / unverifiable, resolve-without-open.
- [ ] 4.2 `seekable-decompressor-streams` scenarios: XZ and lzip seeking, accelerator
      present and absent.
- [ ] 4.3 Retire the matching `tests/_dev_oracle/` coverage as it transfers.

## 5. Verify — acceptance criteria

**Spec scenarios covered**
- [ ] 5.1 All of `compressed-streams`.
- [ ] 5.2 All of `seekable-decompressor-streams`.

**Gates**
- [ ] 5.3 `uv run pyrefly check` and `uv run ty check` both clean (strict).
- [ ] 5.4 `uv run ruff check` clean.
- [ ] 5.5 New stream tests green; frozen oracle no worse.
- [ ] 5.6 (If a perf regression is suspected) benchmark the simplified
      `BinaryIOWrapper` against DEV's method-swap on a hot read loop.
