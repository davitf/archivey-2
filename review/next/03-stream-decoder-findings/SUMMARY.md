# Brief 3 — Seekable decoder layer, accelerators & vendored LZW: findings

Deep review of the post-#96 seekable decompressor layer, the #105 rapidgzip hot
path, and the #89 vendored LZW core, following `review/next/03-stream-decoder-layer.md`.
Docs-only change — no source is modified. Every finding is traced to `file:line`
with a runnable reproduction (`repro.py`).

## Headline

**The composition refactor (#96) is structurally sound** — the base
`DecompressorStream` really is format-agnostic, `recreate()` rebuilds decoder state
cleanly on every seek (no buffer/CRC/CTR carryover), and lzip's seek path is correct.
The bugs are at the **seams the refactor and the accelerator work introduced**: the
seek-point *merge* invariant that xz is supposed to uphold but doesn't, and two
places where **truncated input is silently accepted** instead of raising — one on the
new rapidgzip hot path (#105), one in the base read loop that only the vendored LZW
decoder (#89) exercises. Two of the three touch VISION claim #3 ("damaged input is a
first-class citizen"); one is a hard crash on valid input.

None of these is caught by the current suite: every seek test reads forward-to-EOF
*before* seeking, which is exactly the ordering that hides F1, and no test reads a
truncated stream through both the `read(-1)` and chunked idioms.

## Baseline (captured green)

`uv run pytest` → **1555 passed, 120 skipped**. `pyrefly` 0 errors, `ty` all-pass,
`ruff` clean. Config `[all]` (rapidgzip 0.16.0, py7zr 1.1.3, brotli 1.2.0, inflate64
1.0.4, pyppmd 1.3.1, ncompress 1.0.2, cryptography 49). F1/F3/F4 reproduce in **every**
dependency config (stdlib `lzma`/`zlib` + core); F2 needs the `[seekable]` extra
(rapidgzip) and does **not** affect `[core-only]`, where the stdlib backend is used and
raises correctly.

## Findings

| # | Sev | Where | One-liner |
|---|-----|-------|-----------|
| F1 | **High** | `decompressor_stream.py:251`, `xz.py:564` | A valid multi-stream `.xz` read with the everyday *seek-to-end-then-read* (size probe) pattern trips `assert False` in `_resolve_same_offset_collision` → `AssertionError` (a raw crash, not an `ArchiveyError`). Under `python -O` the assert is compiled out and the point is silently dropped. The `add_seek_points` comment claims "xz/lzip should filter those away" — that invariant is violated. |
| F2 | **High** | `codecs.py:953` / `:987` / `:365` | On the #105 hot path, a truncated **deflate/zlib** stream decoded through rapidgzip **silently returns partial data with no error**, where the stdlib backend raises `TruncatedError`. DEFLATE/ZLIB have no truncation backstop at all; the gzip ISIZE backstop (`_GzipTruncationCheckStream`) is defeated by any truncated payload whose tail contains a false `1f 8b 08` (near-certain for the >1 MiB incompressible members that trip the AUTO gate). VISION #3 regression. |
| F3 | **Medium** | `decompressor_stream.py:286`, `:307` | `read(-1)`/`readall()` never consult `pending_error`, so a truncated `.Z` read with the `f.read()` idiom returns partial data and **swallows** the `TruncatedError` that the *same input* raises when read in chunks. Only the vendored LZW decoder uses the deferred-error path, so only `.Z` is affected — but the root cause is in the base. VISION #3. |
| F4 | Low-Med | `unix_compress.py:51` | The `.Z` `maxbits` header field is bounded to ≤ 31 by the mask but never to the format's real ceiling of 16, so archivey accepts out-of-spec `.Z` files that `compress`/`ncompress` reject and permits a larger-than-standard code table. Input-proportional (not a tiny-file OOM), so severity is low. |
| F5 | Med (test gap) | `tests/test_seekable_streams.py` | No randomized/property seek test exists (old review finding #6, still open). Every seek test reads forward-to-EOF before seeking — the one ordering that cannot expose F1. A seek-math property test across build-index-first vs read-first orderings would have caught it. |

Details and reproductions: `seek-index.md` (F1, F5), `accelerators.md` (F2),
`vendored-lzw.md` (F3, F4). Maintainer decisions: `QUESTIONS.md`.

## What's actually fine (verified, not assumed)

- **The base is genuinely format-agnostic.** No format constant leaked into
  `DecompressorStream`; the before/after placement asymmetry lives entirely in the
  decoders (`lzip.py:275` before, `unix_compress.py:374` after), as the design claims.
- **`recreate()` carries no stale state.** Traced every decoder: `ZlibDecoder`,
  `BrotliDecoder`, `PpmdDecoder`, `BcjDecoder`, `Deflate64Decoder`, `XzDecoder`,
  `LzipDecoder`, `UnixCompressDecoder` all construct fresh backend objects in
  `recreate` — no buffer, CRC, or CTR accumulator survives a seek reset. `pending_error`
  is cleared on reset (`decompressor_stream.py:264`).
- **lzip's seek index is immune to F1.** Every lzip point carries `state=None`, so
  same-offset collisions resolve as forward-refinement last-wins, never the assert
  (verified: `repro.py` runs the lzip analogue of F1 without incident).
- **XZ progressive enrichment save/restore is airtight single-threaded.** The
  `inner.tell()`/`try…finally: inner.seek(saved_pos)` around the footer scan
  (`xz.py:574-603`) always restores position. (Concurrency is an explicitly documented
  non-promise; not a finding.)
- **The LZW kernel's hostile-input core is careful.** KwKwK (`code == next_code`),
  `code > next_code` rejection, and the `prev_entry is None` first-code guard are all
  correct (`unix_compress.py:244-259`); the dictionary is truncated on CLEAR and at
  flush; growth is input-proportional (no small-file OOM bomb). The BSD-3 `uncompresspy`
  notice is intact and complete — no runtime import of the upstream package
  (`unix_compress.py:412-443`; `grep` confirms no `import uncompresspy`).
- **xz/lzip truncation is honest regardless of read style** — both raise via the
  `not self._decoder.finished` path (`decompressor_stream.py:279`), which fires inside
  the flush chunk, so `read(-1)` and chunked reads agree (verified). F3 is specific to
  the *deferred* `pending_error` mechanism that only `.Z` uses.
- **The AUTO size-gate boundary is consistent** — the `input_size < min_size` test
  (`config.py:62`) is strict, so a member exactly at 1 MiB goes to rapidgzip and both
  backends produce identical bytes for valid input; the only divergence is the F2
  truncation behaviour.

## Non-goals honored

The composition refactor, the SeekTable-vs-decoder decision, and the five forward-only
adapters are **not** re-litigated (all settled in the #96 design). BGZF's absence is
not treated as a finding. F1 is reported as a correctness bug in the *result*, not a
challenge to the folding of index discovery into the decoder.
