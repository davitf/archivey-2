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
      Upstream code map / intent: `UPSTREAM_TRUNCATION_REPORT.md`.
- [x] 1.2 Confirm/curate the silent-truncation set (the maintainer's data suggests it may be as
      narrow as a header-only / ~10-byte input). Note any `parallelization` dependence.
      → **Not narrow:** silent∩stdlib-raise is the mid-body default (416 cuts). See
      `FINDINGS.md`. `parallelization` 0 vs 1 identical for gzip.
      Upstream: soft-EOF is **by design** (`processNextChunk` / speculative `tryToDecode`);
      `parallelization=0` means all cores (`UPSTREAM_TRUNCATION_REPORT.md`).
- [x] 1.3 Repeat on macOS (arm64) and Linux to confirm the silent set is platform-independent.
      → CI workflow ran successfully (run on PR). **Windows ≡ Linux** (wide silent set).
      **macOS** mostly raises mid-body; residual silent set ≈ cut=10 + one multimember
      short. Results in `results/macos-arm64.*`, `windows-amd64.*`. Stack still needed
      cross-platform.
- [x] 1.4 Do the same quick pass for `rapidgzip.IndexedBzip2File` (bzip2) so the bzip2 path's
      truncation behavior is documented too. Do not invent an ISIZE twin for raw
      deflate/zlib unless that sweep shows a real silent set (container CRC covers members today).
      → Short-prefix silent-empty only under `parallelization=0` (cuts 0..9); no ISIZE twin.
      Upstream notes: report §2.8 / §6.

## 2. Decide the backstop's shape

> **Locked 2026-07-20:** empty→stdlib on zero-byte EOF + single-member ISIZE (close
> `<18`). Multi-member ISIZE sum deferred. No `tell_compressed` trap. No upstream
> issue (document only). Keep `parallelization=0`. See `design.md`.

- [x] 2.1 If the silent set is narrow and specific, replace the broad ISIZE compare with a
      targeted check for exactly those cases (and prefer rapidgzip's own errors otherwise).
      → **Rejected as sole outcome** (silent set is wide).
- [x] 2.2 If a size comparison is kept, define multi-member handling explicitly: sum per-member
      `ISIZE` by walking members, with a rule that cannot false-positive on a valid file.
      → **Deferred.** Member discovery = forward `1f 8b 08` scan with false-header risk
      (same class as today’s bailout). Keep “further magic ⇒ do not raise” for now;
      ISIZE backstop remains **single-member**-effective.
- [x] 2.3 If rapidgzip's own errors plus a tiny special-case suffice, remove the
      `_GzipTruncationCheckStream` machinery entirely.
      → **Rejected.**
- [x] 2.4 Whichever outcome: re-check AUTO eligibility when only `gzip_isize_backstop` made
      truncation “verifiable” (bare `.gz` / single-file-compressed). Keep
      `_wrap_accelerated_length` / container `VerifyingStream` behavior intact.
      → **Keep** `gzip_isize_backstop` as AUTO verifiability signal; re-verify in §3 tests.
- [x] 2.5 Decide empty→stdlib fallback on rapidgzip EOF with zero bytes delivered (recovers
      partial data + loud error; valid empty gzip must still succeed). Compose with 2.2, not
      as a replacement. Record lock-in in `design.md` / delta spec before §3.
      → **Locked** (compose with single-member ISIZE; multi-member sum still deferred).
- [x] 2.6 Decide remaining open items in `design.md`: multi-member ISIZE sum vs
      single-member-explicit scope; optional `tell_compressed==0` trap; whether to file the
      upstream `is_stream_complete` issue; whether to change `parallelization=0` (all cores)
      to `1` here or later; whether §2 locks without macOS (1.3).
      → multi-member defer; tell_compressed reject; no upstream issue (doc instead);
      keep parallelization=0; lock §2 + CI for 1.3.

## 3. Implement + test the chosen approach

- [x] 3.1 Implement the decision from §2 in `_open_gzip` / the gzip codec path.
      → `_GzipTruncationCheckStream`: empty→stdlib fallback + single-member ISIZE
      (raise on `size < 18` for non-empty soft EOF); keep further-magic multi-member bailout.
- [x] 3.2 Tests: truncation at representative offsets for single- and multi-member gzip; assert
      `TruncatedError`/`CorruptionError` and that valid files (incl. multi-member) never raise.
      → `tests/test_accelerator_corruption.py` (header-only, mid-cut recovery, empty, multi).
- [x] 3.3 Update `seekable-decompressor-streams` (sync the delta), `docs/internal/known-issues.md`,
      and the truncation notes in `docs/internal/library-analysis.md`.
      → Also scoped end-user notes in `docs/gotchas.md` / `docs/formats.md` (bare `.gz` vs
      container CRC).
- [x] 3.4 If `_GzipTruncationCheckStream` is gone and nothing else needs standalone
      `VerifyingStream` beyond unit tests / container bounds, note Topic 6 adjacency in
      `review/backlog.md` (do not delete in this change unless clearly unused).
      → Wrapper kept; Topic 6 note updated.
## Already done (interim, in PR #14)

- [x] Block-wise (bounded-memory) scan in `_GzipTruncationCheckStream._has_additional_gzip_member`
      — no longer reads the whole file into memory.
