# Tasks — Spike then refactor DecompressorStream → composition

> Spike-first change. **Do not start §2 mechanical migration until §1 open threads are
> closed and recorded in `design.md` Decisions** (remove them from Open Questions).
> Run tools through `uv` (`uv run --no-sync pytest`, `pyrefly check`, `ty check`, `ruff`).
> Related: `seekable-gzip-and-block-writing` must not land another `DecompressorStream`
> subclass leaf in parallel — coordinate or wait.

## 1. Spike — finalize open threads (design.md A–D)

- [ ] 1.1 **Thread A (ResumeHint absolute vs relative):** prototype the cursor helper that
      turns relative `(decomp_delta, comp_delta)` into absolute `SeekPoint`s; implement
      throwaway SeekTable stubs for lzip (point-before) and unix-compress CLEAR
      (point-after). Record the choice under Decisions; delete Open Question A.
- [ ] 1.2 **Thread B (XZ progressive enrichment):** spike moving `_update_index` into an
      `XzSeekTable.record` that may seek/restore `_inner` and attach block `state` on
      SeekPoints; remove stream `isinstance(_XzState)` branching. Keep
      `tests/test_seekable_streams.py` green. Record under Decisions; delete Open Question B.
- [ ] 1.3 **Thread C (deferred TruncatedError / header commit):** spike `.Z` so
      `pending_error` + factory-owned header params recreate CLEAR resumes without a
      format stream subclass overriding `read`/`_decompress_chunk`/`_flush`/`_reset`.
      Record under Decisions; delete Open Question C.
- [ ] 1.4 **Thread D (naming / layout):** grep import sites (`codecs.py`, tests,
      `single_file_reader`); pick `IndexedDecompressStream` vs keeping the
      `DecompressorStream` name; decide module rename now vs later. Record under Decisions;
      delete Open Question D.
- [ ] 1.5 **Thread E (BGZF / zstd fit note):** one short Decision confirming SeekTable
      supports both backwards scans and member walks / progressive points (no code).
      Note coordination with `seekable-gzip-and-block-writing`.

## 2. Core composition scaffold

- [ ] 2.1 Introduce `DecodeOut` / `ResumeHint`, `Decoder` protocol, and `SeekTable`
      (incl. null/no-op table when seekability undeclared) beside the existing seek
      machinery — per Decisions from §1.
- [ ] 2.2 Implement the single stream class (name from 1.4) that owns buffer / pos / eof /
      size / `seek` / `read` / `try_get_size` once and delegates decode + index to
      Decoder + SeekTable. Preserve today’s SEEK_END / scan-to-EOF / truncation behavior.
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
