# Compression-library analysis

This is the single source of truth for **which library backs each codec Archivey reads, and
why**. For every codec it records the chosen backend, the alternatives weighed, and the
criteria behind the decision, so a future contributor does not have to re-litigate a choice
(or rediscover a library's quirks) from scratch.

Every decision is documented **in full here**, even when it was originally made elsewhere, so
this doc stays self-contained as the predecessor repository is retired. Where a choice has an
external origin — notably the native XZ parser, first implemented in
[`davitf/archivey-dev#214`](https://github.com/davitf/archivey-dev/pull/214) — the link is kept
only as provenance, not as a stand-in for the rationale.

Archivey reads codecs through one uniform, pull-based stream layer
(`compressed-streams`); format parsers compose those streams instead of calling codec
libraries directly. Random access *inside* a compressed stream is a separate concern owned by
`seekable-decompressor-streams`. The packaging contract (which extra pulls which library)
lives in `packaging-and-extras`; this doc explains the *reasoning* the extras encode.

## How each candidate is scored

| Criterion | What it captures |
|-----------|------------------|
| **Non-seekable source** | Works on a forward-only pipe/socket (no `fileno`, no `seek`)? |
| **Efficient seeking** | Indexed/random access without re-decompressing from the start? |
| **Corruption detection** | Raises on bad data instead of silently returning garbage? |
| **Truncation detection** | Raises on a short/cut stream instead of a silent short read? |
| **Error-reporting fidelity** | Are errors distinguishable and translatable to our `CorruptionError` / `TruncatedError` / non-seekable error? |
| **Install / availability** | Pure-Python vs. native wheels; platform/arch coverage; build deps |
| **Maintenance** | Activity, releases, Python-version support |

Two recurring notes:

- **"Re-decode rewind" is acceptable, not failure.** A codec with no native index services a
  backward seek by re-decompressing from the start — O(n) per rewind. Per
  `seekable-decompressor-streams` this is permitted but never silent: the first rewinding seek
  logs a warning. So "no efficient seeking" is a quality-of-implementation note, not a
  disqualifier.
- **Container CRCs are a second integrity net.** Even when a codec does not detect corruption
  itself, the `compressed-streams` verification stage checks the container-supplied digest
  (e.g. a ZIP/7z member CRC32) over the decompressed bytes at clean EOF. Standalone single-file
  streams (`.gz`, `.zst`, …) without a member digest rely on the codec's own check.

## Summary: chosen backend per codec

| Codec | Chosen backend | Availability | Efficient seek | Corruption | Truncation |
|-------|----------------|--------------|----------------|------------|------------|
| gzip | stdlib `gzip` (+ `rapidgzip` for random access) | core (`[seekable]` for accel) | via `rapidgzip` | yes (CRC) | yes¹ |
| bzip2 | stdlib `bz2` (+ `rapidgzip.IndexedBzip2File`) | core (`[seekable]` for accel) | via `rapidgzip` | yes (block CRC) | yes |
| xz | native `xz.py` over stdlib `lzma` | core | **yes** (block index) | yes (CRC) | yes |
| lzip | native `lzip.py` over stdlib `lzma` | core | **yes** (trailer scan) | yes (CRC) | yes |
| LZMA1/LZMA2 (raw) | stdlib `lzma` `FORMAT_RAW` | core | n/a (container-owned) | yes | yes |
| Delta, BCJ x86/ARM/ARMT/PPC/SPARC/IA64 | stdlib `lzma` raw filters; LZMA1+BCJ stages BCJ via `pybcj` | core (LZMA2+BCJ); `[7z]` (`pybcj`) for LZMA1+BCJ | n/a (filter stage) | yes | yes |
| raw Deflate / zlib | stdlib `zlib` | core | no (rewind) | yes | yes |
| zstd | **stdlib `compression.zstd` (3.14+) / `backports.zstd` (<3.14)** | `[zstd]` on <3.14; core on 3.14+ | no (rewind) | yes (frame checksum) | **yes** |
| lz4 | `lz4` | `[lz4]` | no (rewind) | yes | yes |
| brotli | `brotli` | `[7z]` | no (rewind) | yes | partial² |
| unix-compress (`.Z`) | native `unix_compress.py` (LZW) | core | **yes** (CLEAR seek points) | yes | best-effort³ |
| Deflate64 | `inflate64` | `[7z]` | no | yes | yes |
| PPMd (var.H) | `pyppmd` | `[7z]` | no | yes | yes |

¹ gzip truncation: stdlib raises `EOFError` (sized reads can yield a correct prefix first).
The `rapidgzip` accelerator often soft-EOFs by design (empty or short/full with no exception).
Archivey backstops **path** gzip with empty→stdlib fallback plus a single-member ISIZE
compare (`seekable-decompressor-streams`; see `rapidgzip-upstream-report.md`). Multi-member
ISIZE summing is deferred. Container members still rely on container CRC/`VerifyingStream`.
² brotli has no length/CRC trailer, so a truncated stream is detected only when the
decompressor never reports "finished" at EOF (surfaced as `TruncatedError`), not by a stored
size.
³ `.Z` has no length/checksum; finished compressors zero-pad after the last complete code, so
nonzero leftover bits at EOF are a best-effort `TruncatedError` (raised on the next empty
`read()` after delivering bytes). Cuts that leave only zero leftover bits stay silent.

---

## zstd — the open question (decision: migrate off `zstandard`)

zstd is the one codec whose choice was actively in doubt. The current backend, `zstandard`,
has two real warts; the question was which of several candidates to move to.

### Candidates

- **`zstandard`** (current) — Gregory Szorc's CFFI/C wrapper of libzstd. Bespoke reader API.
- **`pyzstd`** — Ma Lin's (animalize) wrapper; its `ZstdFile` is built on the stdlib
  `_compression` machinery (same family as `gzip`/`bz2`/`lzma`). It was the basis for CPython's
  stdlib module (PEP 784) and, as of 0.19, **depends on `backports.zstd` for Python < 3.14**.
  Also ships `SeekableZstdFile`.
- **stdlib `compression.zstd`** (Python 3.14+) — the new standard-library module; same
  `ZstdFile`/`ZstdError`/`open` surface.
- **`backports.zstd`** (Rogdham) — a pure backport of `compression.zstd`'s API for Python
  3.10–3.13 (no third-party deps).
- **`indexed_zstd`** (martinellimarco) — efficient *seeking* over arbitrary `.zst`; see the
  seekable section.

### Measured behaviour

Probed directly on Python 3.11 with a 200 KB incompressible (multi-block) frame:

| Behaviour | `zstandard` 0.25 | `pyzstd` 0.19 | `backports.zstd` 1.6 / stdlib 3.14 |
|-----------|------------------|---------------|------------------------------------|
| Truncation (cut stream) | ❌ **silent short read** | ✅ `EOFError` | ✅ `EOFError` |
| Corruption, frame checksum on | ✅ `ZstdError` | ✅ | ✅ `ZstdError` |
| Corruption, no checksum | ❌ silent | ❌ silent | ❌ silent (inherent to zstd: the default frame has no integrity check) |
| Backward seek | ❌ raises `OSError` → needs the `_ZstdReopenStream` reopen-from-start hack | ✅ rewinds in place | ✅ rewinds in place |
| Non-seekable forward read | ✅ | ✅ | ✅ |
| Reader API family | bespoke | `_compression`-based | `_compression`-based |

The two `zstandard` warts are the ones called out in the proposal: it **silently** short-reads
a truncated stream (no `TruncatedError`), and its reader **cannot seek backward**, which is why
the current code wraps it in `_ZstdReopenStream` (close, rewind the source, reopen, re-decode
forward).

### Decision

**Migrate the zstd decode backend off `zstandard` to the stdlib `compression.zstd` line.** The
recorded target is:

- **Python 3.14+:** use the stdlib `compression.zstd`.
- **Python 3.11–3.13:** use `backports.zstd` (the same `compression.zstd` API). Because
  `pyzstd >= 0.19` depends on `backports.zstd`, an environment that installs `pyzstd` for any
  reason also satisfies this — so the decode path can target the **`compression.zstd` /
  `backports.zstd` API uniformly** (`compression.zstd` if importable, else `backports.zstd`)
  and need not import `pyzstd` directly for plain decode.

Why, against the alternatives:

- It **fixes both warts at once**: truncation surfaces as `EOFError` (→ our `TruncatedError`,
  exactly as for `gzip`/`bz2`/`lzma`), and the reader rewinds in place, so `_ZstdReopenStream`
  and its special-case can be **deleted** when the swap lands.
- It gives **API-family parity** with the other stdlib codecs (`_compression.DecompressReader`),
  so the error taxonomy and the rewind-warning behaviour are uniform with gzip/bz2/lzma.
- It is **future-proof toward the standard library** — the long-term backend is `compression.zstd`,
  and `backports.zstd` is just the same API on older Pythons.
- `zstandard` is rejected as the primary decoder for the two warts above (its only edge,
  bespoke streaming features, Archivey does not use).
- `pyzstd` is **not** named as the direct dependency: it would work (identical behaviour in the
  table, plus `SeekableZstdFile`), but targeting the `compression.zstd`/`backports.zstd` API is
  the smaller, more future-aligned surface, and `pyzstd` pulls `backports.zstd` anyway. `pyzstd`
  remains relevant only if/when the *Seekable Zstd* container is supported.

**Status / packaging:** the decode backend is stdlib `compression.zstd` (3.14+) /
`backports.zstd` (3.11–3.13). The `[zstd]` extra pins `backports.zstd` on older
Pythons only; `_ZstdReopenStream` has been removed. `pyzstd` was previously pinned in
`[all]` purely as a test-fixture generator and is now removed (the active test suite
generates zstd fixtures with `backports.zstd`; only the (since-retired) frozen `tests/_dev_oracle`
referenced `pyzstd`, and it guards for its absence).

### Seekable zstd (efficient random access) — none for now

Decision: **no efficient zstd seeking yet** — a backward seek re-decompresses from the start and
warns, the same as brotli/lz4/zlib (`seekable-decompressor-streams`, "index-less codecs warn on a
rewinding seek"). The candidates were:

- **`indexed_zstd`** — gives O(1) seeking over arbitrary `.zst` via `libzstd-seek`, but **only at
  frame granularity** (its jump table maps frame boundaries; a seek into a frame decodes forward
  from the frame start — there is no intra-frame state checkpointing like `rapidgzip`).
  **Deferred, and possibly unnecessary:** it is a Cython/C++17 extension "based on
  `indexed_bzip2`" that statically bundles a C++ core, carrying the *same class* of macOS
  dual-load symbol-collision risk that forced Archivey onto a single accelerator library
  (`known-issues.md`). And frame-granularity seeking is *exactly* what Archivey's own
  `_SegmentedDecompressorStream` already provides for xz and lzip — so a small **native zstd
  frame-index reader** reusing that infrastructure would likely give the same seeking with no
  heavy dependency and no macOS risk. The note is no help for the common **single-frame** `.zst`
  either way (one frame → one seek point). This is registered in `IDEAS.md` (Performance &
  robustness), framed as "evaluate native frame-index reuse before depending on `indexed_zstd`".
- **`pyzstd.SeekableZstdFile`** — rejected as a general answer: it reads only the *Seekable Zstd*
  container (a seek table stored in a trailing skippable frame), **not** arbitrary `.zst`, so it
  cannot give random access to ordinary zstd streams or `.tar.zst`.

---

## xz — native parser over stdlib `lzma`

**Decision:** XZ is read by Archivey's **own** `internal/streams/xz.py` over stdlib `lzma`, not
by any third-party library. (Originally implemented in
[`davitf/archivey-dev#214`](https://github.com/davitf/archivey-dev/pull/214); the full rationale
is recorded below so this doc stands on its own.)

### Why XZ allows a native seekable reader

An XZ file is a sequence of one or more independent **streams** (optionally separated by 4-byte
null padding). Each stream ends with a 12-byte **footer** that points back to an **index**
recording, for every **block** in the stream, its compressed (unpadded) and uncompressed sizes.
So the per-block uncompressed offsets can be reconstructed by reading only footers and indices —
**no decompression** — and any uncompressed offset can then be reached by decoding just the
block(s) that contain it. This is the same structural property `lzip` has (a trailer with sizes),
which is why both share one framework.

### Alternatives considered

- **stdlib `lzma.open` (the previous default)** — correct, zero-dep, but **cannot seek
  efficiently**: a `SEEK_END` (or any backward seek) re-decompresses the entire file, and the
  old single-file metadata path read only the *last* stream's index, so it **reported the wrong
  size for multi-stream XZ files**. Rejected as the seeking/metadata backend (still the
  underlying codec — the native parser drives `lzma` block by block).
- **[`python-xz`](https://github.com/Rogdham/python-xz)** — does give block-level random access,
  but: it is an **external dependency**, it **requires a seekable input** (no forward-only/pipe
  support), and it always performs an **upfront full index scan** on open. Rejected: a native
  parser on stdlib `lzma` removes the dependency and lifts both limitations.

### What the native parser does (`XzDecompressorStream`)

- **Block-level random access** via the XZ stream index: a seek jumps to the block containing the
  target offset and decodes forward within it, not from the start of the file. (Block-level, not
  just stream-level, because the index already carries per-block records — and a typical
  single-stream `.tar.xz` is one stream with one large block, where stream-level seeking would
  give only a single useless seek point at offset 0.)
- **Efficient `SEEK_END`** via a backward index scan: walk streams from EOF, reading each
  footer + index (and skipping 4-byte stream padding) to build the block table without
  decompressing anything.
- **Block decompression via a synthetic single-block XZ stream**: each block's raw bytes are
  wrapped in a minimal complete XZ stream (`[stream header][block][index+footer]`) and fed to
  `lzma.LZMADecompressor(format=FORMAT_XZ)`. The three values needed to synthesize the wrapper —
  `check`, `unpadded_size`, `uncompressed_size` — all come from the index, so the block header's
  filter chain never has to be parsed by hand (`lzma` parses it). (This is the same technique
  `python-xz` uses internally.)
- **Correct multi-stream handling**, both forward (a `NEED_HEADER` ↔ `IN_STREAM` state machine
  that consumes concatenated streams and inter-stream padding) and backward (the scan walks every
  stream), fixing the old last-stream-only size bug. During a forward read, each completed stream
  triggers a mini backward scan of just that stream to populate its block seek points
  progressively.
- **Graceful fallbacks**: a non-seekable source decodes sequentially (no index built); an
  index-less or truncated stream falls back to per-stream sequential scanning; a corrupt index is
  logged and skipped rather than failing the read.
- **`file_size` is read from the stream** (via the backward scan on open) rather than a separate
  metadata pass, so XZ and lzip populate size uniformly with no per-format code.
- **Architectural uniformity**: shares the `_SegmentedDecompressorStream` framework with
  `LzipDecompressorStream`.

Out of scope (matching the original decision): XZ *writing*, parallel/threaded decode, and a
block-reader LRU cache.

### `python-xz` is not a dependency at all

DEV kept `python-xz` only as a *disabled-by-default* comparison/benchmark backend. **v2 did not
carry that over** — there is no `use_python_xz` config, no `src/` import, and the test oracle
does not use it — so the `python-xz` pin that lingered in `[all]` was entirely dead and has been
**removed** (`packaging-and-extras`).

---

## The rest — chosen library, rejected alternatives, reason

### gzip — stdlib `gzip`, accelerated by `rapidgzip`

Default decode is stdlib `gzip` (zero-dep core). For **random access**, the optional
`rapidgzip` (`[seekable]`) builds an index for true seeking; without it, stdlib `gzip` seeks by
re-decompressing from the start (rewind warning). `rapidgzip` is the single accelerator library
for both gzip and bzip2 — see bzip2 below and `known-issues.md` for why the standalone
`indexed_gzip`/`indexed_bzip2` are not used. Truncation: stdlib raises `EOFError`; `rapidgzip`
soft-EOFs by design — Archivey uses empty→stdlib fallback + single-member ISIZE on path
sources (`seekable-decompressor-streams`, `rapidgzip-upstream-report.md`).

### bzip2 — stdlib `bz2`, accelerated by `rapidgzip.IndexedBzip2File`

Default decode is stdlib `bz2`. Random access uses **`rapidgzip`'s bundled `IndexedBzip2File`**,
*not* the standalone `indexed_bzip2` package: loading both `rapidgzip` and `indexed_bzip2` into
one process corrupts the heap and aborts on macOS (overlapping statically-linked C++ symbols
coalesced by dyld). Routing both gzip and bzip2 through `rapidgzip` keeps a single accelerator
library in the process. This is the **single-accelerator macOS constraint** documented in full in
[`known-issues.md`](known-issues.md) and matches the rapidgzip author's own guidance.

### lzip — native `lzip.py` over stdlib `lzma`

Read by Archivey's own `LzipDecompressorStream` (the framework `xz.py` later reused): stdlib
`lzma` provides the LZMA1 codec, and the lzip member trailer (CRC32 + sizes) is scanned for
efficient seeking and size reporting. Zero-dep core; no third-party lzip library is needed or
preferred.

### LZMA1 / LZMA2 (raw) and the filter stages (Delta, BCJ family)

Raw LZMA1/LZMA2 (7z/ZIP coder streams) use stdlib `lzma` in `FORMAT_RAW` with the coder's filter
properties. Delta and BCJ-over-**LZMA2** compose into one stdlib filter chain (zero-dep core).
**LZMA1+BCJ** is different: combining them in one liblzma `FORMAT_RAW` chain can silently
truncate the final BCJ look-ahead bytes when LZMA1 lacks an EOS marker (common from the
7-Zip CLI; see BPO-21872 / xz-devel). Archivey stages LZMA1 via stdlib and BCJ via
`pybcj` (import name `bcj`) under the `[7z]` extra — the same approach py7zr uses.
BCJ2 remains unsupported.

### raw Deflate / zlib — stdlib `zlib`, accelerated by `rapidgzip`

Raw deflate (`-15`, ZIP/7z members) and zlib-wrapped deflate default to stdlib `zlib`. For
**random access**, the same `[seekable]` `rapidgzip` accelerator used for gzip also decodes
raw DEFLATE and zlib natively (auto-detected; no synthetic gzip wrapper) as of 0.16.0.
Selection matches gzip (`use_rapidgzip` × declared seekability × availability), plus the
`AUTO` minimum compressed-size gate (`RAPIDGZIP_AUTO_MIN_COMPRESSED_SIZE`, 1 MiB) so tiny
members do not pay accelerator setup. Without the accelerator, a rewind re-decodes from the
start (warning naming `[seekable]`). Standalone accelerated zlib/deflate has no Adler-32 /
ISIZE-style truncation backstop (accepted limitation; container CRC covers ZIP/7z members).

### lz4 — `lz4`

The `lz4` package's frame reader (`lz4.frame`). Standard, broadly-wheeled, actively maintained.
Forward-only seek (rewind + warning). No compelling alternative for the LZ4 frame format in
Python.

### brotli — `brotli`

Google's `brotli` package, used via its incremental `Decompressor` (it exposes no file-like
`open()`, so Archivey wraps it in `BrotliDecompressorStream`). Brotli has no magic and no
length/CRC trailer, so it is detected by trial-decoding a bounded prefix, and truncation is
caught only by "never finished at EOF". Pulled by the `[7z]` bundle (7z can use Brotli) and used
for standalone `.br`. The `brotlicffi` fork is an alternative for PyPy but adds nothing on
CPython.

### unix-compress (`.Z`) — native LZW

LZW (`.Z`) decode via Archivey's native `LzwState` / `UnixCompressDecompressorStream`
(adapted from uncompresspy under BSD-3-Clause attribution). Forward decode works on
non-seekable sources; on a seekable source with seekability declared, CLEAR boundaries
become `SeekPoint`s. Reserved header flags (`0x60`) raise `UnsupportedFeatureError`.
Truncation is best-effort via nonzero leftover bits after the last complete code
(`TruncatedError` on the next empty `read()`). `ncompress` remains a *compressor* only
(test-fixture generator in the `dev` group).

### Deflate64 — `inflate64`

Deflate64 (a.k.a. Enhanced Deflate, used by some ZIP/7z members) via `inflate64`. The de-facto
Python implementation (same author as `pyppmd`/`py7zr`); no real alternative. `[7z]` bundle.

### PPMd (var.H) — `pyppmd`

PPMd variant H (7z) via `pyppmd`. The only maintained Python PPMd binding. `[7z]` bundle.
(Concrete construction lands with the native 7z reader in Phase 6; the backend selection and
missing-dependency gating are already wired.)

---

## Test-only libraries (not runtime, not in any user-facing extra)

These live in the `dev` dependency group, never an extra (`packaging-and-extras`): `py7zr` and
`rarfile` (decode **oracles** to cross-check the native 7z reader and RAR metadata parser),
`ncompress` (an LZW **compressor** to generate `.Z` fixtures for the native unix-compress
decoder). The active suite uses `backports.zstd` (or stdlib `compression.zstd` on 3.14+) for
zstd fixture generation.

A guard test (`tests/test_extras_imported.py`) asserts that every package pinned in a
user-facing extra is actually imported by some `src/` code path (with a small, documented
allowlist for features whose implementation is a later phase — currently empty), so a
dead or test-only dependency cannot slip back into an extra. `py7zr` is a **dev** oracle
only (7z writing is not shipped as a user-facing extra).

## Follow-up changes

The decisions above that imply further work are tracked separately:

- **Efficient seekable zstd** — optional; **evaluate a native frame-index reader first**
  (reusing the xz/lzip `_SegmentedDecompressorStream`), since `indexed_zstd` only seeks at frame
  granularity, which that infrastructure already provides — avoiding the heavy C++ dependency and
  its macOS coexistence risk. Tracked in `IDEAS.md`.
