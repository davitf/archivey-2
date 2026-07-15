# Brief 3 — Seekable decoder layer & accelerators: findings

**Reviewer pass:** correctness + hostile-input review of the post-#96 stream/decoder
layer (`decompressor_stream`, `decompress`/`xz`/`lzip`/`unix_compress`, `codecs`
accelerator path), as it stands at `38a7d72` (#119). Not a re-litigation of the
composition refactor — that decision is treated as settled per the brief.

## Baseline (green before hunting)

- `uv run pytest -x -q` (config `[all]`): **1555 passed, 120 skipped, 4 warnings** in ~43s.
- Optional backends present: `rapidgzip 0.16.0` (the pinned floor), `lzma`, `brotli`,
  `pyppmd`, `inflate64`, `lz4`, `IndexedBzip2File`.
- Type/lint not re-run for this read-only review pass (no source changes proposed here);
  the findings below ship with reproducers, not patches.

Every finding below is reproduced by a script under
`review/next/03-stream-decoder-layer/repro/` and traced to `file:line`.

## Top findings

| # | Sev | Finding | Where | VISION | Status |
|---|-----|---------|-------|--------|--------|
| F1 | **High** | Crafted `.xz` with ≥2 zero-`uncompressed_size` blocks trips `assert False` → **AssertionError crash** when the seek index is built (`seek(0, SEEK_END)` / `try_get_size`). | `decompressor_stream.py:251`; reached via `xz.py:608`/`xz.py:582` | #2 | Open |
| F2 | **High** | Accelerated **deflate/zlib** silently swallow **truncation *and* mid-stream corruption** — 0/partial bytes, no error — where stdlib raises `TruncatedError`. No backstop exists for deflate/zlib (only gzip+path). | `codecs.py:953`, `codecs.py:987` | #3 | Open |
| F3 | **Med** | LZW decodes an entire `feed()` eagerly with no output budget; the base buffers the whole thing. A **52 KB `.Z` → 450 MB** buffered on `read(1)` (peak 1.4 GB). `maxbits` accepted up to 31 (real `compress` caps at 16), so the dictionary ceiling is 2³¹, not 2¹⁶. | `unix_compress.py:143`, `unix_compress.py:51`; `decompressor_stream.py:302` | #2/#4 | Open |
| F4 | **Med** | `.Z` truncation is deferred via `pending_error` and **not raised on a single-shot `read(-1)`/`readall()`** — partial bytes returned, error only on the *next* read. xz/lzip raise on the first `read(-1)`. | `decompressor_stream.py:286`, `unix_compress.py:347` | #3 | Open |
| F5 | Low | No property-based seek-math test exists (old finding #6 still open); seek tests are example-based over fixed offsets. | `tests/test_seekable_streams.py` | — | Open |

## Headline

The refactor itself is clean — the base is genuinely format-agnostic, the
before/after placement policy lives entirely in the decoders, and lzip/xz/unix-compress
each emit their own absolute `SeekPoint`s exactly as the #96 design describes. The
correctness problems are **not** in the collapse; they are in two spots the collapse
*inherited or newly exposed*:

1. **The collision assert became a hostile-input crash surface (F1).** The #96 PR body
   itself flagged the "assert on non-origin collisions" as a follow-up. It is reachable:
   XZ is the one decoder that stores a non-`None` `state` (block bounds) on its points, so
   two same-offset points carry *distinct* objects and fall straight through to
   `assert False`. lzip's points carry `state=None`, so its same-offset collisions
   resolve last-wins and are safe — which is exactly why this bites XZ and only XZ.

2. **The accelerator hot-path change (#105) widened a silent-truncation hole (F2).** The
   gzip ISIZE backstop was never extended to deflate/zlib, which now ride rapidgzip by
   default (AUTO, seekable, ≥1 MB). Those two codecs have no length trailer to check, so a
   truncated or corrupt body returns nothing (or a partial prefix) with no error — the
   opposite of the "damaged input is a first-class citizen" promise.

The vendored LZW (F3) is a faithful port of `uncompresspy` — including its *absence* of a
16-bit `maxbits` cap — dropped into the trusted zero-dep core, where the eager-decode
buffering of the base stream turns LZW's unbounded amplification into a real OOM lever.

## Where I disagree / what is actually fine

Precise negatives, since the brief asked for them:

- **The composition refactor is correct.** The base carries no format knowledge; the
  before/after asymmetry is entirely inside the decoders (`lzip.py:275` before-advance vs
  `unix_compress.py:374` after-advance). `add_seek_points`' origin-refinement path
  (`decompressor_stream.py:212`) correctly commits the unix-compress `SeekPoint(0, 3)`
  header shift even with indexing off.
- **XZ progressive-enrichment `inner` save/restore is airtight** under the sync contract:
  `xz.py:575` saves `tell()`, the scan uses absolute seeks, and `finally: seek(saved_pos)`
  (`xz.py:603`) restores unconditionally. The base *also* defensively restores in
  `_ensure_index_built` (`decompressor_stream.py:335`). The "concurrent reader observes the
  moved position" worry does not apply: v1 is sync-only and member streams are not shared
  across threads without an external lock.
- **Forward-only decoders carry no state across `recreate`** — each returns a freshly
  constructed backend object (`decompress.py:30/58/100/136/165`); and they are only ever
  recreated at the origin anyway (no seek points).
- **The `_AcceleratorStream` finalizer is correct and at the birth site** — `weakref.finalize`
  on a `staticmethod` that takes the raw inner (no `self` capture), holding a strong ref so
  `close()` runs before the object is freed (`codecs.py:139`). Valid accelerated output is
  **byte-identical** to stdlib (verified for gzip/deflate/zlib), so there is no
  correctness cliff at the AUTO threshold for *valid* input — only the truncation asymmetry
  of F2.
- **lzip empty-member collisions are safe** (`state=None` → last-wins); a 3-member lzip
  with two empty members seeks to the right size with no assert.
- **LZW code-table bounds are correct**: `dictionary[code]` only ever `IndexError`s into the
  `code == next_code` KwKwK branch, which requires a prior entry; no negative or
  past-the-end index reaches output (`unix_compress.py:244`).
- **Vendoring is complete**: no runtime `import uncompresspy` anywhere in `src/`; the BSD-3
  notice is intact in-file (`unix_compress.py:412`).
- **The rapidgzip error tables still match on the 0.16.0 floor** for the cases that *do*
  raise (corrupt gzip via ISIZE backstop; header/format errors) — the F2 gap is that
  deflate/zlib truncation/corruption doesn't raise *at all*, not that a raised error is
  mistranslated.

See `seek-index.md`, `vendored-lzw.md`, `accelerators.md` for the detail and reproducers,
and `QUESTIONS.md` for the four maintainer decisions these surface.
