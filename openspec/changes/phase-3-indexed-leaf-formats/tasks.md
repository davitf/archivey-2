# Tasks — Phase 3: Indexed leaf formats (ZIP, single-file, ISO) + detection

> Run tools through uv: `uv run pytest`, `uv run pyrefly check`, `uv run ty check`,
> `uv run ruff`.
> Prerequisite: Phase 2 complete (codec + seekable stream layer green).
> Clean-slate: backends are **ported as units** onto the new ABC (interface-only
> changes); the spine, detector, registry, and `PeekableStream` are written fresh.

> **DEV source map** (port-as-unit references; pin commit `730275b…`):
> ZIP → `formats/zip_reader.py`; single-file → `formats/single_file_reader.py`
> (one reader for all formats — keep that shape); ISO → `formats/iso_reader.py`;
> detection magic table + extension map → `formats/format_detection.py`. DEV's
> `RecordableStream`/`RewindableStreamWrapper` are **not** ported — they are
> replaced by the fresh `PeekableStream`.

## 0. Decisions locked in this change (no code, just honored below)

- [ ] 0.1 Single-file = **one** multi-format `SingleFileBackend` + per-codec hooks.
- [ ] 0.2 Registry: **always register**; support is tri-state (FULL/PARTIAL/NONE),
      compositional over format backend + codec backends. Format backends stay 1:1.
- [ ] 0.3 Non-seekable ZIP **fails fast** (no implicit spool).
- [ ] 0.4 Scope: unix-compress single-file lands here; **ZST/LZ4 → Phase 8**;
      **SFX detection → Phase 7**; ISO raw-`.bin` stripping deferred (MAY-drop).

## 1. `PeekableStream` (new primitive)

- [ ] 1.1 `internal/streams/peekable.py` — buffer first `DETECTION_LIMIT` bytes
      (4 096 default; 32 774 when ISO detection is triggered), `.peek(n)` returns
      buffered bytes without consuming, reads drain buffer-then-underlying. Presents
      as `BinaryIO`. Constructed by the opener for **non-seekable** sources.
- [ ] 1.2 Tests: peek without consume; read replays buffer then passes through;
      peek beyond buffered limit; non-seekable underlying; ISO-sized window.

## 2. Format detection

- [ ] 2.1 `detect_format(source) -> FormatInfo` + the `FormatInfo` /
      `DetectionConfidence` dataclasses per `format-detection`.
- [ ] 2.2 Magic table aggregated from each backend's `MAGIC`/`EXTENSIONS` **data**
      (no per-backend `detect()` logic); magic-first (`CERTAIN`) → extension
      (`GUESS`); zlib 2-byte header treated as a weak/low-confidence match.
- [ ] 2.3 Magic/extension **conflict** → `logging.WARNING` on `archivey.detection`;
      magic wins.
- [ ] 2.4 Inner-TAR probe over a single-file compressor → `TAR_GZ`/`TAR_BZ2`/`TAR_XZ`/
      `TAR_LZIP`/… (decompress ≥ 512 bytes; skip + defer when codec backend absent).
- [ ] 2.5 Brotli **content probe** (`PROBABLE`); skipped when the Brotli backend is
      missing (fall through to `.br` extension `GUESS`); each probe restores position.
- [ ] 2.6 ISO extended 32 774-byte peek; too-short stream → "not ISO", fall through
      (never reject solely for being shorter than the ISO window).
- [ ] 2.7 Non-consumption: seekable/path sources `seek(0)` after detection;
      non-seekable wrapped once in `PeekableStream` by the opener and shared with the
      backend.
- [ ] 2.8 (Deferred — Phase 7) SFX EXE-stub scan + `payload_offset`. Not built here.

## 3. ZIP backend

- [ ] 3.1 `formats/zip_reader.py` on the ABC (stdlib `zipfile`); `MAGIC`/`EXTENSIONS`
      declared as data; `REQUIRES_SEEK = True`; `_MEMBER_LIST_UPFRONT`,
      `_SUPPORTS_RANDOM_ACCESS`.
- [ ] 3.2 `ZipInfo` → `ArchiveMember` mapping: `mode` from `external_attr>>16`
      (None when 0 / non-Unix), NT-timestamp precedence over DOS `date_time`,
      type from mode/`is_dir()`/symlink extra fields, `compression` map,
      `is_encrypted` from `flag_bits & 0x1`.
- [ ] 3.3 Central-directory lookup is O(1) via `NameToInfo` (no extra I/O).
- [ ] 3.4 Non-seekable source → `StreamNotSeekableError` at open (fail fast).
- [ ] 3.5 Multi-volume (split/spanned) ZIP → `UnsupportedFeatureError` (detect via
      non-zero disk fields / ZIP64 locator `disks>1` / `.z01` segment), with the
      "rejoin first" hint, not a stdlib `BadZipFile`.

## 4. Single-file backend (one multi-format backend)

- [ ] 4.1 `formats/single_file_reader.py` — one `SingleFileBackend`,
      `FORMATS` = standalone-codec set available this phase
      (gz/bz2/xz/lzip/zlib/brotli/unix-compress); decompression via the
      `compressed-streams` codec layer (resolve by `format.stream`).
- [ ] 4.2 One `FILE` member; name inference (strip known compression ext / append
      `.uncompressed` / default `"data"`); no synthesized directories; exactly one
      member yielded.
- [ ] 4.3 Per-codec metadata hooks (dispatch table, not `if`-chains): gzip
      `FNAME`→`raw_filename` (+ mtime; `name` still from source unless configured);
      xz/zst header size; lz4 frame size; lzip trailer size; gz size always `None`;
      bz2/zlib/br/Z size `None` until full read (then may update).
- [ ] 4.4 Cost: `INDEXED` listing; `SOLID` access by default, lowered to the
      seekable cost when a seek-capable codec backend is active.
- [ ] 4.5 unix-compress quirks honored (requires seekable source; no truncation
      signal → a short stream yields fewer bytes with no error).

## 5. ISO backend

- [ ] 5.1 `formats/iso_reader.py` (`pycdlib`, `[iso]`); registered always but marked
      unavailable when `pycdlib` is absent; `OPTIONAL_DEPENDENCY = "pycdlib"`;
      `REQUIRES_SEEK = True`; `MAGIC = ((32769, b"CD001"),)`.
- [ ] 5.2 Richest-namespace auto-select (Rock Ridge > Joliet > plain) →
      `ArchiveInfo.extra["iso.namespace"]`; namespace-dependent fidelity (mode/uid/gid
      `None` under Joliet/plain; symlinks + POSIX under Rock Ridge).
- [ ] 5.3 Write attempt → `UnsupportedOperationError`; non-seekable source rejected.
- [ ] 5.4 (Deferred / lower-priority) raw `.bin` sector-stripping wrapper — not built
      unless it stays a thin wrapper; otherwise dropped per spec.

## 6. Backend registry: selection + tri-state availability

- [ ] 6.1 Register **all** known backends at import (optional ones inside their
      import guard record availability instead of skipping registration); retain
      `OPTIONAL_DEPENDENCY` + install hint.
- [ ] 6.2 `FormatSupport` enum (FULL/PARTIAL/NONE); `format_availability(fmt)`
      computed compositionally over the format backend + the codec backends a format
      can use; returns missing components (package/extra/tool + hint + unlocked
      codecs).
- [ ] 6.3 `list_formats()` → FULL ∪ PARTIAL; `list_known_formats()` → all known.
- [ ] 6.4 Selection: `reader_for_format()` maps detected format → backend; a NONE
      format raises `UnsupportedFormatError` with the install hint; missing-dep gaps
      kept distinct from by-design rejections (BCJ2/unknown method IDs →
      `UnsupportedFeatureError`).
- [ ] 6.5 Public-API exposure of `detect_format`, `list_formats`,
      `list_known_formats`, `format_availability`.

## 7. Wire `open_archive()` + cost

- [ ] 7.1 `open_archive()` detects (wrapping non-seekable in `PeekableStream`),
      selects the backend, enforces `REQUIRES_SEEK` fail-fast, hands over the shared
      stream.
- [ ] 7.2 `CostReceipt` values verified per format (ZIP `INDEXED`/`DIRECT`/`SEEKABLE`;
      single-file `INDEXED`/`SOLID`→lowered; ISO `INDEXED`/`DIRECT`/`SEEKABLE`).

## 8. Tests added (new suite)

- [ ] 8.1 `format-zip` scenarios (CostReceipt, O(1) lookup, member mapping,
      non-seekable fail-fast, multi-volume rejection).
- [ ] 8.2 `format-single-file-compressors` scenarios for this-phase codecs (name
      inference, one member, gzip `raw_filename`, per-format size rules, cost,
      seekable-codec lowering). ZST/LZ4 scenarios deferred to Phase 8.
- [ ] 8.3 `format-iso` scenarios (namespace auto-select + fidelity, write rejected,
      non-seekable rejected) — skip when `pycdlib` absent.
- [ ] 8.4 `format-detection` scenarios (magic, extension, conflict warning, inner-TAR,
      Brotli probe + skip, ISO extended/too-short, never-consumes). SFX deferred.
- [ ] 8.5 `backend-registry` scenarios (always-register; tri-state FULL/PARTIAL/NONE;
      `list_formats()` excludes NONE; ISO-without-pycdlib error + hint; two
      simultaneous readers).
- [ ] 8.6 `access-mode-and-cost` indexed-listing / random-access (default
      `streaming=False`) for ZIP; non-seekable ZIP fail-fast.
- [ ] 8.7 Retire the matching `tests/_dev_oracle/` coverage as it transfers.

## 9. Verify — acceptance criteria

**Spec scenarios covered**
- [ ] 9.1 `format-zip` (all read), `format-single-file-compressors` (this-phase
      codecs), `format-iso` (all), `format-detection` (all except SFX).
- [ ] 9.2 `backend-registry` selection + degradation + tri-state availability;
      `access-mode-and-cost` indexed/random for ZIP.

**Gates**
- [ ] 9.3 `uv run pyrefly check` and `uv run ty check` both clean (strict).
- [ ] 9.4 `uv run ruff check` clean.
- [ ] 9.5 New tests green; frozen oracle no worse; `git status` clean after a run
      (no committed generated binaries).
