# Recon map — modules, concurrency surface, shared mutable state

Baseline captured 2026-07-12 on branch `claude/codebase-deep-review-656xfc`
(HEAD `0f4254d`). Everything below is traced from the code, not inferred from names.

## Baseline health

- **Tests:** `1348 passed, 70 skipped` (`uv run pytest`, `[all]` extras). 45s.
- **Coverage:** 87% lines (report-only, no gate — as designed).
- **Type check:** `pyrefly` 0 errors (3 warnings suppressed); `ty` clean.
- **Lint:** `ruff check` clean; `ruff format --check` clean (115 files).

So the tree is green on every gate. This review is about what the gates don't catch.

## Module map (src/archivey)

Public spine:
- `core.py` — `open_archive` / `open_stream` / `extract` entry points + detection wiring.
- `reader.py` — `ArchiveReader` ABC (public read surface) + `MemberSelector` alias.
- `types.py` — `ArchiveMember` (mutable), `ArchiveFormat`, `MemberType`, `MemberStreams`, …
- `config.py` — `ArchiveyConfig`, `ExtractionLimits`, `AcceleratorMode`, password provider types.
- `cost.py` — `CostReceipt` + the three orthogonal cost axes.
- `exceptions.py` — the `ArchiveyError` tree (+ `ArchiveyUsageError`, deliberately outside it).
- `diagnostics.py` — public diagnostic value types + `ExtractionReport`.

Internal spine (`internal/`):
- `base_reader.py` — `BaseArchiveReader` (the real machinery every backend extends) +
  `ReadBackend`/`WriteBackend` ABCs + `_ProgressivePassIterator`.
- `reader_state.py` — **`ReaderState`**: the whole concurrency/lifecycle state machine
  (operation tokens, live-stream gate, materialization election, draining close, teardown leases).
- `extraction.py` — `ExtractionCoordinator` (pull-based sink) + `BombTracker`.
- `filters.py` — `check_universal` (path safety) + policy permission transforms.
- `naming.py` — name normalization + link-target resolution.
- `detection.py` — magic/extension/content-probe/inner-tar detection.
- `registry.py` — backend registry + tri-state `FormatAvailability`.
- `volumes.py` — multi-volume discovery + `ConcatenatedFile`.
- `password.py` — `_PasswordCandidates` (candidate/provider state machine).
- `diagnostics_collector.py` — `DiagnosticCollector` (counts, retention, watermarks, RAISE).
- `selection.py`, `open_site.py`, `password_confirm.py`, `zipcrypto.py`, `logs.py`.

Backends (`internal/backends/`): `zip_reader`, `tar_reader`, `sevenzip_reader` +
`sevenzip_parser`, `iso_reader`, `single_file_reader`, `directory_reader`.

Streams (`internal/streams/`): `archive_stream` (translate/stamp + finalizer),
`codecs` (the whole codec table + accelerator wrappers + `VerifyingStream` peers),
`decompressor_stream` + `decompress`/`xz`/`lzip` (seekable decoders), `crypto`
(AES stage + 7z KDF), `verify`, `peekable`, `counting`, and the dependency-free
`streamtools/` core (`base`, `binaryio`, `slice`, `shared`, `locked`, `solid`).

## Concurrency surface (the mechanisms)

Concurrency is opt-in via `MemberStreams.CONCURRENT`. Without it the default contract is
forward-only, single-live-stream, single-owner. The mechanisms:

1. **`ReaderState` operation tokens** (`reader_state.py`) — root/child/worker tokens gate
   reader-wide passes (`__iter__`, `stream_members`, `extract_all`, `members`) vs short-lived
   workers (`open`/`read`/`get`). One `threading.RLock` guards all state; three `Condition`s
   (`_materialization_cv`, `_workers_cv`, `_close_cv`) coordinate waits.
2. **Materialization election** (`begin/complete/fail_materialization`) — first-touch member
   listing; under CONCURRENT overlapping callers wait on the CV and share the published snapshot.
3. **Live-stream gate + lifecycle leases** (`acquire/release_live_stream`, `_lease_count`,
   `claim/complete_teardown`) — enforces single-live-stream by default, defers `_close_archive`
   until the last escaped stream closes.
4. **Draining close** (`mark_reader_closed`) — under CONCURRENT, blocks new admissions and waits
   for in-flight workers before transitioning to READER_CLOSED.
5. **Per-backend shared-handle locks** — ZIP `CloseLockedStream` (serializes open/close only;
   reads go through zipfile's `_SharedFile`), TAR/ISO `LockedStream` (serializes every shared-fp op),
   `SharedSource`/`SlicingStream` re-seek-under-lock (7z, single-file stream sources).
6. **`ArchiveStream._open_lock`** — one-shot claim of `open_fn` so a lazy stream opens once.
7. **`weakref.finalize` guards** — `ArchiveStream` (lease release safety net) and
   `_AcceleratorStream` (must `close()` rapidgzip's C++ threads before interpreter exit).
8. **`DiagnosticCollector`** — RLock + per-thread reentrancy set (`_emitting_threads`).
9. **`_PasswordCandidates`** — `_state_lock` (known-good promotion) + `_provider_lock`
   (provider reentry guard); provider invoked with no archivey lock held.
10. **Process-global, install-once:** the pycdlib deque cycle-guard
    (`iso_reader._install_pycdlib_directory_cycle_guard`, module-scope, `_PYCDLIB_CYCLE_GUARD_INSTALLED`).

## Shared mutable state inventory

Per-reader (guarded by `ReaderState` under the pass/worker/materialization protocol):
- `_members_cache`, `_members_by_name_lists` — published-once immutable snapshots.
- `_forward_pass_started`, `_progressive_gen`, `_pass_scanned`, `_pass_by_name_lists` —
  streaming pass state (single-owner; a streaming reader never fans out).
- `_folder_passwords` (7z), `_header_cache`/`_pending_stream` (single-file),
  `_uname_cache`/`_gname_cache` (directory) — per-reader caches.
- `ArchiveMember` fields (`_member_id`, `_archive_id`, `link_target_member`, `link_target`,
  `_diagnostics`) — mutated during materialization / lazy link resolution.

Per-reader, backend-owned handle state (guarded by the backend handle lock under CONCURRENT):
- zipfile `ZipFile.fp` + `_fileRefCnt`, tarfile shared `fileobj`, pycdlib `_cdfp`,
  `SharedSource._handle` position.

Process-global:
- `registry._registry` (populated at import, read-only after).
- `codecs._zstd/_lz4_frame/_brotli/…` optional-module sentinels (set once at import).
- `iso_reader.pcd_module.collections` proxy (install-once).
- `DiagnosticCollector` instances are per-scope, not global.

## Where I focused the deep passes

The interesting concurrency lives in `ReaderState` + the three CV coordinations, the backend
handle locks, and the finalizer/teardown interplay. The interesting correctness lives in the
extraction coordinator (link/orphan/bomb logic), the seekable-decoder index math, and the 7z
parser (hostile input). Those got the most attention; the leaf codecs and the data-model types
are largely mechanical and correct.
