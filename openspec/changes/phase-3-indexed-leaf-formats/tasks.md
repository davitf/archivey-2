# Tasks — Phase 3: Indexed leaf formats (ZIP, TAR read, single-file, ISO) + detection

> Run tools through uv: `uv run pytest`, `uv run pyrefly check`, `uv run ty check`,
> `uv run ruff`.
> Prerequisite: Phase 2 complete (codec + seekable stream layer green).
> Clean-slate: backends are **ported as units** onto the new ABC (interface-only
> changes); the spine, detector, registry, and `PeekableStream` are written fresh.

> **DEV source map** (port-as-unit references; pin commit `730275b…`):
> ZIP → `formats/zip_reader.py`; TAR → `formats/tar_reader.py` (read parts only;
> the streaming/extraction path is Phase 4); single-file →
> `formats/single_file_reader.py` (one reader for all formats — keep that shape);
> ISO → `formats/iso_reader.py`; detection magic table + extension map →
> `formats/format_detection.py`. DEV's
> `RecordableStream`/`RewindableStreamWrapper` are **not** ported — they are
> replaced by the fresh `PeekableStream`.

## 0. Decisions locked in this change (no code, just honored below)

- [x] 0.1 Single-file = **one** multi-format `SingleFileBackend` + per-codec hooks.
- [x] 0.2 Registry: **always register**; support is tri-state (FULL/PARTIAL/NONE),
      compositional over format backend + codec backends. Format backends stay 1:1.
- [x] 0.3 Non-seekable ZIP **fails fast** (no implicit spool).
- [ ] 0.4 **TAR read** (random-access) + compressed-tar detection land here; TAR
      `stream_members`/forward-only + `ExtractionCoordinator`/safe-extraction stay
      **Phase 4**.
- [ ] 0.5 **Seek-heavy containers are not mounted over a compressor**: only TAR
      composes with stream compressors; `.iso.xz`/`.iso.gz`/`.zip.xz` are single-file
      compressors wrapping the inner image.
- [ ] 0.6 Scope: unix-compress single-file lands here; **ZST/LZ4 → Phase 8**;
      **SFX detection → Phase 7**; ISO raw-`.bin` stripping deferred (MAY-drop).
- [x] 0.7 No `config` param on `open_read` this phase: a backend opening a codec builds
      `StreamConfig(streaming=self._streaming)` itself so the accelerator `AUTO`
      resolves correctly. A public config surface arrives in Phase 5.

## Stage map (implementation order — one PR per stage)

Phase 3 stays a **single** OpenSpec change; it is implemented and reviewed in four
mergeable stages. Each stage ends green (pyrefly + ty + ruff + its new tests) before
the next starts. Only Stage 1 touches the shared machinery; 2–4 are additive backends.

| Stage | Theme | Task groups |
|-------|-------|-------------|
| **1** | Foundation + ZIP (proves detect→select→open→read→cost) | §1 · §2.1–2.3, 2.7 · §3 · §6 · §7 · §8.1, 8.4(S1), 8.5(core), 8.6(ZIP) · §9 gates |
| **2** | Single-file compressors | §4 · §2.5 · §8.2, 8.4(S2) · §9 gates |
| **3** | TAR (read) + compressed-tar | §3b · §2.4 · §8.1b, 8.4(S3), 8.6(TAR) · §9 gates |
| **4** | ISO + degradation end-to-end | §5 · §2.6 · §8.3, 8.4(S4), 8.5(NONE) · §9 gates |

Stages 2 and 3 are swappable; only Stage 1 is a hard prerequisite for the rest.
`§8.7` (retire frozen-oracle coverage) and `§7.2` (per-format cost) happen
incrementally — each as its format's stage lands.

## 1. `PeekableStream` (new primitive)

> **Stage 1.**

- [x] 1.1 `internal/streams/peekable.py` — buffer first `DETECTION_LIMIT` bytes
      (4 096 default; 32 774 when ISO detection is triggered), `.peek(n)` returns
      buffered bytes without consuming, reads drain buffer-then-underlying. Presents
      as `BinaryIO`. Constructed by the opener for **non-seekable** sources.
- [x] 1.2 Tests: peek without consume; read replays buffer then passes through;
      peek beyond buffered limit; non-seekable underlying; ISO-sized window.

## 2. Format detection

> **Spans stages** — core in Stage 1; each probe lands with the backend it feeds
> (Brotli → S2, inner-TAR → S3, ISO window → S4). Stage tags are inline below.

- [x] 2.1 **(S1)** `detect_format(source) -> FormatInfo` + the `FormatInfo` /
      `DetectionConfidence` dataclasses per `format-detection`.
- [x] 2.2 **(S1)** Magic table aggregated from each backend's `MAGIC`/`EXTENSIONS`
      **data** (no per-backend `detect()` logic); magic-first (`CERTAIN`) → extension
      (`GUESS`); zlib 2-byte header treated as a weak/low-confidence match. (zlib weak
      match arrives with the zlib magic entry in S2 — no zlib backend is registered yet.)
- [x] 2.3 **(S1)** Magic/extension **conflict** → `logging.WARNING` on
      `archivey.detection`; magic wins.
- [ ] 2.4 **(S3)** Inner-TAR probe over a single-file compressor →
      `TAR_GZ`/`TAR_BZ2`/`TAR_XZ`/`TAR_LZIP`/… (decompress ≥ 512 bytes; skip + defer
      when codec backend absent). These are **openable** once the TAR reader (§3b)
      lands. No inner-ISO / inner-ZIP probe — seek-heavy containers stay
      single-file-wrapped (0.5).
- [x] 2.5 **(S2)** Brotli **content probe** (`PROBABLE`); skipped when the Brotli
      backend is missing (fall through to `.br` extension `GUESS`); each probe restores
      position.
- [ ] 2.6 **(S4)** ISO probe: always attempt it as a **fallback** when the 4 KiB magic
      table and extension produce no `CERTAIN` match (not gated on a `.iso` extension —
      32 KiB is cheap and the rule is cleaner). Peek to 32 774 bytes (`PeekableStream`
      grows its buffer to this cap on demand); too-short stream → "not ISO", fall
      through (never reject solely for being shorter than the ISO window).
- [x] 2.7 **(S1)** Non-consumption: seekable/path sources `seek(0)` after detection;
      non-seekable wrapped once in `PeekableStream` by the opener and shared with the
      backend.
- [ ] 2.8 (Deferred — Phase 7) SFX EXE-stub scan + `payload_offset`. Not built here.

## 3. ZIP backend

> **Stage 1.**

- [x] 3.1 `formats/zip_reader.py` on the ABC (stdlib `zipfile`); `MAGIC`/`EXTENSIONS`
      declared as data; `REQUIRES_SEEK = True`; `_MEMBER_LIST_UPFRONT`,
      `_SUPPORTS_RANDOM_ACCESS`.
- [x] 3.2 `ZipInfo` → `ArchiveMember` mapping: `mode` from `external_attr>>16`
      (None when 0 / non-Unix), NT-timestamp precedence over DOS `date_time`,
      type from mode/`is_dir()`/symlink extra fields, `compression` map,
      `is_encrypted` from `flag_bits & 0x1`.
- [x] 3.3 Central-directory lookup is O(1) via `NameToInfo` (no extra I/O).
- [x] 3.4 Non-seekable source → `StreamNotSeekableError` at open (fail fast).
- [x] 3.5 Multi-volume (split/spanned) ZIP → **best-effort** `UnsupportedFeatureError`
      with the "rejoin first" hint: detect the obvious signals we can cheaply see
      (a `.z01`/`.zNN` segment by name; a non-zero disk field if `zipfile` surfaces it)
      and raise; otherwise let `zipfile` try, and translate a resulting `BadZipFile`
      that indicates multi-disk into `UnsupportedFeatureError` (a partially-readable
      file is left to read). Robust *(disk, offset)* detection is **deferred to the
      future native ZIP backend** — stdlib `zipfile` does not expose the EOCD disk
      fields cleanly.

## 3b. TAR backend (read only)

> **Stage 3.**

- [ ] 3b.1 `formats/tar_reader.py` on the ABC (stdlib `tarfile`); `MAGIC` (`ustar` at
      257 → `TAR`) / `EXTENSIONS` declared as data; `FORMATS` includes `TAR` **and** the
      compressed combos (`TAR_GZ`/`TAR_BZ2`/`TAR_XZ`/… + on-demand `(TAR, <codec>)`);
      random-access reading on a seekable source (scan-and-index → `_MEMBER_LIST_UPFRONT`
      after scan, `_SUPPORTS_RANDOM_ACCESS`).
- [ ] 3b.2 `TarInfo` → `ArchiveMember` mapping across PAX / GNU / ustar variants
      (mode, mtime, uid/gid, type incl. symlink/hardlink/dir, size).
- [ ] 3b.3 Compressed-tar: the backend **opens the codec internally** —
      `open_codec_stream(codec_for_stream_format(fmt.stream), source,
      config=StreamConfig(streaming=streaming))` → `tarfile.open(fileobj=…, mode="r:")`.
      (Our codec layer, not `tarfile`'s native `r:gz`/`r:bz2`/`r:xz`, so `tar.lzip`/
      `tar.zst`/`tar.lz4` etc. work too. The opener stays generic — composition is the
      backend's job, keeping the seek-heavy-container rule (0.5) per-backend.) Cost
      reflects the underlying codec.
- [ ] 3b.4 Cost: listing is `REQUIRES_SCANNING` for a plain tar / `REQUIRES_DECOMPRESSION`
      for a compressed tar (TAR has **no** central index — not `INDEXED`); `DIRECT`
      access on a seekable plain tar.
- [ ] 3b.5 **Out of scope (Phase 4):** forward-only `stream_members()` on a
      non-seekable `tar.gz`; `ExtractionCoordinator` / safe-extraction. Don't build the
      streaming `_iter_with_data` override here.

## 4. Single-file backend (one multi-format backend)

> **Stage 2.**

- [x] 4.0 **Extend the data model** (prerequisite): add `StreamFormat.LZIP` (`"lz"`),
      `ZLIB` (`"zz"`), `BROTLI` (`"br"`), `UNIX_COMPRESS` (`"Z"`); extend
      `_STREAM_FORMAT_CODECS` to map them to their `Codec`; add named standalone
      `ArchiveFormat` constants (`LZIP`, `ZLIB`, `BROTLI`, `Z`). Uncommon **combos**
      (e.g. `tar.lz`) get **no** predefined constant — they are built on demand as
      `ArchiveFormat(container, stream)`. (Spec delta: `archive-data-model`.)
- [x] 4.1 `formats/single_file_reader.py` — one `SingleFileBackend`,
      `FORMATS` = standalone-codec set available this phase
      (gz/bz2/xz/lzip/zlib/brotli/unix-compress); decompression via
      `open_codec_stream(codec_for_stream_format(fmt.stream), source,
      config=StreamConfig(streaming=streaming))` (see 0.7).
- [x] 4.2 One `FILE` member; name inference (strip known compression ext / append
      `.uncompressed` / default `"data"`); no synthesized directories; exactly one
      member yielded.
- [x] 4.3 Per-codec metadata hooks (dispatch table, not `if`-chains): gzip `FNAME` →
      `extra["gzip.original_filename"]` (decoded) **and** `raw_name` (undecoded bytes);
      `name` still derived from the source filename unless configured otherwise (+
      gzip mtime). Other size hooks: xz/zst header size; lz4 frame size; lzip trailer
      size; gz size always `None`; bz2/zlib/br/Z size `None` until full read (then may
      update). (xz/lzip size implemented via the seekable index/trailer; zst/lz4 are
      Phase 8.)
- [x] 4.4 Cost: `INDEXED` listing (always one member); `DIRECT` access (one member —
      no inter-member dependency, so `SOLID` does not apply). Whether the opened member
      *stream* can seek (xz block index, etc.) is a stream-level property
      (`seekable-decompressor-streams`), not the archive-level `CostReceipt`.
- [x] 4.5 unix-compress quirks honored: **requires a seekable source** — a non-seekable
      `.Z` raises `StreamNotSeekableError` at open (the unix-compress codec already
      raises this; other single-file formats stay readable on a non-seekable source).
      No truncation signal → a short stream yields fewer bytes with no error.
- [x] 4.6 A non-`None` `password` on a single-file open raises
      `UnsupportedOperationError` (single-file compressors have no encryption).

## 5. ISO backend

> **Stage 4.**

- [ ] 5.1 `formats/iso_reader.py` (`pycdlib`, `[iso]`); registered always but marked
      unavailable when `pycdlib` is absent (the `_optional("pycdlib")` sentinel);
      `OPTIONAL_DEPENDENCY = "pycdlib"`; `REQUIRES_SEEK = True`;
      `MAGIC = ((32769, b"CD001"),)`.
- [ ] 5.2 Richest-namespace auto-select (Rock Ridge > Joliet > plain) →
      `ArchiveInfo.extra["iso.namespace"]`; namespace-dependent fidelity (mode/uid/gid
      `None` under Joliet/plain; symlinks + POSIX under Rock Ridge).
- [ ] 5.3 Write attempt → `UnsupportedOperationError`; non-seekable source rejected.
- [ ] 5.4 (Deferred / lower-priority) raw `.bin` sector-stripping wrapper — not built
      unless it stays a thin wrapper; otherwise dropped per spec.

## 6. Backend registry: selection + tri-state availability

> **Stage 1** (the tri-state machinery; **NONE** end-to-end is verified in Stage 4
> with ISO, and full **PARTIAL**-at-read completes in Phase 7 with 7z).

- [x] 6.1 Register **all** known backends at import; optional ones derive availability
      from the `_optional(OPTIONAL_DEPENDENCY)` sentinel (no per-backend boolean);
      retain `OPTIONAL_DEPENDENCY` + install hint.
- [x] 6.2 `FormatSupport` enum (FULL/PARTIAL/NONE); `format_availability(fmt)`
      computed compositionally over the format backend + the codec backends a format
      can use; returns missing components (package/extra/tool + hint + unlocked
      codecs).
- [x] 6.3 `list_supported_formats()` → FULL ∪ PARTIAL; `list_known_formats()` → all
      known.
- [x] 6.4 Selection: `reader_for_format()` maps detected format → backend; a NONE
      format raises `UnsupportedFormatError` with the install hint; missing-dep gaps
      kept distinct from by-design rejections (BCJ2/unknown method IDs →
      `UnsupportedFeatureError`).
- [x] 6.5 Public-API exposure of `detect_format`, `list_supported_formats`,
      `list_known_formats`, `format_availability`.

## 7. Wire `open_archive()` + cost

> **Stage 1** for the wiring (7.1); **7.2** per-format cost is verified as each
> format's stage lands (ZIP S1, single-file S2, TAR S3, ISO S4).

- [x] 7.1 `open_archive()` detects (wrapping non-seekable in `PeekableStream`),
      selects the backend, enforces `REQUIRES_SEEK` fail-fast, hands over the shared
      stream.
- [ ] 7.2 `CostReceipt` values verified per format (ZIP `INDEXED`/`DIRECT`/`SEEKABLE`;
      TAR `REQUIRES_SCANNING`|`REQUIRES_DECOMPRESSION`/`DIRECT`; single-file
      `INDEXED`/`DIRECT`; ISO `INDEXED`/`DIRECT`/`SEEKABLE`).
- [x] 7.3 Thread the encoding: `open_read` receives the caller's explicit `encoding`
      if given, else the detector's `encoding_hint`, else `None` (backend
      auto-detects). The existing `encoding` parameter carries it — no new argument.

## 8. Tests added (new suite)

- [x] 8.1 **(S1)** `format-zip` scenarios (CostReceipt, O(1) lookup, member mapping,
      non-seekable fail-fast, multi-volume rejection).
- [ ] 8.1b **(S3)** `format-tar` random-access read scenarios (PAX/GNU/ustar mapping,
      compressed-tar `tar.gz`/`tar.xz`/…, cost). Streaming/`stream_members` scenarios
      deferred to Phase 4.
- [x] 8.2 **(S2)** `format-single-file-compressors` scenarios for this-phase codecs
      (name inference, one member, gzip stored name → `extra` + `raw_name`, per-format
      size rules, `DIRECT` cost, non-seekable `.Z` raises, password raises). ZST/LZ4
      scenarios deferred to Phase 8.
- [ ] 8.3 **(S4)** `format-iso` scenarios (namespace auto-select + fidelity, write
      rejected, non-seekable rejected) — skip when `pycdlib` absent.
- [ ] 8.4 `format-detection` scenarios, by stage: **(S1)** magic, extension, conflict
      warning, never-consumes; **(S2)** Brotli probe + skip; **(S3)** inner-TAR;
      **(S4)** ISO extended/too-short. SFX deferred.
- [ ] 8.5 `backend-registry` scenarios: **(S1)** always-register via `_optional`
      sentinel, tri-state FULL/PARTIAL, `list_supported_formats()` vs
      `list_known_formats()`, two simultaneous readers; **(S4)** NONE +
      ISO-without-pycdlib error + hint (end-to-end degradation).
- [ ] 8.6 `access-mode-and-cost` indexed-listing / random-access (default
      `streaming=False`): **(S1)** ZIP + non-seekable ZIP fail-fast; **(S3)** TAR.
- [ ] 8.7 Retire the matching `tests/_dev_oracle/` coverage as each format transfers
      (per stage).

## 9. Verify — acceptance criteria

> Each gate (9.3–9.5) is re-run and must pass **at the end of every stage**, not only
> at the end of the phase.

**Spec scenarios covered**
- [ ] 9.1 `format-zip` (all read), `format-tar` (random-access read + compressed-tar;
      streaming → Phase 4), `format-single-file-compressors` (this-phase codecs),
      `format-iso` (all), `format-detection` (all except SFX).
- [ ] 9.2 `backend-registry` selection + degradation + tri-state availability;
      `access-mode-and-cost` indexed/random for ZIP and TAR.

**Gates**
- [ ] 9.3 `uv run pyrefly check` and `uv run ty check` both clean (strict).
- [ ] 9.4 `uv run ruff check` clean.
- [ ] 9.5 New tests green; frozen oracle no worse; `git status` clean after a run
      (no committed generated binaries).
