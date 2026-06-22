# Phase 3: Indexed leaf formats (ZIP, TAR read, single-file, ISO) + detection

## Why

Phases 1–2 gave us the spine (the `BaseArchiveReader` ABC, the backend registry,
the data model) and the stream layer (`compressed-streams` +
`seekable-decompressor-streams`). Phase 3 is the first phase that attaches **real
seekable/indexed leaf backends** to that spine and makes `open_archive()` route to
them by detected format:

- **ZIP** (stdlib `zipfile`), **TAR read** (stdlib `tarfile`, random-access mode +
  compressed-tar), **single-file compressors**, and **ISO** (`pycdlib`) backends,
  ported onto the new ABC (interface-only; the directory backend already landed in
  Phase 1). TAR's forward-only streaming (`stream_members`) and the
  `ExtractionCoordinator` / safe-extraction stay in Phase 4 — only the reader and
  compressed-tar detection land here (decided: pulled forward so the two stdlib
  formats and the inner-TAR detection result cohere in one phase).
- **`format-detection`** — the central magic table (declared as *data* by each
  backend), extension fallback, conflict warning, the inner-TAR / Brotli content
  probes, the ISO extended-peek window, and the new **`PeekableStream`** the opener
  wraps non-seekable sources in (replacing DEV's `RecordableStream` /
  `RewindableStreamWrapper`).
- **`backend-registry`** selection + a reworked **availability model** (see below).
- **`access-mode-and-cost`** — real `CostReceipt` values wired per format.

Two design questions surfaced while scoping this phase and are **resolved here**, so
the work is plannable as concrete tasks:

1. **Single-file is one backend, not one-per-format.** Nothing in the specs requires
   a backend class per compressor; the ABC's `FORMATS` is a tuple and the codec layer
   already dispatches per codec. We build a single multi-format `SingleFileBackend`
   with small per-codec metadata hooks (mirroring DEV's gzip / xz-lzip branches), so a
   new standalone codec becomes readable by adding a codec + enum + detection entry —
   no new backend code. (Spec delta: `format-single-file-compressors`.)

2. **Format support is tri-state and compositional.** "Supported = every optional lib
   installed" is too coarse for per-member multi-codec containers: a 7z that uses only
   LZMA2 reads with zero extras, while a 7z member using PPMd needs `[7z]`. We
   introduce **FULL / PARTIAL / NONE** support, computed compositionally across the
   *format backend* and the *codec backends* a format can use, and expose it via an
   availability query. (Spec delta: `backend-registry`.)

## What Changes

### Format backends (ported onto the Phase-1 ABC)

- **ZIP** (`formats/zip_reader.py`): stdlib `zipfile`; central-directory listing
  (O(1) / `INDEXED`), `DIRECT` random access, `SEEKABLE`. `ZipInfo` → `ArchiveMember`
  field mapping per `format-zip` (mode from `external_attr>>16`, NT-timestamp
  precedence, symlink/dir typing, `is_encrypted` from `flag_bits & 0x1`).
  Multi-volume (split/spanned) ZIPs raise `UnsupportedFeatureError`. **Non-seekable
  ZIP fails fast** with `StreamNotSeekableError` (no implicit spooling — decided; an
  explicit `spool_max_size` opt-in is left for a future change).
- **TAR — read only** (`formats/tar_reader.py`): stdlib `tarfile`; random-access
  reading on a seekable source (scan-and-index → `INDEXED` listing, `DIRECT` access)
  and the compressed-tar combinations (`tar.gz`/`tar.bz2`/`tar.xz`/`tar.lzip`, …) via
  the codec layer. PAX/GNU/ustar variant mapping to `ArchiveMember`. **Out of scope
  here (Phase 4):** forward-only `stream_members()` on a non-seekable `tar.gz`, and the
  `ExtractionCoordinator` / safe-extraction. This is the *only* container that composes
  with stream compressors (see the seek-heavy-container note under ISO).
- **Single-file compressors** (`formats/single_file_reader.py`): **one**
  `SingleFileBackend` whose `FORMATS` is the standalone-codec set
  (gz/bz2/xz/lzip/zlib/brotli + unix-compress this phase; **zst/lz4 deferred to
  Phase 8**; needs `StreamFormat`/`ArchiveFormat` enum extensions — see Specs). One
  `FILE` member, name inferred from the source filename (strip known ext / append
  `.uncompressed` / default `"data"`), per-codec metadata hooks (gzip `FNAME` →
  `extra["gzip.original_filename"]` decoded + `raw_name` undecoded; xz/zst header size;
  lz4 frame size; lzip trailer size; gz size always `None`; bz2/zlib/br/Z size known
  only after full read). `DIRECT` access cost (one member — no inter-member
  dependency); whether the member *stream* can seek is a stream-level property, not the
  archive `CostReceipt`. A non-seekable source raises only for unix-compress (`.Z`,
  which needs random access); other single-file formats read fine non-seekable.
- **ISO** (`formats/iso_reader.py`): `pycdlib` behind `[iso]`; richest-namespace
  auto-select (Rock Ridge > Joliet > plain) reported in
  `ArchiveInfo.extra["iso.namespace"]`; namespace-dependent metadata fidelity;
  `DIRECT`, requires seek; write attempts raise `UnsupportedOperationError`. The
  raw-`.bin` sector-stripping wrapper is **lower-priority / deferred** (the spec marks
  it MAY-drop).

  > **Seek-heavy containers are not mounted over a compressor (decided).** Compressed-
  > container composition is reserved for *sequentially-read* containers (TAR). A
  > seek-heavy container behind a stream compressor — `.iso.xz`, `.iso.gz`, `.zip.xz` —
  > is treated as a **single-file compressor** whose one member is the inner image, not
  > mounted in place: `pycdlib`/`zipfile` seek all over the payload, and layering that on
  > a seekable decompressor means pathological re-decompression for no real-world
  > workflow (compressed ISOs are decompressed before mounting). This matches detection
  > probing for an inner **TAR only**. Revisit if a native streaming ISO reader ever
  > lands.

### Format detection + `PeekableStream`

- `detect_format()` and `FormatInfo` per `format-detection`: magic-first
  (`CERTAIN`) → extension fallback (`GUESS`); magic/extension conflict logs a
  WARNING on `archivey.detection`; inner-TAR probe (`tar.gz`/`tar.bz2`/`tar.xz`/…);
  Brotli content probe (`PROBABLE`); ISO extended 32 774-byte window; never consumes
  bytes.
- Each `ReadBackend` declares `MAGIC` / `EXTENSIONS` as **data**; the detector
  aggregates and matches — backends carry no `detect()` logic.
- **`PeekableStream`** (new primitive): buffers the first `DETECTION_LIMIT` bytes
  (4 096; 32 774 when ISO is in play), exposes `.peek(n)`, replays-then-passes-through
  on read. Constructed by the opener for non-seekable sources and shared with both
  detection and the backend. (Seekable sources just `seek(0)` after detection.)
- The **inner-TAR probe is fully realized here** (not a dead end) because the TAR
  reader lands in this phase — a `.tar.gz` detects as `TAR_GZ` *and* opens.
- **SFX (EXE-stub) detection is deferred to Phase 7**, alongside the native 7z/RAR
  readers that would actually open such payloads. Detecting a format we cannot yet
  open would be a dead end this phase.

### Backend registry: selection + tri-state, compositional availability

- **All known backends register at import time** (no longer "only when the optional
  dependency imports"). Optional backends declare their `OPTIONAL_DEPENDENCY` as data
  and the registry derives availability centrally from the module-or-`None` sentinel
  (`_optional(dep)`, the existing codec-layer idiom) — no per-backend boolean — rather
  than silently not registering. This is what lets the registry produce the install-hint
  error the spec already promises.
- **`FormatSupport` = FULL / PARTIAL / NONE**, computed compositionally over the
  format backend **and** the codec backends a format can use:
  - *FULL* — format backend usable and every optional codec/tool it can use is present.
  - *PARTIAL* — opens and lists; common members decode, but some optional codec/tool is
    missing (7z without `[7z]`/`[crypto]`; ZIP with only stdlib codecs; RAR without the
    `unrar` binary → listing-only). A member needing the missing piece raises
    `PackageNotInstalledError` at read time (existing `compressed-streams` behavior).
  - *NONE* — the backend (or a single-codec format's sole codec) is unavailable: ISO
    without `pycdlib`, `.zst` without `zstandard`.
- **Missing-dependency gaps ≠ by-design rejections.** 7z **BCJ2** and unknown method
  IDs are rejected by design (`UnsupportedFeatureError`), independent of what's
  installed; they never count against FULL.
- Query surface: `format_availability(fmt)` → support level + the missing
  components (package/extra/tool + install hint + which member-codecs each unlocks);
  `list_supported_formats()` → **FULL ∪ PARTIAL** (matches the spec already listing
  7z/RAR without extras); `list_known_formats()` → the full universe.
- **Format backends stay 1:1 for v1.** Multiple *format* backends per format (e.g. a
  future native ZIP reader beside stdlib `zipfile`) is recorded as an explicit future
  extension, not built now — the only present multi-implementation case lives at the
  codec layer (default vs `rapidgzip`/`indexed_bzip2`), where open-time selection
  already exists (`AcceleratorMode`).

### Cost surface

- Wire `CostReceipt` (`ListingCost` / `AccessCost` / `StreamCapability`) values for
  ZIP, TAR (random-access read), single-file (default + seekable-codec-lowered), and
  ISO; random / by-name access on these indexed sources.

## Implementation stages

This is a large phase, so it is implemented and reviewed in **four mergeable stages**
(it stays a single OpenSpec change — the proposal and spec deltas here cover all four).
Only Stage 1 touches the shared machinery; the rest are additive backends, so reviews
get smaller as they go. Each stage ends green (pyrefly + ty + ruff + its new tests).
See `tasks.md` → "Stage map" for the per-task breakdown.

1. **Foundation + ZIP** — `PeekableStream`, `detect_format` core (magic + extension +
   conflict), the registry rework (always-register + tri-state availability + query
   API), `open_archive()` routing + `CostReceipt`, and the **ZIP** backend end-to-end.
   The vertical slice that proves detect→select→open→read→cost on one format and
   surfaces any ABC/detection/registry gaps early. Exercises FULL + PARTIAL support.
2. **Single-file compressors** — the one multi-format `SingleFileBackend` + per-codec
   metadata hooks, plus the Brotli content probe in detection.
3. **TAR (read)** — the `tarfile` random-access reader + compressed-tar, plus the
   inner-TAR detection probe (now openable).
4. **ISO + degradation** — the `pycdlib` backend, the ISO extended-peek window, and the
   optional-dependency graceful degradation exercised end-to-end (NONE support + hint).

Stages 2 and 3 are swappable; Stage 1 is the only hard prerequisite.

## Specs

This change carries **spec deltas** (it modifies behavior the specs describe):

- **`backend-registry`** (MODIFIED + ADDED): registration becomes "always register;
  mark availability"; new tri-state, compositional `FormatSupport` and the
  availability query surface; degradation reworded against the tri-state; and the
  `MAGIC`/`EXTENSIONS` declarations carry the `ArchiveFormat` each signal implies, so a
  multi-format backend (single-file, TAR) maps each magic/extension to its format.
- **`format-single-file-compressors`** (ADDED + MODIFIED): a single multi-format
  `SingleFileBackend` with per-codec metadata hooks is the prescribed structure; new
  standalone codecs become readable without new backend code; the gzip stored filename
  is surfaced via `extra["gzip.original_filename"]` + `raw_name` (there is no
  `raw_filename` field); access cost is `DIRECT` (one member), superseding the earlier
  `SOLID`-and-lowered framing.
- **`archive-data-model`** (MODIFIED): extend `StreamFormat` with `LZIP`/`ZLIB`/
  `BROTLI`/`UNIX_COMPRESS` and add named standalone `ArchiveFormat` constants
  (`LZIP`/`ZLIB`/`BROTLI`/`Z`); uncommon container×codec combos are built on demand,
  not predefined. Also clarify `raw_name` as *exactly what the archive stored* (encoded,
  pre-normalization) — for gzip that is the `FNAME` bytes, even though `name` is derived
  from the source filename.

It **implements** (no delta) already-written specs: `format-zip`, `format-iso`,
`format-detection`, the **random-access read** parts of `format-tar` (its
`stream_members` / sequential scenarios land in Phase 4), and the indexed-access parts
of `access-mode-and-cost`. The `format-zip` "reconcile non-seekable spooling" note is
resolved in favor of fail-fast (recorded above); the `format-detection` SFX requirement
is realized in Phase 7, not here.

## Impact

- **Depends on:** Phase 2 (codec + seekable stream layer green).
- **Affected code:** new `formats/zip_reader.py`, `formats/tar_reader.py`,
  `formats/single_file_reader.py`, `formats/iso_reader.py`; new
  `internal/streams/peekable.py` (`PeekableStream`); `internal/detection.py`
  (`detect_format`, magic aggregation); `internal/registry.py` (always-register +
  availability); the public API (`detect_format`, `list_supported_formats`,
  `list_known_formats`, `format_availability`); `ARCHITECTURE.md` (module tree +
  detection/registry notes).
- **Tests:** `format-zip`, `format-tar` (random-access read + compressed-tar),
  `format-single-file-compressors` (this-phase codecs), `format-iso`,
  `format-detection` scenarios; `backend-registry` selection + tri-state availability +
  ISO-without-pycdlib + `list_supported_formats()` excludes NONE; `access-mode-and-cost`
  indexed/random scenarios for ZIP/TAR; non-seekable ZIP fail-fast. Retire the matching
  frozen-oracle coverage as it transfers.
- **Deferred (recorded, not built here):** TAR forward-only `stream_members()` +
  `ExtractionCoordinator` / safe-extraction (Phase 4), SFX detection (Phase 7), ZST/LZ4
  single-file (Phase 8), ISO raw-`.bin` sector stripping (optional/MAY-drop), mounting
  compressed seek-heavy containers (`.iso.xz`/`.zip.xz` — single-file-wrapped instead),
  multiple *format* backends per format, explicit ZIP `spool_max_size` opt-in.
- **Risk:** `PeekableStream` is the one fresh primitive (not a port) — get the
  peek/replay/position semantics right against the `format-detection` scenarios;
  detection must restore stream position after every probe.
