# Tasks — Characterize rapidgzip truncation/corruption; refine or remove the ISIZE backstop

> Investigation + specs proposal. The implementation choice (narrow / extend / remove the
> backstop) is made once the behavior is characterized. Run tools through `uv`
> (`uv run pytest`, `uv run pyrefly check`, `uv run ty check`, `uv run ruff`).
> Pre-0.2.0 pay (debt-ledger Q4). Read `design.md` before starting — AUTO/ISIZE
> coupling, two length mechanisms, fuzz-off constraints.

## 1. Characterize rapidgzip's truncation behavior

- [x] 1.1 Sweep truncation: for several payload sizes and shapes (empty, < 1 block, multi-block,
      multi-member, suspected ~10-byte header-only), cut the gzip at every offset (or a dense
      stratified sample) and record whether `rapidgzip` raises, returns short/zero silently,
      returns full output, or times out. Capture exception text. Prefer **path** sources;
      wall-clock timeout required (C++ hang risk). Do **not** rely on mutation/Atheris
      (accelerators off there). Compare cuts to stdlib `gzip` as oracle.
      → `scripts/rapidgzip_truncation_sweep.py` + `results/linux-x86_64.{md,json}`.
- [x] 1.2 Confirm/curate the silent-truncation set (the maintainer's data suggests it may be as
      narrow as a header-only / ~10-byte input). Note any `parallelization` dependence.
      → **Not narrow:** silent∩stdlib-raise is the mid-body default (416 cuts). See
      `FINDINGS.md`. `parallelization` 0 vs 1 identical for gzip.
- [ ] 1.3 Repeat on macOS (arm64) and Linux to confirm the silent set is platform-independent.
      → **Linux done.** macOS/Windows deferred (maintainer local run or CI job using the
      same script). Do not lock §2 solely on cross-platform identity, but confirm before 0.2.0.
- [x] 1.4 Do the same quick pass for `rapidgzip.IndexedBzip2File` (bzip2) so the bzip2 path's
      truncation behavior is documented too. Do not invent an ISIZE twin for raw
      deflate/zlib unless that sweep shows a real silent set (container CRC covers members today).
      → Short-prefix silent-empty only under `parallelization=0` (cuts 0..9); no ISIZE twin.

## 2. Decide the backstop's shape

> **Agent recommendation (not locked):** **extend** (2.2), reject remove (2.3) and
> reject narrow-only (2.1 alone). Details in `FINDINGS.md`. Maintainer analyzes before
> locking; §3 waits on that call.

- [ ] 2.1 If the silent set is narrow and specific, replace the broad ISIZE compare with a
      targeted check for exactly those cases (and prefer rapidgzip's own errors otherwise).
      → Linux data: silent set is **not** narrow; do not use 2.1 as the sole outcome.
      A small `< 18` / incomplete-member special-case is still needed *in addition to* ISIZE.
- [ ] 2.2 If a size comparison is kept, define multi-member handling explicitly: sum per-member
      `ISIZE` by walking members, with a rule that cannot false-positive on a valid file.
      → **Recommended path.**
- [ ] 2.3 If rapidgzip's own errors plus a tiny special-case suffice, remove the
      `_GzipTruncationCheckStream` machinery entirely.
      → **Not supported by Linux data** (ISIZE already catches 337/416 silent∩raise cuts).
- [ ] 2.4 Whichever outcome: re-check AUTO eligibility when only `gzip_isize_backstop` made
      truncation “verifiable” (bare `.gz` / single-file-compressed). Keep
      `_wrap_accelerated_length` / container `VerifyingStream` behavior intact.

## 3. Implement + test the chosen approach

- [ ] 3.1 Implement the decision from §2 in `_open_gzip` / the gzip codec path.
- [ ] 3.2 Tests: truncation at representative offsets for single- and multi-member gzip; assert
      `TruncatedError`/`CorruptionError` and that valid files (incl. multi-member) never raise.
- [ ] 3.3 Update `seekable-decompressor-streams` (sync the delta), `docs/internal/known-issues.md`,
      and the truncation notes in `docs/internal/library-analysis.md`.
- [ ] 3.4 If `_GzipTruncationCheckStream` is gone and nothing else needs standalone
      `VerifyingStream` beyond unit tests / container bounds, note Topic 6 adjacency in
      `review/backlog.md` (do not delete in this change unless clearly unused).

## Already done (interim, in PR #14)

- [x] Block-wise (bounded-memory) scan in `_GzipTruncationCheckStream._has_additional_gzip_member`
      — no longer reads the whole file into memory.
