## Why

The `DecompressorStream` / `SegmentedDecompressorStream` / per-format `*DecompressorStream`
hierarchy has grown hard to reason about: three index-building paths, two abstract APIs
(`_create_*` vs `_make_*`), untyped `SeekPoint.state`, and format subclasses that disagree on
seek-point semantics (lzip point-then-advance vs `.Z` advance-then-point). XZ further punches
through the abstraction with a dual decoder (`_XzState` | `_XzBlockChain`). The pending BGZF
change would add yet another stream subclass. We want the half-size composition shape
(one stream + Decoder + SeekTable) before more formats land on the inheritance tax.

This change is a **design spike first**: lock the open threads below, then implement a
behavior-preserving refactor. Caller-visible seek/correctness contracts stay.

## What Changes

- **Spike / design lock**: settle the open threads in `design.md` (hint absolute vs relative,
  XZ progressive enrichment as SeekTable policy, deferred `.Z` truncation hook, BGZF/zstd fit,
  accelerator boundary) with small code spikes where needed — **no behavioral change** until
  those decisions are recorded.
- **Refactor (after spike)**: replace the inheritance tree with composition —
  one `IndexedDecompressStream` owning buffer/seek/EOF once; `Decoder` protocol
  (`feed`/`flush`/`finished`/`recreate`); `SeekTable` policies per format; delete
  `SegmentedDecompressorStream` and the thin `*DecompressorStream` subclasses in
  `decompress.py` / lzip / xz / unix_compress stream tails.
- Keep format parsers and state machines (`_LzipState`, `_XzState`, `_XzBlockChain`, `LzwState`,
  zlib/brotli/… adapters) — essential complexity stays; hierarchy scaffolding goes.
- Accelerators (`rapidgzip` / `_AcceleratorStream`) stay outside this model (foreign `BinaryIO`).
- **Not BREAKING** for the public API: `open_stream` / member-stream seek contracts,
  diagnostics (`SEEK_INDEX_DEGRADED`, `STREAM_REWIND_REDECOMPRESSES`), and truncation rules
  remain as specified today.

## Capabilities

### New Capabilities

<!-- none — internal refactor -->

### Modified Capabilities

- `seekable-decompressor-streams`: add a clarifying requirement that native indexed
  codecs share one seek/EOF/truncation surface (parity matrix). No change to existing
  xz/lzip/.Z/accelerator scenarios — locks the refactor’s non-divergence invariant so
  BGZF and later native indexes cannot fork seek semantics.

## Impact

- **Modules**: `internal/streams/decompressor_stream.py`, `decompress.py`, `xz.py`, `lzip.py`,
  `unix_compress.py`, and the codec `open()` wiring in `codecs.py` that constructs these streams.
- **Public API**: none intended (types/behavior unchanged).
- **Extras/deps**: none.
- **Tests**: existing `test_seekable_streams`, codec/stream input tests, and unix-compress seek
  matrices are the acceptance gate; spike tasks may add focused unit tests for Decoder/SeekTable
  seams once shapes stabilize.
- **Related in-flight**: `seekable-gzip-and-block-writing` (BGZF as another `DecompressorStream`
  subclass) should wait on or re-target this composition model; do not land another inheritance
  leaf in parallel without coordinating.
