# decide-strict-archive-eof-default — pick the TAR end-of-archive strictness stance

**Status:** Decided — **Option F (signal-aware default)**. Depends on nothing. Ready to apply (`tar_reader._verify_tar_eof` + docs). Minor breaking: `nonzero` tars change from warn to raise. Effort: small — one backend method, docs, tests.

**Why it matters:** The founding inventory use case needs honest listings, but stdlib tarfile can treat a corrupt mid-archive header as a clean end. Archivey’s trailer check is the only backstop, and today it warns by default. Flipping that default wholesale is a product stance, not a drive-by fix — Phase 5 already chose warn-by-default for trailer-less real-world tars.

**What it does:** Surveys Options A–F with trade-offs and locks **Option F**: split the EOF diagnostic on the `observed_kind` signal the check already computes. `nonzero` (a stray non-null block where a trailer/header was expected — which a conformant tar never produces) raises `CorruptionError` by default; the ambiguous `absent`/`short` residual (complete-trailer-less vs. truncated-at-boundary) warns by default and escalates to `TruncatedError` only under `strict_archive_eof=True`. Extract raises at end after salvageable writes.

**Decided:** Option F over A/D (leave `nonzero` silent — fails inventory honesty), B/C (break the wild trailer-less corpus), and E (soft-extract report — more surface than v1 needs). `config.py` default stays `False`; no `archive-reading` delta. Native TAR (P3) still owns the `absent`/`short` structural fix; a salvage mode (`IDEAS.md`) is the future escape for reading a `nonzero` tar without an exception.

**Cross-notes for later:** CLI (`cli-v1`) — `archivey test` should default to strict EOF (validator = maximally paranoid). Open-issues P1 reworded to "decided — Option F".

**Bottom line:** Apply per `tasks.md`: change `_verify_tar_eof` to raise `CorruptionError` on `nonzero` unconditionally, keep `absent`/`short` on the existing warn/strict path, update docs, add the regression fixtures.
