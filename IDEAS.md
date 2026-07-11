# Archivey — Future Ideas / Backlog

> **Status: speculative.** Nothing here is committed or scheduled. These are
> "might do later, worth remembering" notes — *not* part of the `PLAN.md` phase
> roadmap. Firm, decided v1 deferrals (async, in-place modify, sparse-file
> extraction, etc.) live in `openspec/project.md`
> ("Deferred / out of scope (v1)") and `SPEC.md` Appendix A — this file is the
> looser idea pile. Promote an item by writing a real spec/`openspec` change for it.

## Backends & format coverage

- **Native streaming ZIP reader** — a native parser that does what stdlib `zipfile`
  can't: read from **non-seekable** streams (pipes/sockets) and **truncated / no-EOCD**
  archives by walking local file headers forward, plus better coverage of data
  descriptors, ZIP64 edge cases, extra fields, and **AES/WinZip encryption** (zipfile
  only does legacy ZipCrypto). Fits the native-first direction (cf. 7z/RAR). Streaming
  mode is forward-only and sizes/CRC arrive in trailing data descriptors — i.e. the
  **late-bound `ArchiveMember` fields** + `FORWARD_ONLY`/`is_solid=False` cost model we
  already designed for. Lands as a native variant of `formats/zip_reader.py`.
  This is also the natural home for **multi-volume (split/spanned) ZIP** — the
  `.z01`…`.zip` sets that `zipfile` cannot read (it rejects multi-disk archives; see
  `format-zip`). A native parser can read the central directory disk-aware and resolve
  each *(disk-number, offset)* against a concatenation stream over the ordered segments
  — the analogue of the 7z volume-join, but with ZIP's per-disk addressing rather than a
  dumb byte-split. (For v1 we just detect these and raise `UnsupportedFeatureError`.)
  Also the home for **graceful UTF-8-flag-lie handling**: stdlib `zipfile` strictly
  decodes a name whose general-purpose bit 11 claims UTF-8, so one hostile/broken name
  makes the *whole archive* unlistable (`UnicodeDecodeError` → `CorruptionError`; the
  adversarial string corpus pins that behavior). A native parser can decode such names
  with the same cp437/`surrogateescape` fallback used for unflagged names and keep the
  archive readable — likely with a diagnostic once warnings-as-data lands.

- **libarchive backend** — `python-libarchive-c` as an **alternative / additional**
  backend for several formats (zip/tar/7z/iso/cpio/…), in the `[all]`/alternative tier
  behind a `[libarchive]` extra. Caveats: native C dependency (the packaging-finicky
  axis `[recommended-lite]` exists to avoid), stream-oriented (weak random access,
  historically no solid-RAR support).

- **Synthetic single-stream RAR → libarchive** — generalize rarfile's "hack": build a
  minimal artificial RAR stream containing a single file (or one solid block) and feed
  it to libarchive's RAR decompressor, as an alternative to shelling out to the external
  `unrar` binary. Could remove the `unrar` runtime requirement for common cases.
  **Higher-risk / research spike** — RAR decode correctness is hard and libarchive's
  RAR5 coverage is partial; `unrar` remains the reference.

- **Subprocess decompressor streams** — a single reusable `SubprocessDecompressorStream`
  that pipes compressed/uncompressed data through a system binary (`zstd`, `xz`,
  `brotli`, `lz4`, …) as an alternative to installing the Python codec libs. Same pattern
  we already use for `unrar`; valuable in locked-down environments where C-extension
  wheels won't install but CLI tools are on PATH. Forward-only; needs availability
  detection and careful subprocess/error handling. Low-priority backend tier.

- **Non-seekable unix-compress (`.Z`)** — the `uncompresspy` backend currently requires a
  **seekable** source (it decodes via random access), so archivey reports
  `StreamNotSeekableError` for a `.Z` pipe/socket. The library's decode path looks
  straightforward to make forward-only; low-priority task to either fork the relevant bit
  into archivey or send a fix upstream so `.Z` works on non-seekable streams like the other
  single-file compressors.

## API & ergonomics

- **`SANITIZE` extraction policy: name rewriting** — the post-v1 opt-in `SANITIZE`
  policy already sketched in `safe-extraction` (re-root/collapse unsafe paths instead of
  rejecting) is also the right home for **renaming members the destination cannot
  represent**: undecodable-byte (`surrogateescape`) names that UTF-8-enforcing
  filesystems (APFS, some network mounts) refuse with `EILSEQ`/`EINVAL`, and other
  representability failures. One policy knob covering all "make it extractable by
  rewriting the name" behavior — not a bespoke argument per case. Default behavior
  stays reject-with-typed-error (see the adversarial-string-corpus-contract
  safe-extraction delta).

- **Pathlib-like navigation** — an `ArchivePath` supporting `/` joining, `iterdir()`,
  `glob()`, `read_bytes()`, `is_dir()`, … over the member tree (precedent: `zipfile.Path`).
  Read-only wrapper; needs random access, so a `DIRECT`/indexed-archive convenience.

- **fsspec integration** — three distinct directions:
  (1) **expose** an opened archive as an `fsspec` filesystem so pandas/dask/pyarrow/etc.
  read members by path (pairs with the pathlib navigation layer) — the substantial one;
  (2) **open** archives *from* an `fsspec` URL (`s3://…/a.zip`, `http(s)://`, …) as the
  `source`; (3) **extract** *to* an `fsspec` location as the `dest`.
  For (2), passing an fsspec-opened file object **already works** (the stream-input
  tests exercise fsspec objects), so the remaining value is *URL-level* opening —
  archivey calling `fsspec.open()` itself, behind an optional `[fsspec]` extra. What
  that buys beyond "hand me a stream": archivey can pick sensible fsspec caching for
  the access mode (`streaming=True` → plain forward read; random access → block/file
  cache so ZIP central-directory + member seeks don't re-fetch), and — the real
  unlock — it has **filesystem context**, which a bare stream can never provide:
  multi-volume sets (`name.7z.001`…) need `fs.ls()` to discover sibling volumes, which
  the Phase 6 volume-joining path requires. Shape TBD: a URL-string branch inside
  `open_archive()` (gated on `"://" in source` + fsspec installed) vs. a separate
  `open_archive_url()`; the separate function keeps typing/behavior of the core
  entry point simple and the dependency boundary explicit.

- **Configurable symlink-extraction behavior** — a policy knob (in the spirit of `OnError`
  / `ExtractionPolicy`) for what happens when a SYMLINK member cannot be created as a real
  symlink — most notably on filesystems/platforms without symlink support (FAT, Windows
  without the privilege). Phase 4 fixes this at "per-member `OnError` failure, never copy"
  (deliberately *deviating* from `tarfile`, which silently copies the in-archive target's
  data). A future option could offer e.g. `symlink=error|copy|skip` (copy = `tarfile`-style
  materialize-the-target, guarded so it can't reintroduce a path escape). Its own change +
  exploration — the safe default lands first.

## Performance & robustness

- **rapidgzip for zlib / raw-deflate streams** — give zlib- and deflate-compressed streams
  the same fast random access rapidgzip already gives gzip. This is especially valuable for the
  future native **ZIP** parser: ZIP members are raw deflate, so a seekable deflate backend means
  random access *within* a large member, not just to its start. Investigate whether rapidgzip can
  consume zlib/raw-deflate **directly** (it already handles gzip/zlib framing; raw deflate, wbits
  -15, may need a hint or may be unsupported). If not, **synthesize a gzip stream** from the
  source — wrap raw deflate (or zlib, after dropping its 2-byte header + adler32 trailer) in a
  minimal 10-byte gzip header + 8-byte trailer so rapidgzip will index it; check whether it needs
  a *valid* CRC32/ISIZE trailer or just well-formed framing to build the seek index. No
  coexistence concern — archivey already uses rapidgzip as its single accelerator library (see
  `docs/known-issues.md`). Pairs with **seek-index persistence** below.

- **Compressed-passthrough transcoding (no recompress)** — when writing a member from a source
  that is itself an archive/compressed stream, and the destination format can carry the source's
  *compressed* representation as-is (e.g. a deflate member from a ZIP/gzip → a ZIP entry, both raw
  deflate), copy the already-compressed bytes straight through instead of decompress→recompress.
  Skips the most expensive part of a format conversion entirely. Needs internal coordination
  between the read and write paths: the reader must be able to hand out the *raw compressed* block
  (codec + parameters + the bytes) rather than only a decompressed stream, and the writer must
  accept a pre-compressed payload and emit the right container framing/headers (and decide what to
  do about checksums — reuse the stored CRC vs. recompute). Only valid when codecs + parameters
  match (e.g. deflate↔deflate; not deflate→zstd), so it's an opportunistic fast path with a
  decompress-recompress fallback. Pairs with the native ZIP parser (raw-deflate access) above.

- **Parallel extraction / concurrent member streams** — the declared worker seam
  (`MemberStreams.CONCURRENT`) is committed and **provisional in v1** (cooperative
  post-materialization fan-out; free-threaded promotion deferred — see
  `openspec/changes/concurrent-member-streams` D15). Scheduling/throughput for
  extract-independent-members remains future; any speed claim needs targeted
  measurements. Also applies to **solid archives with multiple independent blocks** —
  e.g. a 7z with several solid folders can decompress folders in parallel (py7zr does
  this); members *within* one solid block stay sequential. No benefit for a single-block
  solid archive. Misuse fails loudly (`ArchiveyUsageError` / `ConcurrentAccessError`).

- **Efficient seekable zstd — probably a *native* frame-index reader, not `indexed_zstd`.**
  *(Status: **scheduled** — promoted to the rescoped Phase 8 in `PLAN.md`; the analysis
  below is the basis for that phase's benchmark-first task.)*
  zstd currently has *no* fast random access: a backward seek re-decompresses from the start
  (rewind + warning), like brotli/lz4/zlib. The obvious candidate,
  [`indexed_zstd`](https://github.com/martinellimarco/indexed_zstd) (martinellimarco; the zstd
  backend ratarmount uses, wrapping `libzstd-seek`), is a heavy Cython/C++17 extension that
  statically bundles a C++ core "based on `indexed_bzip2`" — so it carries the *same class* of
  macOS dual-load symbol-collision risk that forced archivey onto a single accelerator library
  (`docs/known-issues.md`) and would need its own coexistence canary.

  **But first check whether it actually buys us anything our own infrastructure can't.**
  `libzstd-seek`'s jump table maps **frame boundaries only** — its own header says records map a
  compressed to an uncompressed position where "both positions refer to frame boundaries", giving
  "constant-time random access **at zstd frame granularity**". A seek into the middle of a frame
  jumps to that frame's start and decodes forward; there is **no** intra-frame state
  checkpointing (unlike `rapidgzip`, which snapshots the inflate window mid-stream). That is
  *exactly* the granularity our `_SegmentedDecompressorStream` already delivers for **xz** (block
  index) and **lzip** (member/trailer scan): seek = jump to the segment containing the offset,
  decode forward within it. So the likely-better path is a **small native zstd reader** that
  reuses that infrastructure — build a frame index by scanning frame headers (compressed size
  per frame from its header; decompressed size from the frame's optional `Frame_Content_Size`
  field or, when present, the *Seekable Zstd* skippable-frame seek table) — getting the same
  frame-granularity seeking **for free**, with zero new heavy dependency and no macOS risk. This
  is the zstd analogue of why we wrote `xz.py`/`lzip.py` instead of depending on `python-xz`.

  Things to confirm before committing to the native route:
  - **Does `indexed_zstd` do anything a frame-index reader wouldn't?** From the docs, no — it is
    frame-granularity only (no intra-frame seeking). If that holds, the native reader loses
    nothing. (`rapidgzip`-style intra-member seeking would be the only reason to prefer a heavy
    lib, and `libzstd-seek` does not do it.)
  - **Benchmark the candidates — "same granularity" doesn't mean "same speed".** Even at equal
    seek granularity, the C++ lib might still win on raw throughput (e.g. faster libzstd decode
    of the forward run within a frame, cheaper jump-table construction, less Python-level
    overhead) enough to justify adding it as an *accelerator* (the way `rapidgzip` is, behind an
    extra) rather than rejecting it. Decide with numbers, not just the feature comparison: build a
    representative large `.zst` (and a multi-frame / *Seekable Zstd* variant), then time several
    access patterns — cold full sequential read, `SEEK_END` + tail read, a scattered set of random
    seeks-then-reads, and a backward rewind — across the **stdlib `compression.zstd` reader**, the
    **native frame-index wrapper**, and **`indexed_zstd`**, measuring wall time, peak memory, and
    index-build cost. If the native wrapper is within a small constant of `indexed_zstd`, prefer it
    (no heavy dep, no macOS risk); if `indexed_zstd` is dramatically faster on a real workload,
    weigh it as an optional accelerator. A `benchmarks/`-style script (cf. DEV's `bench_xz.py`) is
    the natural home.
  - **The benefit only exists for multi-frame `.zst`.** A single-frame stream — the common
    default from the `zstd` CLI and most writers — has exactly one frame, so frame-granularity
    seeking yields a single seek point (offset 0) and helps neither approach; the win is real
    only for *Seekable Zstd* files or anything compressed with frame splitting (e.g. `.tar.zst`
    written that way). Worth measuring how often multi-frame `.zst` actually occurs before
    investing in either.
  - **Frames without `Frame_Content_Size`** can't be indexed without decoding them (this is also
    `libzstd-seek`'s slow fallback). The native reader can simply build the index only when sizes
    are available (header field or seek table) and otherwise fall back to the rewind path.

  Note `pyzstd.SeekableZstdFile` is **not** a substitute either: it reads only the *Seekable
  Zstd* container, not plain `.zst`. See `docs/library-analysis.md` (zstd).

## Strategy & adoption (2026-07 review backlog)

> Parked here from the 2026-07 architecture-review discussion so nothing is lost.
> Security/compat items with a threat angle live in `docs/threat-model.md` (the gap
> register); the product framing lives in `VISION.md`. These are the rest.

- **Salvage / best-effort read mode** — the founding use case (indexing decades of
  messy backups) is full of truncated and corrupt archives, and today every backend is
  all-or-error. A `salvage=True`-style read mode would yield every recoverable member
  plus per-member/status errors instead of one terminal exception: for ZIP, walk local
  headers when the central directory is gone; for TAR, resync on the next valid header;
  for single-file streams, return the decodable prefix with a truncation flag. Nobody
  does this well; it is both a founding need and a differentiator. Needs its own spec
  (interacts with error-handling and the equivalence matrix).
- **Hashes without decompression** — dedupe workflows can often use the digests the
  archive already stores (`member.hashes`: CRC32, RAR5 BLAKE2sp, …) instead of reading
  data. Document the recipe; consider a helper that returns "best available digest +
  provenance (stored vs computed)" so an indexer can choose cheap-but-weak vs
  costly-but-strong uniformly.
- **Benchmarks as a CI gate** — suite tracking open/list/read/extract wall time vs
  stdlib (`zipfile`/`tarfile`) and py7zr/libarchive where comparable, plus
  **bytes-decompressed and seek counts** (the real bottlenecks — re-decompression and
  seek storms — hide in wall time on small corpora). Budget per `VISION.md`: ≤1.3×
  stdlib common paths, ~2× when justified. Stand up before any perf-sensitive claim.
- **Public backend API** — stabilize/export the `ReadBackend` ABC + registry so rare
  formats (CAB, CPIO, SquashFS, WIM, XAR, DMG…) can be third-party plugins instead of
  a solo compatibility treadmill. Decide pre-1.0 (it constrains how freely the backend
  contract can change afterwards).
- **fsspec adapter** — expose an opened archive as an fsspec filesystem
  (`ArchiveFileSystem`); big adoption channel (pandas/dask/HF datasets ecosystems) and
  a good stress test of the reader contract. Also the natural place for
  `open_archive("https://…")` stories rather than teaching core about URLs.
- **Migration guide** — `zipfile`/`tarfile`/`shutil.unpack_archive`/`patool` →
  archivey, gotcha-by-gotcha ("`tarfile.extractall` without `filter=` does X; here it
  cannot happen"). Cheap, high-leverage for the "default library" goal.
- **Warnings-as-data sweep** — audit every `logger.warning` in the library: each should
  (also) be queryable as data (member/info field, `FormatInfo`, `CostReceipt`,
  `ExtractionResult`), since most applications never surface logging. See
  `docs/threat-model.md` C2.
- **Extraction collision handling + `OverwritePolicy.RENAME`** — deterministic
  cross-platform handling of casefold/normalization collisions (threat-model O2), plus
  an opt-in RENAME policy (`name (1)`) for archives with intentional duplicates.
- **Writing, done properly, later** — writing is deliberately post-reading (possibly
  post-1.0). When specced, design in from the start: **reproducible output**
  (`SOURCE_DATE_EPOCH`, stable member ordering, normalized metadata — the build-tool
  adoption wedge) and the **metadata-fidelity boundary** (xattrs/ACLs — threat-model
  C3; read-side is additive later, but write-side fidelity must be a day-one decision
  of the writing spec, since it shapes `add_member` and the round-trip contract).
- **Free-threading position** (threat-model C4) — parallel extraction / parallel
  decode under 3.13t; interacts with the existing parallel-extraction idea above.
- **CLI earlier, as dev tool + demo** — `archivey list/test/extract` was invaluable for
  eyeballing DEV against real archives and is the ten-second demo of safe extraction
  ("the unzip that can't be zip-slipped"). Roadmap moves it right after the native
  readers (see PLAN); a selling point, though not the main one.
