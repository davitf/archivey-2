# Tasks — Characterize rapidgzip truncation/corruption; refine or remove the ISIZE backstop

> Investigation + specs proposal. The implementation choice (narrow / extend / remove the
> backstop) is made once the behavior is characterized. Run tools through `uv`
> (`uv run pytest`, `uv run pyrefly check`, `uv run ty check`, `uv run ruff`).

## 1. Characterize rapidgzip's truncation behavior

- [ ] 1.1 Sweep truncation: for several payload sizes and shapes (empty, < 1 block, multi-block,
      multi-member), cut the gzip at every offset and record whether `rapidgzip` raises, returns
      short output silently, or returns full output. Capture the exact exception text.
- [ ] 1.2 Confirm/curate the silent-truncation set (the maintainer's data suggests it may be as
      narrow as a header-only / ~10-byte input). Note any `parallelization` dependence.
- [ ] 1.3 Repeat on macOS (arm64) and Linux to confirm the silent set is platform-independent.
- [ ] 1.4 Do the same quick pass for `rapidgzip.IndexedBzip2File` (bzip2) so the bzip2 path's
      truncation behavior is documented too.

## 2. Decide the backstop's shape

- [ ] 2.1 If the silent set is narrow and specific, replace the broad ISIZE compare with a
      targeted check for exactly those cases (and prefer rapidgzip's own errors otherwise).
- [ ] 2.2 If a size comparison is kept, define multi-member handling explicitly: sum per-member
      `ISIZE` by walking members, with a rule that cannot false-positive on a valid file.
- [ ] 2.3 If rapidgzip's own errors plus a tiny special-case suffice, remove the
      `_GzipTruncationCheckStream` machinery entirely.

## 3. Implement + test the chosen approach

- [ ] 3.1 Implement the decision from §2 in `_open_gzip` / the gzip codec path.
- [ ] 3.2 Tests: truncation at representative offsets for single- and multi-member gzip; assert
      `TruncatedError`/`CorruptionError` and that valid files (incl. multi-member) never raise.
- [ ] 3.3 Update `seekable-decompressor-streams` (sync the delta) and `docs/known-issues.md`.

## Already done (interim, in PR #14)

- [x] Block-wise (bounded-memory) scan in `_GzipTruncationCheckStream._has_additional_gzip_member`
      — no longer reads the whole file into memory.
