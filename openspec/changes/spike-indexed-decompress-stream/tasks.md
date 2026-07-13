# Tasks — Spike then refactor DecompressorStream → composition

> Spike-first change. **Do not start §2 mechanical migration until §1 open threads are
> closed and recorded in `design.md` Decisions** (remove them from Open Questions).
> Run tools through `uv` (`uv run --no-sync pytest`, `pyrefly check`, `ty check`, `ruff`).
> Related: `seekable-gzip-and-block-writing` must not land another `DecompressorStream`
> subclass leaf in parallel — coordinate or wait.

## 1. Spike — finalize remaining open thread (B); A/C/D/E locked in design.md

- [x] 1.1 **Thread A (ResumeHint absolute vs relative):** locked — relative units +
      SeekTable before/after policy; enrichment may inject absolute points.
- [ ] 1.2 **Thread B (XZ progressive enrichment):** still open — choose fat SeekTable /
      thin+hook / Enricher (see design Open Question B). Spike the chosen shape so
      `isinstance(_XzState)` branching leaves the stream; keep
      `tests/test_seekable_streams.py` green. Record under Decisions; delete Open Question B.
- [x] 1.3 **Thread C (deferred TruncatedError):** locked — formal `Decoder.pending_error`
      Protocol property (not duck-typed); stream raises on next empty read.
- [x] 1.4 **Thread D (naming / layout):** locked — keep class name `DecompressorStream`
      and module `decompressor_stream.py`; adapters beside codecs; no `Indexed…` rename.
- [x] 1.5 **Thread E (BGZF / zstd fit):** locked — SeekTable supports progressive points,
      backwards scans, and forward member walks; coordinate with
      `seekable-gzip-and-block-writing`.

## 2. Core composition scaffold

- [ ] 2.1 Introduce `DecodeOut` / `ResumeHint`, `Decoder` protocol, and `SeekTable`
      (incl. null/no-op table when seekability undeclared) beside the existing seek
      machinery — per Decisions from §1.
- [ ] 2.2 Implement `DecompressorStream` (kept name) that owns buffer / pos / eof /
      size / `seek` / `read` / `try_get_size` once and delegates decode + index to
      Decoder + SeekTable. Preserve today’s SEEK_END / scan-to-EOF / truncation behavior.
      `Decoder.pending_error` is a formal Protocol property.
- [ ] 2.3 Delete `SegmentedDecompressorStream` once no subclass remains.

## 3. Migrate codecs onto Decoder + SeekTable

- [ ] 3.1 Migrate zlib / brotli / ppmd / deflate64 / bcj to thin Decoder adapters
      (`hints=[]`); remove `DecompressorStream` subclasses from `decompress.py`.
- [ ] 3.2 Migrate lzip: keep `_LzipState` + backwards trailer scan; stream subclass tail
      becomes factory wiring + `LzipSeekTable`.
- [ ] 3.3 Migrate xz: keep `_XzState` / `_XzBlockChain` / index parsers; `recreate(point)`
      selects decoder; SeekTable owns progressive + `build_full` (from spike 1.2).
- [ ] 3.4 Migrate unix-compress: keep `LzwState`; wire CLEAR hints + deferred truncation
      per spike 1.3; remove format-specific stream overrides.
- [ ] 3.5 Update `codecs.py` / any `isinstance(DecompressorStream)` call sites
      (`single_file_reader`, tests) to the new type/name.

## 4. Verify

- [ ] 4.1 `uv run --no-sync pytest tests/test_seekable_streams.py tests/test_stream_inputs.py
      tests/test_codecs.py` (plus unix-compress / compressed-stream seek coverage) green.
- [ ] 4.2 `uv run --no-sync pyrefly check` and `uv run --no-sync ty check` clean on touched
      modules; `uv run --no-sync ruff check` / `ruff format --check`.
- [ ] 4.3 Confirm no new divergent seek implementation for native indexed codecs (shared
      surface delta in `seekable-decompressor-streams`).
- [ ] 4.4 `openspec validate --strict spike-indexed-decompress-stream`.
- [ ] 4.5 Before push: three-config suite per `CONTRIBUTING.md` (`[all]`, `[all-lowest]`,
      `[core-only]`), then restore `uv sync --group dev --extra all`.
