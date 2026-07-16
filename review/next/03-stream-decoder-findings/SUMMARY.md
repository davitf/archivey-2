# Brief 3 — Seekable decoder layer, accelerators & vendored LZW: findings

Deep review of the post-#96 seekable decompressor layer, the #105 rapidgzip hot path,
and the #89 vendored LZW core, following `review/next/03-stream-decoder-layer.md`.
Docs-only — no source is modified. Every finding is traced to `file:line` with a runnable
reproduction (`repro.py`).

> **Consolidated deliverable.** This brief was accidentally run by two independent
> reviews in parallel (PR #122 and PR #121). This document merges both. The two agreed on
> the headline shape and on three findings, and each found something the other missed —
> most usefully, PR #121 found the **LZW memory-amplification bomb** (F3a) and a **second,
> hostile-input trigger** for the seek-point crash (F1b), and PR #122 found that the same
> crash fires on **valid** multi-stream `.xz` via the ordinary size-then-read pattern
> (F1a). All findings below were independently re-verified in this session (see
> "Reconciliation" for what changed after cross-checking). PR #121's separate deliverable
> lives at `review/next/03-stream-decoder-layer/`.

## Headline

**The composition refactor (#96) is structurally sound** — the base `DecompressorStream`
is genuinely format-agnostic, `recreate()` rebuilds decoder state cleanly on every seek
(no buffer/CRC/CTR carryover), the before/after placement policy lives entirely in the
decoders, and the `_AcceleratorStream` finalizer is correct. The bugs are at the **seams
the refactor and the accelerator work introduced**: a seek-point *merge* invariant that
XZ is supposed to uphold but doesn't (crashing on both crafted and valid input), two
places where **damaged input is silently accepted**, and an unbounded **memory bomb** in
the trusted LZW core.

None of these is caught by the current suite: every seek test reads forward-to-EOF
*before* seeking (the one ordering that hides F1a), no test reads a truncated stream
through both `read(-1)` and chunked idioms (F4), and there is no property/randomized seek
test at all (F5).

## Baseline (captured green)

`uv run pytest` → **1555 passed, 120 skipped**. `pyrefly` 0 errors, `ty` all-pass,
`ruff` clean. Config `[all]` (rapidgzip 0.16.0 — the pinned floor —, py7zr 1.1.3, brotli
1.2.0, inflate64 1.0.4, pyppmd 1.3.1, ncompress 1.0.2, cryptography 49). F1/F3/F4/F5
reproduce in **every** dependency config (stdlib `lzma`/`zlib` + core); F2 needs the
`[seekable]` extra (rapidgzip) and does **not** affect `[core-only]`, where the stdlib
backend is used and raises correctly.

## Findings

| # | Sev | Where | One-liner |
|---|-----|-------|-----------|
| F1 | **High** | `decompressor_stream.py:251` | Two distinct inputs make `add_seek_points` route colliding same-offset points to `assert False` in `_resolve_same_offset_collision` → raw `AssertionError` (outside the `ArchiveyError` tree; silent wrong-seek under `python -O`). **(a)** a *valid* multi-stream `.xz` read with the everyday seek-to-end-then-read (size probe) pattern — stream-start (`state=None`) collides with a first-block point (`state=block`) [`xz.py:564` vs `:615`]; **(b)** a 72-byte *crafted* `.xz` with ≥2 zero-`uncompressed_size` blocks, which `build_index` alone maps to the same offset with distinct `_XzBlockBounds` objects [`xz.py:158` accepts `uncompressed_size==0`]. XZ is the only decoder that stores non-`None` `state`, so it is the only one that trips this. |
| F2 | **High** | `codecs.py:953`, `:987`, `:365` | On the #105 hot path, a truncated **deflate/zlib** stream decoded through rapidgzip **silently returns partial/zero bytes with no error**, where stdlib raises `TruncatedError`. deflate/zlib have no truncation backstop; the gzip ISIZE backstop (`_GzipTruncationCheckStream`) is skipped for non-path sources and is also defeated by a chance `1f 8b 08` in the >1 MiB payloads that trip the AUTO gate. (PR #121 additionally observed mid-stream *corruption* swallowed in some configs; that outcome is data-dependent — see accelerators.md.) The *silent* surface is **hash-less** accelerated streams (standalone raw deflate/zlib, gzip without a surfaced trailer CRC): CRC-bearing members — ZIP deflate, single-member gzip on a path — are already caught as `CorruptionError` by `VerifyingStream`, prefix preserved. Fix = reuse #113's `LengthVerifyingStream` where `member.size` is known, AUTO→stdlib otherwise. VISION #3 regression. |
| F3 | **Medium** | `decompressor_stream.py:302`; `unix_compress.py:51` | **Base-level per-read memory bomb (not LZW-specific).** `_read_decompressed_chunk` reads 64 KB *compressed* and buffers the **entire** decoded result, so any `DecompressorStream` codec balloons on a `read(1)`: brotli **80 B → 50 MB**, xz 7.4 KB → 50 MB, deflate 48 KB → 50 MB, LZW 9.4 KB → 20 MB (all re-verified). LZW (F3a) is the most acute — zero-dep core, no `[extra]` shield, unbounded position-growing ratio — and adds a second bug (F3b): `maxbits` accepted up to 31 (format ceiling 16 → dictionary ceiling 2¹⁶ vs 2³¹). `stream_members`/forward iteration apply no bomb guard. VISION #2/#4. |
| F4 | **Medium** | `decompressor_stream.py:286`, `:307`; `unix_compress.py:347` | `read(-1)`/`readall()` never consult `pending_error`, so a truncated `.Z` read with the `f.read()` idiom returns partial data and **swallows** the `TruncatedError` that the *same input* raises when read in chunks. Only the vendored LZW decoder uses the deferred-error path, but the root cause is in the base. VISION #3. |
| F5 | Low-Med (test gap) | `tests/test_seekable_streams.py` | No randomized/property seek test (old review finding #6, still open). Every seek test reads forward-to-EOF before seeking — the one ordering that cannot expose F1a. A seek-math property test would have caught F1. |

Details and reproductions: `seek-index.md` (F1, F5), `accelerators.md` (F2),
`vendored-lzw.md` (F3, F4). Maintainer decisions: `QUESTIONS.md`.

## Reconciliation — where the two reviews differed, and what survived cross-checking

- **F1 has two genuinely different triggers**, both re-verified this session. PR #122's
  (a) is the more alarming for *reliability* (fires on a valid `cat a.xz b.xz`); PR #121's
  (b) is the more alarming for *hostile input* (a 72-byte crafted file crashes the index
  build with no decode). Same assert, same fix surface — merged into one finding with both
  reproductions.
- **F3 (memory bomb) was found only by PR #121, and maintainer review then broadened it.**
  PR #122 had caught only the `maxbits` sub-part; PR #121 found the eager-`feed` bomb.
  Maintainer review (2026-07-16) correctly pointed out it is **not LZW-specific** — it is
  the base's `_read_decompressed_chunk`, so brotli/xz/deflate/ppmd/deflate64 all balloon
  the same way (re-verified: brotli 80 B → 50 MB). F3 is now scoped as a base-level issue
  with LZW as the sharpest instance; see the fix-feasibility table in `QUESTIONS.md` Q3.
- **F2 corruption angle.** PR #121 measured mid-stream *corruption* (not just truncation)
  swallowed by rapidgzip while stdlib raised. In this session the **truncation** swallow
  reproduced cleanly and repeatably (both reviews); a quick attempt to reproduce the
  *corruption* split across several single-byte flips did **not** reproduce (a corrupt raw
  deflate stream can also make stdlib stop at a spurious clean EOF). Corruption is
  therefore reported as a secondary, data-dependent observation, not a firm claim; the
  firm F2 finding is truncation.
- **F4 / F5 were found by both** with the same root cause and are unchanged.

## What's actually fine (verified, not assumed — both reviews concur)

- **The base is genuinely format-agnostic.** No format constant leaked into
  `DecompressorStream`; the before/after placement asymmetry lives entirely in the
  decoders (`lzip.py:275` before, `unix_compress.py:374` after).
- **`recreate()` carries no stale state.** Every decoder constructs fresh backend objects
  in `recreate` — no buffer, CRC, or CTR accumulator survives a seek reset; `pending_error`
  is cleared on reset (`decompressor_stream.py:264`).
- **lzip's seek index is immune to F1.** Every lzip point carries `state=None`, so
  same-offset collisions resolve as forward-refinement last-wins, never the assert
  (verified with empty-member lzip fixtures on both sides).
- **XZ progressive enrichment save/restore is airtight single-threaded**
  (`xz.py:574-603`, `try…finally`); the `stream_cell` closures only *read* stream state and
  are never interleaved with `feed` under the sync-only contract. Concurrency is a
  documented non-promise.
- **The LZW kernel's hostile-input core is careful.** KwKwK (`code == next_code`),
  `code > next_code` rejection, and the `prev_entry is None` first-code guard are correct
  (`unix_compress.py:244-259`); `len(dictionary) == next_code` is invariant (no index past
  the live table). The BSD-3 `uncompresspy` notice is intact and complete — no runtime
  import of the upstream package (`unix_compress.py:412-443`).
- **`_AcceleratorStream` finalizer** is correct and at the birth site (`codecs.py:139`);
  valid accelerated output is byte-identical to stdlib (no correctness cliff at the AUTO
  threshold). xz/lzip truncation is honest regardless of read style (the F4 gap is
  specific to the deferred `pending_error` path that only `.Z` uses).

## Non-goals honored

The composition refactor, the SeekTable-vs-decoder decision, and the five forward-only
adapters are **not** re-litigated (all settled in the #96 design). BGZF's absence is not a
finding. F1 is reported as a correctness bug in the *result*, not a challenge to folding
index discovery into the decoder.
