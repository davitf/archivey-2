# Structural debt — S1–S4 revisited, module seams, markers

All line references: `main` @ `7bb862b`.

## S3 — the pass driver is now **four** copies, exactly as predicted (PAY — but gate the next backend, not the release)

The 2026-07-12 `deep-simplification.md` S3 predicted the native RAR reader
would add a fourth copy of the "close previous / open current / yield /
cleanup tail" loop skeleton. It did. The four copies today:

| Copy | Where | Skeleton specifics |
|---|---|---|
| Base default | `base_reader.py:450-495` | tracks `previous`, closes on advance, **leaves the last stream open** with a comment explaining why |
| TAR streaming override | `tar_reader.py:439-461` | **no `previous` tracking at all** — relies on tarfile invalidating the prior `extractfile` handle on advance |
| 7z override | `sevenzip_reader.py:303-343` | `previous` close + solid-folder swap (`folder_index` change → close/reopen `SolidBlockReader`) + `finally` tail closing both |
| RAR solid override | `rar_reader.py:578-649` | `previous` close + running `pipe_offset` cursor + `finally` tail closing stream, `SolidBlockReader`, and clearing `_live_unrar` |

This is the drift S3 warned about, live: the close-previous invariant is
enforced three different ways and *not at all* in one copy (TAR's omission is
defensible — tarfile owns invalidation — but that reasoning exists only in this
review, not in the code). The `stream_members` ownership contract remains a
"MUST override correctly" docstring instruction to backend authors
(`base_reader.py:450-474`) rather than machinery. Every future backend — and a
**native streaming ZIP reader is the named next backend** (`IDEAS.md`,
`open-issues.md` P2/P4) — adds a fifth copy carrying the same invariants.

**Verdict: KEEP through 0.2.0, PAY before the next backend.** The duplication
is invisible at the public surface — nothing about it freezes at release — and
a behavior-preserving refactor of exactly the loops that carry the library's
trickiest invariants (RAR pipe-offset demux, 7z solid swap) is the wrong thing
to rush against a release date. The explicit justification for carrying it
past 0.2.0: (a) zero public-surface exposure; (b) all four copies are
currently guarded by the strongest suite the project has had (three dependency
configs, corpus sweep, solid-RAR fixture tests). The debt becomes intolerable
the moment a fifth copy is written, so the pay-trigger is hard: **the S3
unification is an entry gate for the native-ZIP (or any new) backend**, the
same way fuzzing was an entry gate for Phase 6. Recommend recording that gate
in `PLAN.md`/`IDEAS.md` now so it cannot be forgotten. (Maintainer may
override and pay pre-release — see `QUESTIONS.md` Q3.)

## S2 — member-list pipeline: **half paid**; the remaining half is the risky half (PAY together with S3)

The original S2 described two complete pipelines. Since then, real convergence
happened — credit where due:

- **One id-stamper:** `_stamp_progressive_member` now delegates to the shared
  `_register_member` (`base_reader.py:1050-1057`), which also handles the
  re-account-after-reset subtlety (`:863-869`).
- **One publication point:** both pipelines publish through
  `_publish_materialized` into the single `_materialized` holder (the old N2
  two-store race is structurally fixed, not ordering-by-comment).
- **One name-index builder:** `_index_member_name` is shared (`:879-883`).

What remains duplicated is the *drive + finalize* layer, and it has already
reproduced the "invariant enforced twice, in parallel prose" pattern:

- Two drive loops: the eager `_materialize_members` scan
  (`base_reader.py:782-816`) vs `_ProgressivePassIterator`
  (`:1640-1690`).
- Two link finalizers: `_finalize_materialized_links` (`:740-759`) vs
  `_finalize_pass_links` (`:1021-1048`).
- Two **mirrored double-fault guards** with near-identical block comments —
  "keep the recovered prefix even if link finalization hits a second fault"
  (`:794-804`) and "same double-fault guard as the RA incomplete path"
  (`:1035-1042`). Same invariant, maintained twice; the comment on the second
  literally points at the first. This is the tell the original S2 called out
  ("state that exists twice gets its invariants enforced once"), one layer up.
- `_get_members_index_only` (`:841-849`) is a third enumeration path (its
  laziness is genuinely different; it would survive unification as a pure
  enumeration).

**Verdict: same as S3 — KEEP through 0.2.0 with the justification above, PAY
as one OpenSpec change with S3** ("materialization is a drained forward
pass" + a narrow per-backend open hook). Doing S2 and S3 together is cheaper
than either alone because the unified pass driver *is* the single drive loop
S2 wants.

## S1 — one error boundary: **paid, and it held** (fine; one small residue)

`_translated_errors` exists on the base (`base_reader.py:357`) and the newer
paths kept it honest:

- TAR uses it at 4 sites (`tar_reader.py:404,422,445,683`), ISO at 3
  (`iso_reader.py:288,352,473`), ZIP at the member-open boundary
  (`zip_reader.py:1211`).
- RAR and 7z define only the `_translate_exception` hook
  (`rar_reader.py:651`, `sevenzip_reader.py:648`) and route raw-library
  errors through the shared `ArchiveStream` boundary — no hand-rolled
  translate/stamp/raise loops. The ~10-site duplication S1 measured is gone.
- The remaining direct `_stamp_error_context` calls
  (`zip_reader.py:1252-1317`, `single_file_reader.py:359-394`,
  `base_reader.py:675-693`) are **origination** sites — they construct new
  typed errors (password disambiguation outcomes) or pass `stamp=` lambdas
  into stream machinery. That is not the S1 disease (translating raw errors
  by hand); no action.

Residue: `tar_reader.py:378-385` `_translate_open_error` is a small
hand-rolled translate-and-stamp variant (returns instead of raising, adds a
CorruptionError fallback). One site, deliberate shape. **KEEP** — folding it
into the boundary would need a new keyword for the fallback; not worth it.

## S4 — ReaderState: reworked, not accreted (fine; verify note)

The old review predicted that patching N3/N4 into the five-mechanism counter
encoding would accrete a sixth mechanism. That did not happen: the current
`reader_state.py` (398 lines) has a consolidated `OperationToken` carrying
`kind` ("root"/"child"/"worker") **and the acquiring thread**
(`reader_state.py:31-43`), which is the "owner fields, not new bookkeeping"
shape S4 asked for. `begin_internal_opens`/`end_internal_opens`
(`:105-121`) still uses a depth count internally, but scoped and locked.
No further action proposed; if the file is touched again, re-read S4 first.

## Module-split coherence — the splits are earning their seams (fine)

Checked each split the backlog named; every one carries a documented,
load-bearing rationale in its module docstring:

- `config.py` (public types) vs `internal/config.py` (derived `StreamConfig`
  view for the stream layer) — the internal one re-exports and states its
  purpose (`internal/config.py:1-22`).
- `measurement.py` (public `IoStats` + re-exported `enable_measurement`) vs
  `internal/measurement.py` (contextvar + counters; "no public performance
  API" stated) — single public import path preserved.
- `internal/extraction_types.py` — public value types living under
  `internal/` *explicitly* to break the `reader.py`/`core.py` ↔ coordinator
  import cycle; the docstring says exactly that (`extraction_types.py:1-10`).
- `sevenzip_methods.py` / `sevenzip_pipeline.py` / `sevenzip_parser.py` /
  `sevenzip_reader.py` — parser/pipeline/reader layering matches the
  native-reader design docs; no seam looks gratuitous.
- `internal/timestamps.py` — the shared FILETIME math (X2's fix); per-backend
  field layout stays per-backend, as the old review recommended.
- Small single-purpose modules (`open_site.py`, `password_confirm.py`) state
  who uses them and why they are format-agnostic.

No consolidation recommended. **KEEP all.**

## Markers, dead code, leftovers

- **In-code deferred markers: effectively one.** The only "deferred /
  follow-up" marker in `src/` is `extraction.py:438-446` — the O7
  policy-gated rename follow-up, which is already a recorded threat-model
  residual. No stray `TODO`/`FIXME`/`XXX`/`HACK` markers exist. This is an
  unusually clean tree; the debt genuinely lives in the documented registers,
  not in scattered comments. (fine)
- **`VerifyingStream` leftover:** post-#137 fusion, the standalone wrapper
  survives only as the rapidgzip length backstop (`codecs.py:296-304`) and
  unit-test subject — exactly the state `backlog.md` Topic 6 parked ("delete
  once nothing but unit tests / codecs.py length backstops need it"). Still
  true; still parked. **KEEP** (Topic 6 adjacency; do not delete while the
  codecs backstop needs it).
- **Public surface:** `__init__.py` documents the deliberate two-tier export
  scheme (`__all__` = documented API; advanced types importable but
  uncrowded, each tagged `# noqa: F401` with a reason). No dead exports
  found by inspection; ruff would flag unused imports in CI. (fine)
