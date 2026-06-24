# Tasks — Per-format compression-library evaluation + decision doc

> Investigation + a decision doc + dependency cleanup. Library *swaps* are deferred to
> follow-up changes; this change fixes the decisions and their rationale. Run tools through
> `uv` (`uv run pytest`, `uv run pyrefly check`, `uv run ty check`, `uv run ruff`).

## 1. Build the comparison matrix

- [ ] 1.1 For each codec the library reads (gzip, bzip2, xz/lzma, lzip, zstd, lz4, brotli,
      unix-compress, deflate64, ppmd), list the candidate libraries and the chosen one today.
- [ ] 1.2 Define the scoring columns: non-seekable support, efficient seeking,
      corruption detection, truncation detection, error-reporting fidelity (translatable?),
      install/availability (pure-Python vs. native wheels, platform/arch), maintenance, notes.

## 2. Investigate zstd (the open question)

- [ ] 2.1 `zstandard` (current): document the no-backward-seek behavior we wrap in
      `_ZstdReopenStream`, and which corruption/truncation cases it does and does not raise.
- [ ] 2.2 `pyzstd`: decode behavior, seeking (incl. `SeekableZstdFile` — confirm it only
      reads the **Seekable Zstd** format, not arbitrary `.zst`), error reporting, availability.
- [ ] 2.3 stdlib `compression.zstd` (3.14+) and `backports.zstd` (older Pythons): seeking
      support (likely none/limited), error reporting, the "prefer stdlib when present" angle.
- [ ] 2.4 `indexed_zstd` (martinellimarco; used by ratarmount): efficient seeking, non-seekable
      behavior, availability, and the same-process-as-rapidgzip concern (does it bundle a C++
      core that could collide like indexed_bzip2 — see `docs/known-issues.md`?).
- [ ] 2.5 Decide: primary decode backend (validate/revise the `zstandard`→`pyzstd` intent) +
      the seekable-zstd story (indexed_zstd / SeekableZstdFile / none). Record the rationale.

## 3. xz (own parser vs python-xz) — decision already made, just carry it over

- [ ] 3.1 Summarize the recorded DEV decision (`davitf/archivey-dev#214`) in the analysis
      doc: native `xz.py` on stdlib `lzma` for block-index random access, efficient
      `SEEK_END` (backwards index scan), correct multi-stream handling, per-stream fallback
      for index-less/truncated files, and `DecompressorStream` uniformity with lzip. Link the PR.
- [ ] 3.2 Note that v2 did **not** carry over DEV's disabled-by-default `python-xz`
      comparison backend, so the `python-xz` `[all]` pin is dead — remove it (§5.2).

## 4. Record the rest

- [ ] 4.1 Document the chosen library + rejected alternatives + reason for gzip, bzip2, lzip,
      lz4, brotli, unix-compress, deflate64, ppmd. Cross-link the rapidgzip/indexed_bzip2
      single-accelerator macOS constraint already in `docs/known-issues.md`.

## 5. Write the doc + clean up deps

- [ ] 5.1 Add `docs/library-analysis.md` with the filled matrix + per-codec decisions; add a
      nav entry in `mkdocs.yml`; cross-link from `docs/known-issues.md` / `COMPARISON.md`.
- [ ] 5.2 Audit `pyproject.toml` extras against `src/` imports; remove `python-xz` and
      `pyzstd` from `[all]` (confirmed unused in `src/`) or justify keeping each; ensure
      `[zstd]` matches the decision.
- [ ] 5.3 Add a `tests/check_zero_dep_core.py`-style or unit guard that every package pinned
      in an extra is importable-and-reachable (no dead optional deps), so this can't regress.

## 6. Verify + hand off

- [ ] 6.1 `uv run pytest` / `pyrefly` / `ty` / `ruff` green (doc + packaging only — no
      runtime behavior change in this change).
- [ ] 6.2 Sync the spec deltas (`packaging-and-extras`, `documentation`).
- [ ] 6.3 File the follow-up swap changes the decisions call for (zstd backend migration,
      optional seekable-zstd support, any xz change) — each its own proposal.
