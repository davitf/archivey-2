# Archivey — Implementation Plan (v2 Clean-Slate Rewrite)

> **Approach:** clean-slate rewrite. New code is written fresh against `SPEC.md`,
> `ARCHITECTURE.md`, and the authoritative `openspec/specs/` capability specs. The
> existing `archivey-dev` codebase is **reference-only** — we read it and port
> specific, well-isolated parts (leaf format/codec logic), but we do **not** copy
> it wholesale as a baseline.
> **No backwards-compatibility requirement** with DEV's public API.
>
> Each phase ends mergeable, `mypy --strict`-clean, `ruff`-clean, with the named
> new tests green. **"Done" for a phase = the listed spec scenarios are covered by
> passing tests in the new suite** — not "the diff looks finished."

---

## Clean-slate, but layered

Port-vs-rewrite is decided by **layer**, not file-by-file:

- **Port as whole units** (clean port, interface-only edits) — the *leaf* logic
  that is correct and hard to re-derive: format backends' decode/parse (ZIP, TAR
  + all variants, single-file compressors, ISO, directory), format-detection
  heuristics, and the stream primitives (`ArchiveStream`,
  `RewindableStreamWrapper`/`RecordableStream`, `DecompressorStream`, `XzStream`,
  `LzipStream`). Pull these from DEV as units and adapt only their interface to
  the new ABC. Rewriting them from memory is pure downside risk — lost edge cases.
- **Write fresh against SPEC/ARCHITECTURE** (never copy-then-delete) — the
  *spine*: the public API, the `BaseArchiveReader` ABC, the backend registry,
  `ExtractionCoordinator`, and the `internal/streams/` package layout. These are
  the parts the rewrite exists to fix; copying DEV's versions only to dismantle
  them imports the very complexity we're removing. We build the target shape
  **once, correctly** — there is no later "interface cleanup" phase.

| DEV area | Disposition |
|----------|-------------|
| ZIP / TAR / single-file / ISO / directory decode logic | **Port as unit** (interface adapted to new ABC) |
| Format detection logic + magic table | **Port as unit** |
| `ArchiveStream`, `Rewindable`/`Recordable`, `DecompressorStream`/XZ/lzip | **Port as unit** (relocated into `internal/streams/`) |
| Declarative test corpus (`sample_archives.py`, `ArchiveContents`, `FileInfo`) | **Port as unit** (cleaned; see test strategy) |
| Public API surface (`open_archive`, reader methods, types) | **Write fresh** to `SPEC.md` |
| `BaseArchiveReader` ABC + registration/iteration/link logic | **Write fresh** to `ARCHITECTURE.md` |
| Backend registry + `Backend` ABC | **Write fresh** |
| `ExtractionHelper` (pending/deferred state machine) | **Write fresh** as `ExtractionCoordinator` |
| `io_helpers.py` god-module, `BinaryIOWrapper` method-swap trick | **Write fresh** as the `internal/streams/` package |
| 7z `py7zr` reader, RAR `rarfile` reader | **Reference only** — not ported (native-first, Phase 7) |
| DEV `test_*.py` drivers | **Reference only** — not ported (frozen oracle, then deleted) |

---

## Test strategy: frozen oracle, new suite grows, old set deleted

We do **not** keep DEV's test suite. It is a temporary scaffold.

1. **Durable assets reused from DEV:** (a) the *declarative archive corpus* —
   `sample_archives.py` specs plus `ArchiveContents`/`FileInfo` expected data,
   which describe archives independently of any API; and (b) the cross-check
   *oracle libraries* (`py7zr`, `rarfile`, `7z`/`unrar` CLIs) per
   `testing-contract`. The DEV `test_*.py` *drivers* are bound to the old API and
   are **not** ported.
2. **Frozen oracle.** DEV's suite is cloned into a quarantined, read-only
   location (`tests/_dev_oracle/`, git-ignored from refactoring) and run as a
   regression gate while we build. It is never refactored and is allowed to
   skip/xfail as APIs diverge — we invest only in the new suite.
3. **New suite grows per phase.** Each phase writes tests covering *its* spec
   scenarios (migrating expectations from the corpus) and retires the
   corresponding frozen-oracle coverage as it transfers.
4. **Old set deleted at the end (Phase 10).** Once every spec scenario is covered
   by the new suite, the frozen DEV oracle tree is deleted. The new, well-defined
   suite becomes the sole suite.

Consequence: the test-framework **foundations** (declarative harness, on-demand
generation + cache, committed-fixture JSON sidecars, no committed binaries, flat
`tests/`) are built in **Phase 1**, not deferred. `testing-contract` is a
through-line, finalized in Phase 10.

---

## 7z / RAR in the baseline (resolved)

The 7z/RAR **read** path is native-first, and DEV's `py7zr`/`rarfile` read
backends are explicitly interim. The clean-slate answer to the open sequencing
question in `openspec/project.md` is therefore: **do not port them.** 7z and RAR
reads are marked `xfail`/`skip` until the native readers land in **Phase 7**;
`py7zr`/`rarfile` enter earlier only as `dev`-group oracles. Those formats are
simply absent from the equivalence matrix until Phase 7.

---

## Phase 1 — Scaffold, spine, and the new test harness

**Goal:** an empty-but-correct skeleton — the target package shape, the spine
contracts (written fresh), the logging hierarchy, and the new declarative test
framework — all green, with no formats wired yet.

**Entry criteria:** fresh repo; `archivey-dev` cloned per `CLAUDE.md`.

### Tasks
1. **`pyproject.toml`** (clean slate): `hatchling`; `[project]` `archivey`,
   `0.2.0.dev0`, Python `>=3.11`; extras exactly per `packaging-and-extras/spec.md`
   (`[7z]`, `[rar]`, `[crypto]`, `[7z-write]`, `[iso]`, `[zstd]`, `[lz4]`, `[cli]`,
   `[seekable]`, `[recommended-lite]`, `[recommended]`, `[all]`); `dev`
   `[dependency-groups]` for tooling + oracles (`py7zr`, `rarfile`); `mypy`
   strict, `ruff`, `coverage`.
2. **Package layout:** `src/archivey/` with `internal/`, `formats/`, the public
   `__init__.py`. Establish the `archivey` **logger hierarchy** (no handlers).
3. **Spine, written fresh to the target contract** (types/ABCs in place even with
   no backends): the `BaseArchiveReader` ABC (ARCHITECTURE naming — `_iter_members`,
   `_iter_with_data`, `_open_member` with **no** `for_iteration`, **no**
   `_prepare_member_for_open`; `_SUPPORTS_RANDOM_ACCESS`/`_MEMBER_LIST_UPFRONT`
   class attributes); the backend registry + `Backend` ABC; the public-API
   skeleton (`open_archive`, `ArchiveReader` surface, `Member`/`ArchiveInfo`/
   `ArchiveFormat`/`MemberType`, the `ArchiveyError` hierarchy, `Intent`/
   `CostReceipt` types).
4. **New declarative test framework:** port the corpus (`sample_archives.py`,
   `ArchiveContents`, `FileInfo`, `ArchiveCreationInfo`) cleaned; `conftest.py`
   parametrization; **generate-on-demand + cache** to `$XDG_CACHE_HOME/archivey-tests/`
   keyed by `hash(spec + creation_params + lib versions)`; `tests/fixtures/` with a
   JSON sidecar per committed archive; **no generated binaries committed**; flat
   `tests/` layout. Clone DEV's suite into `tests/_dev_oracle/` as the frozen gate.

### Tests added
Harness self-tests (corpus round-trips through generation+cache); `__version__`
exposure; logging emits nothing by default.

### Acceptance — spec scenarios covered
- `packaging-and-extras`: *core install pulls no third-party packages*, *install
  rejected on unsupported Python*, *supported on all three operating systems*,
  *`__version__` reflects the installed distribution*.
- `backend-registry`: *core backend available without extras*, *optional backend
  absent at import* (registry exists; no format backends yet).
- `logging`: *library emits no output by default*.
- `testing-contract`: framework stands up (matrix harness importable; oracle hooks
  wired but skipped when libs absent).

**Gates:** `mypy --strict` clean; `ruff` clean; `pytest` green (mostly skips);
`git status` clean after a test run (no new binaries).

---

## Phase 2 — Stream layer (compressed + seekable)

**Goal:** the `internal/streams/` package and the shared codec layer exist, built
fresh with the good DEV primitives ported in.

**Entry criteria:** Phase 1 green.

### Tasks
1. **`internal/streams/`**: `detect.py` (`RecordableStream`, `RewindableStreamWrapper`),
   `slice.py` (`SlicingStream`), `compat.py` (`is_seekable`/`ensure_binaryio`/…
   plus a **simplified `BinaryIOWrapper`** — straightforward delegation, **no**
   `self.read = self._raw.read` method-swap), and ported `decompress.py`/`xz.py`/
   `lzip.py`. Keep `archive_stream.py`.
2. **`compressed-streams`**: the uniform pull-based codec layer — one default
   backend per codec, a single wrapped crypto (AES) stage, missing-backend →
   `PackageNotInstalledError`, decompression-error translation, optional
   digest-verification on full reads, and backend dispatch separable from opening.
3. **`seekable-decompressor-streams`**: XZ block-index and lzip trailer-scan random
   access; `rapidgzip`/`indexed_bzip2` accelerators behind `[seekable]` with clean
   absence behavior.

### Tests added
`compressed-streams` scenarios (default backends, raw LZMA2, crypto wrapper
reachability, missing-backend errors, corrupt/truncated translation, digest
mismatch/partial/unverifiable, resolve-without-open); `seekable-decompressor-streams`
scenarios (XZ/lzip seeking, accelerator present/absent).

### Acceptance — spec scenarios covered
All of `compressed-streams` and `seekable-decompressor-streams`.
**Gates:** mypy/ruff clean; new stream tests green; frozen oracle no worse.

---

## Phase 3 — Indexed leaf formats: ZIP, directory, single-file, ISO

**Goal:** the seekable/indexed leaf backends run on the spine ABC; format
detection covers them.

**Entry criteria:** Phase 2 green.

### Tasks
1. Port **ZIP**, **directory**, **single-file compressors**, and **ISO** backends
   onto the new ABC (interface-only changes). ISO namespace auto-selection
   (Rock Ridge → Joliet → plain) and optional `pycdlib` graceful degradation.
2. Port **format detection** magic table + extension fallback + conflict warning
   for these formats; non-seekable peek/replay shared by the opener.
3. Wire **CostReceipt** values for these formats; `archive-reading` random/by-name
   access on indexed sources.

### Tests added
`format-zip`, `format-directory`, `format-single-file-compressors`, `format-iso`
scenarios; `format-detection` scenarios for these formats; `backend-registry`
selection + *ISO without pycdlib* + *list_formats() excludes unavailable*;
`access-intent-and-cost` O(1)/AUTO/RANDOM scenarios for ZIP; equivalence matrix
seeded; non-seekable ZIP spooling. Retire matching frozen-oracle coverage.

### Acceptance — spec scenarios covered
`format-zip` (all), `format-directory` (all), `format-single-file-compressors`
(all read), `format-iso` (all), `format-detection` (ZIP/TAR magic, gzip-wrapping,
SFX, ISO extended peek, never-consumes-bytes), `backend-registry` (selection +
degradation).
**Gates:** mypy/ruff clean; named tests green.

---

## Phase 4 — TAR, sequential streaming, and safe extraction

**Goal:** TAR (all variants) reads; `stream_members()` bounded-memory streaming
works on a non-seekable source; `ExtractionCoordinator` replaces the deferred
state machine.

**Entry criteria:** Phase 3 green.

### Tasks
1. Port **TAR** (PAX, all variants); compressed-TAR detection (`tar.gz/bz2/xz`).
2. **`ExtractionCoordinator`** (written fresh — unified single ordered pass over
   `_iter_with_data()`; **no** `pending_*` dicts, **no** `can_move_file`, **no**
   `process_file_extracted`):
   - Pre-pass hardlink closure (random-access mode); during-pass FILE/DIR/HARDLINK/
     SYMLINK handling with symlink escape **re-validated at extraction time**; the
     only deferred work is an explicit O(skipped-sources) second pass for excluded
     hardlink targets.
   - Decompression-bomb limits (cumulative max bytes; per-member ratio; scoped to
     extraction paths only) and `on_progress` / per-member `ExtractionResult`.
3. Wire `extract()`/`extractall()` and the one-shot extraction API to the
   coordinator.

### Tests added
`format-tar` scenarios; `safe-extraction` scenarios (path-safety, symlink/hardlink,
policies, overwrite, bomb limits, progress/result); `archive-reading` sequential
+ `stream_members`; `testing-contract` non-seekable `tar.gz` + adversarial
(traversal, bomb). Retire matching frozen-oracle coverage.

### Acceptance — spec scenarios covered
`format-tar` (all), `safe-extraction` (all), `archive-reading` (*forward iteration*,
*materialization on sequential intent*, *streaming a solid archive*, *stream invalid
after advance*), `format-detection` (*gzip wrapping a tar/single file*),
`testing-contract` (*path traversal member*, *zip bomb extraction*, *non-seekable
TAR.GZ source*).
**Gates:** mypy/ruff clean; streaming extraction verified on a non-seekable TAR;
no `pending_*` attributes anywhere.

---

## Phase 5 — Public API finalization & cost surface

**Goal:** the public surface matches `SPEC.md` across every format built so far.

**Entry criteria:** Phase 4 green.

### Tasks
1. Finalize `archive-reading` (metadata access, membership/random access,
   `read`/`open`, transparent **link following** with depth limit, context-manager
   lifecycle).
2. Finalize `archive-data-model` (`ArchiveFormat`/`MemberType` taxonomy,
   compression-method model, the full `Member` record — hashable, `extra`, digests
   under algorithm keys, name normalization — and `ArchiveInfo`).
3. Finalize `access-intent-and-cost` — `Intent` enforcement and **CostReceipt
   values verified per format**; `error-handling` translation contract (cause/
   traceback preserved; genuine I/O not reclassified; context filled by base reader).

### Tests added
`archive-data-model`, `access-intent-and-cost`, `error-handling`, and the remaining
`archive-reading` scenarios; per-format CostReceipt assertions.

### Acceptance — spec scenarios covered
All of `archive-reading`, `archive-data-model`, `access-intent-and-cost`,
`error-handling`.
**Gates:** `mypy --strict` clean; public API matches `SPEC.md §2–§7`; CostReceipt
correct for every format implemented so far.

---

## Phase 6 — Writing support

**Goal:** `ArchiveWriter` ABC, ZIP + TAR writers, streaming conversion.

**Entry criteria:** Phase 5 green.

### Tasks
`ArchiveWriter` ABC (`add`/`add_bytes`/`add_stream`/`add_member`/`add_members`/
`close`); `ZipWriter` (`ZipFile.open(name,'w')`, data descriptor for unknown
size); `TarWriter`; `create_archive()`; `CompressionSpec` model.

### Tests added
`archive-writing` scenarios; `testing-contract` ZIP/TAR round-trip; conversion
(`tar.gz`→`zip`, `zip`→`tar`) with bounded memory verified via `tracemalloc`.

### Acceptance — spec scenarios covered
All of `archive-writing`; `testing-contract` (*ZIP round-trip*, *TAR round-trip*);
`format-zip` (*streaming write via data descriptor*).
**Gates:** mypy/ruff clean; no full-archive buffering during stream conversion.

---

## Phase 7 — Native 7z reader + native RAR metadata parser

**Goal:** make the 7z and RAR **read** paths native; flip them from `xfail` to
passing; wire the oracles. See `format-7z/spec.md`, `format-rar/spec.md`,
`testing-contract/spec.md`.

**Entry criteria:** Phase 6 green; `py7zr`/`rarfile`/`unrar` available as
dev-group oracles.

### Tasks
1. **Native 7z** header parse (packed streams, folders/coder chains, substreams,
   files info) + decode via stdlib `lzma`(raw)/`bz2`/`zlib` + STORED; true pull
   streaming for `stream_members()`, decode-from-folder-start for random `open()`;
   PPMd/Deflate64 via `[7z]`, AES via `[crypto]`; **BCJ2 and unknown method IDs
   rejected explicitly** (never silent fallback). 7z **writing** stays on `py7zr`
   behind `[7z-write]`; reads import no third-party lib.
2. **Native RAR** RAR4/RAR5 metadata parse (listing without `unrar`); member data
   via a single `unrar p -inul` pipe demultiplexed by header sizes with incremental
   CRC32; header-encrypted RAR5 decrypted via `[crypto]`; multi-volume joining.

### Tests added
`format-7z` + `format-rar` scenarios; `testing-contract` oracle cross-validation
(*native 7z matches py7zr*, *native RAR matches rarfile/unrar*, *unsupported 7z
codec rejected not guessed*) — skip when oracle absent. Retire the frozen-oracle
7z/RAR coverage.

### Acceptance — spec scenarios covered
All of `format-7z` and `format-rar`; the three cross-validation scenarios.
**Gates:** 7z/RAR reads import no third-party lib (stdlib + `unrar` only); native
output matches oracles across the corpus; solid `stream_members()` uses one
`unrar p` process; unsupported codecs raise the documented error.

---

## Phase 8 — Zstandard & extended compression

**Goal:** `.zst`/`.tar.zst` and `.tar.lz4` support.

### Tasks
Single-file `ZST` (`[zstd]`); `.tar.zst` and `w:zst`; `.tar.lz4` (`[lz4]`); `.zst`
magic in detection.

### Tests added
`format-single-file-compressors` ZST scenarios; `format-tar` `.tar.zst`/`.tar.lz4`;
`format-detection` zst magic — all skip when the optional lib is absent.

### Acceptance — spec scenarios covered
ZST/LZ4 paths of `format-single-file-compressors`, `format-tar`, `format-detection`.
**Gates:** mypy/ruff clean; tests skip cleanly without the extras.

---

## Phase 9 — CLI

**Goal:** the `archivey` command (`list`/`test`/`extract`, pattern filtering)
behind `[cli]`.

### Tests added & acceptance
All of `cli` (incl. *CLI installed without the `[cli]` extra*).
**Gates:** mypy/ruff clean.

---

## Phase 10 — Polish, packaging, and DEV-oracle retirement

**Goal:** `0.2.0` release-ready; the new test suite is the **sole** suite.

### Tasks
1. README, Google-style docstrings (`mkdocstrings`), `list_formats()`, CHANGELOG.
2. CI matrix (3.11–3.13; ubuntu + windows), generated-archive cache per Python
   version; coverage `fail_under = 90`.
3. **Complete the adversarial corpus** and confirm **every spec scenario across
   all capabilities is covered** by the new suite; then **delete
   `tests/_dev_oracle/`**. The frozen oracle is gone; DEV is reference-only.

### Acceptance — spec scenarios covered
`packaging-and-extras` (finalized — extras→capability, env matrix, version),
`cli`, and the full `testing-contract` (equivalence matrix across all formats,
adversarial corpus, round-trip, non-seekable coverage, oracle cross-validation).
**Gates:** coverage ≥ 90%; CI green on a fresh checkout (all archives generated
from scratch); `tests/_dev_oracle/` removed; no committed generated binaries.

---

## Cross-cutting concerns

### Risk areas
- **Spine-first ordering:** leaf backends in Phase 3+ attach to the Phase-1 ABC,
  so the ABC must be right the first time (it is written to `ARCHITECTURE.md`, not
  evolved from DEV). Mitigation: vertical slices — bring one backend fully green
  before the next, so ABC gaps surface early.
- **Hardlink edge cases in streaming mode:** TAR guarantees target-precedes-link;
  7z does not. `ExtractionCoordinator` is explicit per mode.
- **`BinaryIOWrapper` simplification:** benchmark the removed method-swap on a
  large-member read before committing to plain delegation.
- **Generated-archive cache invalidation:** key on archivey + library versions,
  not just the spec hash, to avoid stale archives after upgrades.
- **Oracle availability:** every oracle-backed test must *skip* (not fail) when the
  oracle lib/tool is absent, so CI without `unrar`/`7z` stays green.
