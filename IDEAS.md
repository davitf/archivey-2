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

- **Pathlib-like navigation** — an `ArchivePath` supporting `/` joining, `iterdir()`,
  `glob()`, `read_bytes()`, `is_dir()`, … over the member tree (precedent: `zipfile.Path`).
  Read-only wrapper; needs random access, so a `DIRECT`/indexed-archive convenience.

- **fsspec integration** — three distinct directions, all useful:
  (1) **expose** an opened archive as an `fsspec` filesystem so pandas/dask/pyarrow/etc.
  read members by path (pairs with the pathlib navigation layer);
  (2) **open** archives *from* an `fsspec` URL (`s3://…/a.zip`, `http(s)://`, …) as the
  `source`; (3) **extract** *to* an `fsspec` location as the `dest`. (2)/(3) mostly mean
  accepting fsspec-opened file objects at the `open_archive`/`extract` boundary.

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

- **Parallel extraction** — extract independent members concurrently for
  `AccessCost.DIRECT` archives (bounded by I/O). Also applies to **solid archives with
  multiple independent blocks** — e.g. a 7z with several solid folders can decompress
  folders in parallel (py7zr does this); members *within* one solid block stay
  sequential. No benefit for a single-block solid archive.

- **Efficient seekable zstd — probably a *native* frame-index reader, not `indexed_zstd`.**
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
