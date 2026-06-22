# Phase 3: Indexed leaf formats (ZIP, single-file, ISO) + detection

## Why

Phases 1–2 gave us the spine (the `BaseArchiveReader` ABC, the backend registry,
the data model) and the stream layer (`compressed-streams` +
`seekable-decompressor-streams`). Phase 3 is the first phase that attaches **real
seekable/indexed leaf backends** to that spine and makes `open_archive()` route to
them by detected format:

- **ZIP** (stdlib `zipfile`), **single-file compressors**, and **ISO** (`pycdlib`)
  backends, ported onto the new ABC (interface-only; the directory backend already
  landed in Phase 1).
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
- **Single-file compressors** (`formats/single_file_reader.py`): **one**
  `SingleFileBackend` whose `FORMATS` is the standalone-codec set
  (gz/bz2/xz/lzip/zlib/brotli + unix-compress this phase; **zst/lz4 deferred to
  Phase 8**). One `FILE` member, name inferred from the source filename (strip known
  ext / append `.uncompressed` / default `"data"`), per-codec metadata hooks
  (gzip `FNAME`→`raw_filename`; xz/zst header size; lz4 frame size; lzip trailer size;
  gz size always `None`; bz2/zlib/br/Z size known only after full read). `SOLID`
  default access, lowered when a seekable codec backend is active.
- **ISO** (`formats/iso_reader.py`): `pycdlib` behind `[iso]`; richest-namespace
  auto-select (Rock Ridge > Joliet > plain) reported in
  `ArchiveInfo.extra["iso.namespace"]`; namespace-dependent metadata fidelity;
  `DIRECT`, requires seek; write attempts raise `UnsupportedOperationError`. The
  raw-`.bin` sector-stripping wrapper is **lower-priority / deferred** (the spec marks
  it MAY-drop).

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
- **SFX (EXE-stub) detection is deferred to Phase 7**, alongside the native 7z/RAR
  readers that would actually open such payloads. Detecting a format we cannot yet
  open would be a dead end this phase.

### Backend registry: selection + tri-state, compositional availability

- **All known backends register at import time** (no longer "only when the optional
  dependency imports"). Optional backends record their `OPTIONAL_DEPENDENCY` and an
  availability flag computed at import, rather than silently not registering — this is
  what lets the registry produce the install-hint error the spec already promises.
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
  `list_formats()` → **FULL ∪ PARTIAL** (matches the spec already listing 7z/RAR
  without extras); `list_known_formats()` → the full universe.
- **Format backends stay 1:1 for v1.** Multiple *format* backends per format (e.g. a
  future native ZIP reader beside stdlib `zipfile`) is recorded as an explicit future
  extension, not built now — the only present multi-implementation case lives at the
  codec layer (default vs `rapidgzip`/`indexed_bzip2`), where open-time selection
  already exists (`AcceleratorMode`).

### Cost surface

- Wire `CostReceipt` (`ListingCost` / `AccessCost` / `StreamCapability`) values for
  ZIP, single-file (default + seekable-codec-lowered), and ISO; random / by-name
  access on these indexed sources.

## Specs

This change carries **spec deltas** (it modifies behavior the specs describe):

- **`backend-registry`** (MODIFIED + ADDED): registration becomes "always register;
  mark availability"; new tri-state, compositional `FormatSupport` and the
  availability query surface; degradation reworded against the tri-state.
- **`format-single-file-compressors`** (ADDED): a single multi-format
  `SingleFileBackend` with per-codec metadata hooks is the prescribed structure;
  new standalone codecs become readable without new backend code.

It **implements** (no delta) already-written specs: `format-zip`, `format-iso`,
`format-detection`, and the indexed-access parts of `access-mode-and-cost`. The
`format-zip` "reconcile non-seekable spooling" note is resolved in favor of fail-fast
(recorded above); the `format-detection` SFX requirement is realized in Phase 7, not
here.

## Impact

- **Depends on:** Phase 2 (codec + seekable stream layer green).
- **Affected code:** new `formats/zip_reader.py`, `formats/single_file_reader.py`,
  `formats/iso_reader.py`; new `internal/streams/peekable.py` (`PeekableStream`);
  `internal/detection.py` (`detect_format`, magic aggregation); `internal/registry.py`
  (always-register + availability); the public API (`detect_format`, `list_formats`,
  `list_known_formats`, `format_availability`); `ARCHITECTURE.md` (module tree +
  detection/registry notes).
- **Tests:** `format-zip`, `format-single-file-compressors` (this-phase codecs),
  `format-iso`, `format-detection` scenarios; `backend-registry` selection +
  tri-state availability + ISO-without-pycdlib + `list_formats()` excludes NONE;
  `access-mode-and-cost` indexed/random scenarios for ZIP; non-seekable ZIP
  fail-fast. Retire the matching frozen-oracle coverage as it transfers.
- **Deferred (recorded, not built here):** SFX detection (Phase 7), ZST/LZ4
  single-file (Phase 8), ISO raw-`.bin` sector stripping (optional/MAY-drop),
  multiple *format* backends per format, explicit ZIP `spool_max_size` opt-in.
- **Risk:** `PeekableStream` is the one fresh primitive (not a port) — get the
  peek/replay/position semantics right against the `format-detection` scenarios;
  detection must restore stream position after every probe.
