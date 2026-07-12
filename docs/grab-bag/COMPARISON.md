> **Grab-bag / historical.** DEV vs clean-slate comparison; some recommendations were later reversed. Index: [grab-bag/index.md](index.md).

# Archivey: Existing Implementation vs. Clean-Slate Proposal — Comparison and v2 Design

> **Context.** SPEC.md / ARCHITECTURE.md / PLAN.md in this repo were written as a clean-slate design from the high-level requirements, deliberately without looking at the existing implementation. This document compares that proposal against the real implementation at [`davitf/archivey-dev`](https://github.com/davitf/archivey-dev) (~10,150 lines of source, ~8,000 lines of tests, v0.1.0a4), and proposes how to combine the best of both into a new version with **no backwards-compatibility requirement**.
>
> Shorthand used below: **DEV** = the existing implementation; **CSP** = the clean-slate proposal in this repo.
>
> **Decision update (post-implementation).** This document originally recommended adopting
> CSP's `Intent` enum (AUTO/SEQUENTIAL/RANDOM) in place of DEV's `streaming=` bool — see the
> "access intent" rows in §2/§3 and §5. That recommendation was **reversed** during the v2
> build: `AUTO` does not actually auto-select (it is just another mode), and the model
> collapses to a real binary — random access vs. forward-only — plus a deferred performance
> hint. v2 therefore **keeps `streaming: bool`** (default `False` = random access, fails fast
> on a non-seekable source; `True` = forward-only single pass) and **drops the `Intent`
> enum**; the eager seek-point hint (the old `Intent.RANDOM`) may return later as an explicit
> opt-in. The `CostReceipt` and tri-state backend-config conclusions are unchanged. The
> authoritative spec is `openspec/specs/access-mode-and-cost/`. The remaining `Intent`
> mentions below are left as the historical comparison and as a description of CSP/DEV's own
> proposals — they do not describe the shipped v2 API.

---

## 1. Executive Summary

The two designs converged on most fundamentals — unified member model, magic-byte detection, safe-by-default extraction filters, translated exception hierarchy, streaming vs. random-access split, link resolution inside the archive. That convergence is itself useful evidence: those parts of the design are probably right.

Where they differ, **DEV is ahead on reading depth** and **CSP is ahead on scope and a few safety/ergonomics ideas**:

- DEV solved the solid-archive streaming problem *properly* — true bounded-memory streaming via a background thread + queue for 7z and a single `unrar p` pipe demultiplexer for RAR. CSP's per-block `SpooledTemporaryFile` caching is strictly worse and should be discarded.
- DEV has an entire subsystem CSP never conceived: **seekable decompressor streams** (XZ block index, lzip trailer scan, rapidgzip/indexed_bzip2 backends) giving random access *inside* compressed streams. (For the per-codec library choices behind this layer — including the native XZ parser and the zstd backend decision — see [`docs/internal/library-analysis.md`](../internal/library-analysis.md).)
- DEV's test harness (declarative sample archives × format matrix × backend configs ≈ 1,000+ effective cases) is far beyond CSP's plan.
- CSP has **writing + conversion**, which DEV lacks entirely, plus **decompression-bomb protection** (absent in DEV), and a crisper **cost/access-mode model** — which DEV's own in-progress `openspec` changes (`access-intent`, `base-reader-architecture-extensions`) independently arrived at but have not implemented.

**Recommendation in one line:** keep DEV's reading core, architecture, and test harness as the foundation; add CSP's writer layer, bomb protection, and cost/access-mode surface; take the API-shape cleanup (naming, frozen-ish member, composite format type already in DEV) as the one-time benefit of the compatibility break; and follow DEV's own native-reader roadmap (drop py7zr/rarfile) as the engine upgrade.

---

## 2. Scorecard

| Area | DEV | CSP | Winner / v2 direction |
|---|---|---|---|
| Read-side architecture (`BaseArchiveReader`) | Proven, deep, handles dup filenames, lazy registration, multi-iterator | Sketch-level ABC | **DEV** |
| Solid 7z streaming | Thread + bounded queue → true streams, O(1) memory | Per-folder SpooledTemporaryFile cache | **DEV** (discard CSP approach) |
| Solid RAR streaming | `unrar p` pipe demultiplexer + per-member CRC | One-shot `unrar x` to tmpdir | **DEV** (keep tmpdir as fallback only) |
| Random access in compressed streams | XZ/lzip native indexes, rapidgzip, indexed_bzip2 | Not conceived | **DEV** |
| Writing & conversion | Absent | Full design (`create()`, `add_members()`) | **CSP** |
| Bomb protection | Absent | `BombTracker` (total bytes + ratio) | **CSP** |
| Cost transparency | `has_random_access()` only; openspec proposal pending | `CostReceipt` at open | **CSP shape, DEV's openspec confirms** |
| Access intent | `streaming=` bool; openspec proposal pending | `Intent` enum (AUTO/SEQUENTIAL/RANDOM) | **DEV's `streaming: bool` kept** (Intent enum considered, then dropped — see note above) |
| Format type model | `ArchiveFormat(container, stream)` composite | Flat enum with TAR_GZ etc. | **DEV** (clearly better) |
| Member identity | `member_id` + `archive_id`, duplicate-filename support | Name-keyed lookup | **DEV** |
| Member dataclass | Mutable + zipfile-compat shims (`CRC`, `date_time`) | Frozen, no shims | **Mixed** (see §4.2) |
| Filters | tarfile-style callable transforms + named policies | Tiered enum policies + non-bypassable universal layer | **Mixed** (see §4.5) |
| Exceptions | `ArchiveError` tree, per-reader `_translate_exception` | Equivalent tree, `@translate_errors` decorator | Tie (merge naming) |
| Detection | Magic + compressed-tar probe + SFX + brotli probe | Magic table + extension fallback | **DEV** |
| Format quirk knowledge | RAR UTF-16 corruption fix, RAR5 encrypted CRC, ZIP 0x5455 timestamps, ISO namespaces | Documented in spec, not learned from production | **DEV** |
| Config system | `ArchiveyConfig` + contextvars, scoped overrides | Per-call kwargs only | **DEV** (with CSP trimming, §4.7) |
| Dependencies | Hard deps: tqdm, typing-extensions, backports-strenum | Zero-dep core | **CSP** |
| Test harness | Declarative archives, 200 fixtures, multi-backend matrix | Plan-level only | **DEV** |
| Documentation & process | mkdocs site, design docs, openspec live specs | Three markdown docs | **DEV** |
| CLI | `archivey` command (list/test/extract, patterns) | Not proposed | **DEV** |
| Native parsers (future) | Designed: native 7z reader & RAR metadata parser, key feasibility findings | Sketch (reuse py7zr header parser) | **DEV** (further along) |
| Progress reporting | tqdm (hard dependency) | Callback-based `on_progress` | **CSP** |

---

## 3. The Big Convergence (and what it validates)

Three of CSP's "novel" ideas turn out to already exist as in-progress DEV proposals (`openspec/changes/`):

1. **`access-intent`** — proposed replacing the `streaming` bool with a declared access intent (AUTO/SEQUENTIAL/...), exactly CSP's `Intent` enum, and making backend flags tri-state so AUTO can pick indexed backends to honor the intent. *(v2 ultimately kept the `streaming` bool and dropped the enum — see the note at the top — but adopted the tri-state-config and cost ideas.)*
2. **`base-reader-architecture-extensions`** — adds cost introspection (`member_access_cost`, `seek_cost`): CSP's `CostReceipt`, split across reader and stream.
3. **`sevenzip-native-reader` / `rar-native-metadata-reader`** — drop py7zr and rarfile in favor of native parsers. The 7z design contains the decisive feasibility finding: *stdlib `lzma` with `FORMAT_RAW` natively implements LZMA1/LZMA2 **and the entire BCJ branch-filter family and Delta***, so a native pull-based 7z decompressor needs almost no new codec code. This is stronger than CSP's version of the same idea (which assumed the `bcj` C extension was needed).

Independent convergence from two directions is good evidence these are the right calls. **v2 should commit to all three** (with the access-mode shape landing as `streaming: bool` rather than the `Intent` enum — see the note at the top).

---

## 4. Area-by-Area Comparison and v2 Decisions

### 4.1 Public API shape

**DEV:**
```python
with open_archive("a.7z", streaming=False, pwd=..., config=...) as ar:
    ar.get_members()                      # list, may scan
    ar.get_members_if_available()         # list | None, never scans
    ar.iter_members_with_streams(members=..., filter=...)  # (member, stream|None)
    ar.open(name_or_member, pwd=...)
    ar.extract(member, path), ar.extractall(path, members=..., filter=...)
    ar.has_random_access()
    ar.resolve_link(member)
open_compressed_stream("file.gz")         # decompressed BinaryIO
```

**CSP:**
```python
with archivey.open("a.7z", intent=Intent.AUTO) as ar:
    for m in ar: ...                      # __iter__ over members
    ar.members(); len(ar); "name" in ar; ar["name"]
    ar.stream_members()                   # (member, stream) bounded-memory
    ar.read(m); ar.open(m)
    ar.extract(...); ar.extract_all(...)
    ar.cost                               # CostReceipt
archivey.extract(src, dest)               # one-shot
archivey.detect_format(src)
```

**v2 decision — merge, leaning CSP for surface ergonomics, DEV for semantics:**

```python
archivey.open_archive(src, *, streaming=False, pwd=None, config=None, format=None) -> ArchiveReader
archivey.open_compressed_stream(src, ...) -> BinaryIO        # keep from DEV
archivey.extract(src, dest, ...)                             # one-shot, from CSP
archivey.detect_format(src) -> FormatInfo                    # from CSP

class ArchiveReader:
    def __iter__(self) -> Iterator[ArchiveMember]            # CSP: pythonic iteration
    def members(self) -> list[ArchiveMember]                 # rename of get_members()
    def members_if_available(self) -> list[ArchiveMember] | None
    def stream_members(self, members=None, *, filter=None) -> Iterator[tuple[ArchiveMember, BinaryIO | None]]
                                                              # rename of iter_members_with_streams()
    def open(self, member, *, pwd=None) -> BinaryIO
    def read(self, member, *, pwd=None) -> bytes              # CSP convenience
    def extract(self, member, path, ...) / extract_all(path, ...)
    def get(self, name) / __getitem__ / __contains__          # CSP conveniences
    def resolve_link(self, member)                            # DEV
    @property info: ArchiveInfo                               # includes CostReceipt
```

Rationale for keeping `open_archive` over CSP's `archivey.open`: it never shadows the builtin in `from archivey import *` contexts and is established. Everything else gets the shorter CSP names — the compat break is the moment to do this. `stream_members()` yielding `None` for non-file members (DEV semantics) is kept: it's explicit and avoids fake empty streams.

### 4.2 The member model

**DEV's `ArchiveMember`** is a mutable dataclass with: normalized `filename`, `raw_filename`, `file_size`, `compress_size`, `mtime_with_tz` (+ `.mtime` naive property), `atime`/`ctime`, `mode`/`uid`/`gid`/`uname`/`gname`, `crc32`, `compression_method: str`, `comment`, `create_system`, `windows_attrs`, `encrypted`, `extra`, `link_target`, `raw_info`, `member_id`/`archive_id`, plus zipfile-compat shims (`CRC`, `date_time`, `is_file`/`is_dir`/`is_link` properties) and `replace()` for filters.

**CSP's `Member`** is frozen, with `sequence`, `name`/`original_name`, sizes, three timestamps, ownership, `compression: tuple[CompressionMethod, ...]` (structured chain), `is_encrypted`, `is_sparse`, `crc32`, `extra` (hash-excluded).

**v2 decision:**

- **Keep DEV's field inventory** — it's richer and production-tested (`windows_attrs`, `create_system`, `comment`, `raw_info` all earn their place; CSP's `is_sparse` joins them).
- **Keep `member_id` + `archive_id`** — duplicate filenames are real (TAR, 7z) and CSP's name-keyed model mishandles them. DEV's hardlink resolution ("most recent member with that name and a lower `member_id`") is the correct semantic.
- **Adopt the structured compression chain** from CSP (`tuple[CompressionMethod, ...]`) replacing `compression_method: str` — DEV's own openspec (§8.B of `base-reader-architecture-extensions`) already wants this.
- **Naming cleanup (compat break):** `mtime_with_tz` → `mtime` (tz-aware-when-known semantics, documented), drop the naive-`mtime` property, drop `CRC`/`date_time` zipfile shims. Compatibility-with-stdlib was a v1 virtue; a clean v2 API matters more, and the shims cost surface area forever.
- **Mutability: keep mutable, but discipline it.** CSP wanted frozen for thread-safety/hashability, but DEV has two legitimate late-mutation points: `link_target` backfill during iteration (7z/RAR store targets in member *data*, sometimes encrypted) and filter edits. Fighting that with frozen+rebuild machinery isn't worth it. Instead: document that members are owned by the reader until iteration yields them, keep `replace()` as the filter mechanism, and exclude `extra`/`raw_info` from any equality.

### 4.3 Format model

DEV's `ArchiveFormat = (ContainerFormat, StreamFormat)` composite — where `TAR_GZ = (TAR, GZIP)` and plain `.gz` = `(RAW_STREAM, GZIP)` — is simply better than CSP's flat enum: it makes "compressed tar" a derived fact rather than an enum explosion and gives `open_compressed_stream` a natural type. **Adopt unchanged.** CSP's `FormatInfo` (detection confidence + how detected) is a useful addition on top.

DEV also supports more stream formats than CSP planned: lz4, lzip, zlib, brotli, Unix compress. Keep them all.

### 4.4 Solid-archive handling (the heart of the comparison)

This was the focus of much of CSP's revision history, and DEV's answer is better than where CSP landed:

| | DEV | CSP final |
|---|---|---|
| 7z iteration | py7zr `extract()` runs in a **background thread**; a custom `Py7zIO` pushes chunks into a **bounded queue (64 chunks)** per file; main thread consumes as true streams (`StreamingFile`/`StreamingFactory`, `sevenzip_reader.py`) | One `extract()` per folder into `SpooledTemporaryFile`s, yield then release |
| Memory | O(queue bound) regardless of file/block size | O(largest solid block) — spills to disk |
| 7z random `open()` | Single-member extract via factory | Per-folder cache (monotonic growth) |
| RAR solid iteration | **`unrar p -inul` single subprocess**, stdout demultiplexed by member sizes with incremental CRC32 validation (`RarStreamMemberFile`), opt-in via `use_rar_stream` | `unrar x` once to tmpdir, stream from disk |
| RAR caveats | Pipe approach needs path (not stream) + sizes known; falls back to rarfile | tmpdir needs disk = uncompressed size |

**v2 decision:** DEV's mechanisms win on memory profile and elegance. Specifically:

- 7z: keep thread+queue streaming for iteration. When the **native 7z reader** lands (pull-based decompression via stdlib `lzma`), the thread apparatus disappears entirely — that's the endgame; the thread+queue is the interim.
- RAR: make the `unrar p` pipe the **default** for solid-archive iteration (DEV hides it behind `use_rar_stream=False` today), with the rarfile per-member path for random access and as fallback. CSP's tmpdir one-shot remains a useful third strategy when the caller wants `extract_all` anyway — which is exactly what it is: extraction, not iteration.
- Keep CSP's contract language: *sequential iteration of a solid archive must cost O(1) decompression passes* — as a spec-level requirement (DEV's openspec test-harness spec can encode it).

One CSP idea worth keeping despite all this: the **two memory profiles** distinction (monotonic `open()` cache vs. bounded `stream_members()`) should be documented in the cost receipt rather than left implicit.

### 4.5 Extraction safety

- **Filters.** DEV mirrors tarfile (`fully_trusted` / `tar` / `data` named policies + arbitrary callable transforms receiving `(member, dest_path)`). CSP had enum tiers plus a **non-bypassable universal layer** (path traversal/absolute paths rejected even under TRUSTED). v2: **keep DEV's model** (familiarity + transform flexibility beats CSP's rigid tiers), but adopt one CSP hardening: the *extraction writer itself* re-validates that every resolved output path stays inside the destination unless the caller passes an explicit `allow_outside_dest=True`. Filters sanitize members; the writer enforces the boundary. Defense in depth, and `fully_trusted` keeps its tarfile meaning.
- **Bomb protection — adopt from CSP, DEV has nothing.** `BombTracker` (cumulative `max_extracted_bytes`, per-member `max_ratio`) wired into `extract_all` and (new) optionally into member streams. Defaults per CSP (2 GiB / 1000:1), overridable in config.
- **Overwrite handling.** Same three modes both sides (`OVERWRITE/SKIP/ERROR`); DEV puts it in config, CSP per-call. v2: per-call parameter with config default — both.
- **Pending-link machinery.** DEV's `ExtractionHelper` (deferred hardlinks for solid archives, link-to-self handling, cross-device fallback to copy, metadata application) is more battle-tested than CSP's two-pass sketch. Keep DEV's.

### 4.6 Errors

Near-identical trees. v2 merges: DEV's class names are fine; add CSP's `__cause__`-preservation requirement as an explicit spec rule (DEV already does it in practice via `run_with_exception_translation`); keep DEV's per-reader `_translate_exception()` hook, which is more flexible than CSP's decorator. One addition from CSP: a dedicated `FilterRejectionError` subtree (DEV's single `ArchiveFilterError` is coarser) — useful for programmatic handling of *why* extraction was blocked (traversal vs. special file vs. link escape).

### 4.7 Configuration

DEV's contextvars-based `ArchiveyConfig` with scoped `archivey_config(...)` overrides is genuinely good (thread- and async-safe, testable). Its weakness — acknowledged by DEV's own `access-intent` proposal — is that backend flags (`use_rapidgzip`, `use_indexed_bzip2`, `use_python_xz`, ...) leak the cost model: you must know the trick to get cheap seeking on `.tar.gz`.

**v2:** keep the config system; convert backend flags to tri-state (`AUTO`/`ON`/`OFF`) resolved against the caller's access mode (the `streaming` flag); fold in CSP's bomb limits and overwrite default. Drop `tqdm` as a hard dependency — progress becomes a callback (CSP), with tqdm used only by the CLI (and listed in its extra).

### 4.8 Writing and conversion — the big addition

DEV is read-only. CSP's writer design transfers almost unchanged, and DEV's reading internals make it stronger:

- `ArchiveWriter` ABC: `add(path)`, `add_bytes`, `add_stream`, `add_member(member, stream)`, `add_members(reader)` — the conversion primitive that pipes `reader.stream_members()` into the writer, automatically getting solid-archive-efficient single-pass reads.
- Backends: ZIP + TAR via stdlib (ZIP using `ZipFile.open(name,'w')` streaming write), single-file compressors, 7z via py7zr (writing keeps py7zr even after the native *reader* replaces it for reads — asymmetry is fine, and py7zr write moves to the `[7z-write]` extra).
- DEV's `ArchiveMember` (with `raw_info`, `member_id`) flows through conversion naturally; the writer maps fields per target format and warns on unsupported member types.
- The composite `ArchiveFormat` answers "what does `create()` take" cleanly: `(TAR, ZSTD)` etc.

This is the single largest piece of new work in v2 (CSP's PLAN.md Phase 5 remains a good breakdown).

### 4.9 Detection

DEV is ahead: magic table *plus* compressed-tar probing (decompress a sample to find tar inside gz/bz2/xz/zstd — disambiguates `.tar.gz` from `.gz`), brotli trial decompression (brotli has no magic), SFX archive detection (executables with embedded RAR/7z), filename fallback with mismatch warnings. CSP adds two things worth keeping: the **`FormatInfo` result type** (confidence + method) and the explicit **bounded-read guarantee** with `PeekableStream`-style non-seekable handling — DEV achieves stream preservation via `RewindableStreamWrapper`, which is equivalent machinery; spec the guarantee, keep DEV's implementation.

### 4.10 Testing

DEV's harness is the keeper, nearly wholesale: declarative `ArchiveContents`/`FileInfo` sample definitions, generation via both libraries *and* CLI tools (catches library-vs-tool disagreements), ~200 committed fixtures, the `sample_archives` pytest marker with per-config parametrization (default / alternative backends / rar-stream), corruption generators, symlink-loop and duplicate-filename archives.

From CSP, add: the **adversarial corpus** as a named first-class suite (zip bombs and ratio attacks are missing from DEV — they become testable once `BombTracker` exists), and **round-trip tests** (create → read → compare) once writing lands. CSP's "equivalence matrix" idea already exists in DEV in better form (`ArchiveFormatFeatures` flags encode per-format expected limitations).

### 4.11 Platform & packaging

- **Python floor:** DEV supports 3.10 (with `backports-strenum`); CSP said 3.11. v2: **3.11+** — drops two backport deps; 3.10 is EOL October 2026 anyway.
- **Free-threaded Python:** DEV already maintains an `optional-freethreaded` extra and thread-safety locks (`_registration_lock`, class-level password lock). Preserve this — it's rare foresight, and the thread+queue 7z reader depends on it being right.
- **Core deps:** v2 target = zero hard deps (drop tqdm/typing-extensions/backports). Extras: `[7z]`, `[rar]`, `[iso]`, per-codec extras, `[cli]` (tqdm), `[all]`.
- **Tooling:** DEV uses ruff + pyright + pytest-parallel + tox + mkdocs-material; CSP suggested mypy. v2 keeps ruff + pytest + mkdocs but **type-checks with Pyrefly + ty** (kept clean on both; no mypy, no pyright) and runs a **reduced ~12-job GitHub Actions matrix** (Python 3.11–3.14) in place of DEV's ~18 tox envs (see `PLAN.md` Phase 1 / `openspec/changes/phase-1…`).

### 4.12 Things only one side thought of (keep all)

From DEV:
- `open_compressed_stream()` as a public single-stream API.
- Seekable decompressor streams + backward index scans (XZ stream footers, lzip trailers) — enables cheap "extract last member of a 10 GB tar.xz".
- RAR 2.9–4 UTF-16 filename corruption repair; RAR5 encrypted-CRC verification (PBKDF2 conversion).
- Junction-point unification (`link_target_type`, `is_junction`) per the `unify-junction-handling` proposal.
- The CLI (list/test/extract with CRC+SHA256 verification, fnmatch patterns, `--track-io`).
- `StatsIO` I/O instrumentation, `ErrorIOStream`, the io_helpers toolbox.
- Per-format limitation design docs (tar/zip stdlib limitations, pycdlib analysis) and the openspec live-spec process itself.

From CSP:
- One-shot `archivey.extract()` top-level function.
- `read()` convenience on the reader.
- Decompression-bomb limits.
- `FormatInfo` detection result.
- Writer/conversion layer.
- Cost receipt as a single queryable object (`ar.info.cost`) rather than scattered booleans.
- Documented sample-usage patterns as spec sections with test obligations (hash-all-files, open-through-link, convert-with-filter).

---

## 5. Proposed Shape of v2

### 5.1 What v2 is

A read **and write** archive library, built on DEV's reading core, with:

1. **DEV's architecture preserved:** `BaseArchiveReader` registration/lazy-iteration model, member identity (`member_id`), link resolution, `ExtractionHelper`, io_helpers, seekable decompressor streams, detection pipeline, test harness, CLI, config system.
2. **The converged openspec conclusions implemented:** the `streaming: bool` access mode kept (the `Intent` enum was considered and dropped — see the note at the top); `CostReceipt` exposed as one object on `ArchiveInfo` (listing cost / member access cost / seek cost / solid block info); tri-state backend config resolved from the access mode.
3. **CSP's additions:** writer + conversion layer; bomb protection; one-shot `extract()`; `FormatInfo`; structured compression chains; progress callbacks.
4. **The compat-break cleanups:** method renames (`members()`, `stream_members()`), `mtime` tz-aware rename, zipfile-shim removal, Python 3.11+, zero hard deps, `FilterRejectionError` subtree, dest-boundary enforcement in the extraction writer.
5. **DEV's native-reader roadmap as the engine plan:** native 7z reader (deletes the thread/queue apparatus, py7zr stays for writing only), native RAR metadata parser (drops rarfile; unrar remains the decompressor). These were designed for v1 but a no-compat v2 is the cheapest moment to land them.

### 5.2 What v2 explicitly drops

- `streaming_only` parameter (folded into the single `streaming` bool; the `Intent` enum that would have subsumed it was dropped — see the note at the top).
- `mtime_with_tz` naming, `CRC`/`date_time` shims, `get_*` method-name prefixes.
- tqdm/typing-extensions/backports-strenum as hard dependencies; Python 3.10.
- CSP's per-folder SpooledTemporaryFile 7z caching and `unrar x`-to-tmpdir as *primary* strategies (the latter survives inside `extract_all`).
- CSP's frozen `Member` and its flat `ArchiveFormat` enum.
- CSP's non-bypassable filter floor in its original form (replaced by writer-level dest enforcement with explicit opt-out).

### 5.3 Suggested sequencing

| Phase | Content | Builds on |
|---|---|---|
| 1 | Fork DEV core; apply renames + `streaming` access mode / `CostReceipt` / tri-state config (the converged openspec conclusions); Python 3.11 floor; drop hard deps | DEV + openspec |
| 2 | Bomb protection + `FilterRejectionError` subtree + writer-level dest enforcement; adversarial test corpus | CSP §7, DEV harness |
| 3 | Writer layer: TAR + ZIP + single-file; `add_members()` conversion; round-trip tests | CSP Phase 5 |
| 4 | One-shot `extract()`, `read()`, `FormatInfo`, structured compression chain, progress callbacks | CSP |
| 5 | Native RAR metadata parser (drop rarfile) | DEV openspec |
| 6 | Native 7z reader (drop py7zr for reads; delete thread/queue machinery); 7z writing via py7zr `[7z-write]` | DEV openspec |
| 7 | Junction unification, `link_target_type`, public stream interface (`seek_cost` on all returned streams) | DEV openspec |
| 8 | Docs site consolidation; benchmark suite extension (solid-archive iteration, conversion throughput) | DEV |

Phases 1–4 produce a releasable v2.0 with the full new surface; 5–7 are engine upgrades behind it.

---

## 6. Open Questions for v2

1. **Repo strategy:** evolve `archivey-dev` in place behind a major version, or develop v2 here (`archivey-2`) and merge back? The openspec live-spec corpus argues for staying in-tree; the no-compat break argues for a branch.
2. **`open_archive` vs `open`:** §4.1 recommends keeping `open_archive`; confirm.
3. **Writer for solid formats:** should v2.0 write 7z at all (py7zr's writer is push-friendly so it's easy), or ship TAR/ZIP/single-file first and add 7z write in 2.1?
4. **`stream_members()` for non-file members:** DEV yields `(member, None)`; should links yield a stream of the *target's* data instead when `follow_links=True` is passed? (CSP's link-following `open()` semantics, extended to iteration.)
5. **Bomb limits as config vs. per-call:** proposal says both (config default, per-call override) — confirm the default values (2 GiB / 1000:1 from CSP, or higher).
6. **CLI scope in v2.0:** carry DEV's CLI as-is initially, or extend with `create`/`convert` subcommands once the writer exists? (Latter is a natural showcase.)
7. **`pwd` vs `password` parameter name:** DEV uses `pwd` (zipfile heritage); with shims gone, `password` is clearer. Trivial but decide once.
