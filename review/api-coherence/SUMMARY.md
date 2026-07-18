# API-coherence review — SUMMARY

> **Status (2026-07-18):** findings delivered in #133; **no code follow-ups have
> landed yet.** Every row in the table below is still open. Blockers that need
> a maintainer answer first: **Q1–Q6** (`QUESTIONS.md`). Mechanical surface
> work (S1/S2/S3/E1/E3) is actionable after Q4/Q6. Triage: `../STATUS.md`.

Reviewed at `main` + CLI (#120) merged (`7139c13`). Baseline (all captured before
review, `[all]` config): pytest **1699 passed / 131 skipped / 3 deselected**, pyrefly
**0 errors** (8 warnings), ty clean, `ruff check` clean. `ruff format --check` fails on
four pre-existing helper scripts under `review/archive/` only — library and tests are
clean. Findings below reproduce in `[all]`; none depend on optional-library versions
(they are design findings, not codec behaviour).

## Headline

The surface is in **better shape than the ~90-name count suggests**: the data model is
carefully specified, the cost receipts are now honest, the docs match the code, and the
CLI consumed most of the API cleanly. Two things should be settled **before the 0.2.0
freeze**:

1. **The one real uniform-interface break: duplicate-name members** (`parity.md` P1).
   The same logical situation — two entries with one name — yields three different
   outcomes by format: 7z marks the older entry `is_current=False` and default
   extraction skips it; RAR gives history rows distinct `path;n` names; ZIP/TAR leave
   both `is_current=True` and **default `extract_all` fails with `ExtractionError`**
   on an archive as ordinary as an appended-to tarball (`tar -rf`). Runnable repro in
   `parity.md`. This undercuts VISION claim (1) directly and is also the crux of the
   maintainer's members-scope question (`members-scope.md`).
2. **Shed ~20 names from `__all__` and fix the export gaps** (`surface.md`). The 13
   `Diagnostic*Context` classes and `RAPIDGZIP_AUTO_MIN_COMPRESSED_SIZE` don't belong
   at top level; meanwhile `PasswordInput` (the type of a public parameter) and
   `OnDiagnostic` are *not* exported, and `api.md` omits `open_stream` entirely. All
   free pre-release.

The CLI case study surfaced three genuine library gaps (`ergonomics.md`): the
`--track-io` counters live only on the internal `BaseArchiveReader` (the CLI
`isinstance`-checks and imports `enable_measurement` from `internal/`); the "verify
everything" job has no library primitive (the CLI hand-rolls a 60-line loop with
subtle generator semantics); and `ArchiveFormat` has no display name (the CLI parses
`repr()`).

## Top findings

| # | Severity | Finding | Where | Status |
|---|----------|---------|-------|--------|
| P1 | **High** | Duplicate-name members: `is_current` computed only by 7z/RAR; ZIP/TAR default extraction *errors* where 7z silently skips — same input, divergent outcome; `safe-extraction` scenario ("superseded by later same-name → SKIPPED") is format-silent and currently false for ZIP/TAR | `sevenzip_parser.py:331`, `extraction.py:351`, repro in `parity.md` | **open — needs Q1** |
| E1 | Medium | No public measurement/IO-stats API: CLI `--track-io` imports `enable_measurement` + `BaseArchiveReader` from `internal/` and reads three counters not on the `ArchiveReader` ABC | `cli/common.py:15-16,56-68`, `base_reader.py:557-1009` | **open** — proposed fix; actionable after Q4 nod |
| E2 | Medium | No library "verify" primitive: `archivey test` hand-rolls manual `next()` loop; a mid-pass failure poisons the stream and loses remaining members | `cli/test_cmd.py:56-73` | **open — needs Q5** (now vs post-0.2.0) |
| S1 | Medium | `__all__` = 90 names: 13 `*Context` classes + `RAPIDGZIP_AUTO_MIN_COMPRESSED_SIZE` should demote; `PasswordInput` / `OnDiagnostic` missing; `MemberSelector` vs internal `MemberSelectorArg` duplicate aliases used inconsistently in one class | `__init__.py:113-204`, `reader.py:27,148` | **open — needs Q4** |
| P2 | Low-Med | RAR `listing_cost=INDEXED` always, but `cost.py` docstring names "RAR with no quick-open record" as the canonical `REQUIRES_SCANNING` example — doc/impl conflict on an honest-cost axis | `rar_reader.py:773` vs `cost.py:24-27` | **open — needs Q3** |
| E3 | Low | `ExtractionStatus.SKIPPED` conflates overwrite-skip and non-current-skip; caller must infer reason via `member.is_current` | `extraction_types.py:80-87`, `extraction.py:351` | **open — needs Q6** |
| S2 | Low | `ArchiveFormat` has no display-name property; CLI string-parses `repr()` | `cli/info_cmd.py:16-23` | **open — needs Q6** |
| S3 | Low | `archivey.core.__all__` exports internal helper `source_name`; `docs/api.md` omits `open_stream`, `MemberStreams`, selectors, format-support queries | `core.py:78`, `docs/api.md` | **open — needs Q4** |
| D1 | Low | CLI list line has no mark for `ANTI` (falls to `"?"`, same as `OTHER`) and no non-current indicator — the member model's own distinctions are invisible in the first consumer | `cli/format.py:9-15` | **defer to `cli-product/`** |

## The maintainer's extra question (members scope)

Analyzed in **`members-scope.md`**. Short version: **keep `members()` / iterators
returning everything, do not add an include/exclude argument** — a default-exclude is
unimplementable for streaming TAR (last-entry-wins is unknowable mid-pass), breaks
`member_count` and `ExtractionReport` alignment, and doesn't even serve the intuitive
goal (anti entries are `is_current=True`, so "current only" still shows tombstones).
The real fix is P1: make `is_current` *mean the same thing everywhere*, then the
one-line caller-side filter (`m.is_current`) — plus the already-predicate-accepting
`stream_members`/`extract_all` selectors — covers every need. Details and the
consequence table are in the file.

## What is actually fine (don't churn)

- **`member.hashes`** — the emptiness contract is real *and documented*: the
  per-format stored-digest matrix in `docs/formats.md` matches the code (ZIP/7z crc32,
  RAR5 crc32+blake2sp, gz/lz conditional, tar/dir/ISO none). #104's parity landed.
- **Timestamps** — faithful naive-vs-aware (`ZIP` DOS naive, tar/dir aware UTC, ISO
  aware with offset, RAR4 wall-clock) with `modified_utc(tz_for_naive=)` as the
  explicit-assumption escape hatch. This is the right design; don't normalize.
- **Cost receipts** (except P2) — directory now honestly `REQUIRES_SCANNING` (old
  finding #4/#9 fixed), compressed tar `REQUIRES_DECOMPRESSION`+`SOLID`, 7z reports
  `solid_block_count`, single-file reports real source seekability. Per-format
  receipt test exists (`test_cost_receipt.py`).
- **The error tree** — granularity is right (wrong-password / truncation / corruption
  / unsupported are separately catchable), and `ArchiveyUsageError` outside the tree
  is documented, tested, and defensible (caller bugs must not be swallowed by
  `except ArchiveyError`).
- **The extraction cluster** — five-ish names is not the smell the brief feared:
  inputs (`ExtractionPolicy`/`OverwritePolicy`/`OnError`), per-member outcome
  (`ExtractionResult`+`Status`), callback (`ExtractionProgress`), aggregate
  (`ExtractionReport` = results + diagnostics) are distinct concepts, each earning its
  name. Only the SKIPPED conflation (E3) is worth touching.
- **Identity rules** — `__contains__` identity-only with a helpful `TypeError`,
  foreign-member `open()` rejection, `get()` as the single name-lookup — coherent and
  well-reasoned against streaming-pass consumption.
- **`MemberStreams` declared capabilities** — uniformly gated across backends
  including the directory reader (deliberate uniformity), and `streaming` ×
  `CONCURRENT` rejection is loud.
- **Safe-by-default config** — limits on by default, `STRICT`+`ERROR` extraction
  defaults, `ExtractionLimits.UNLIMITED` an explicit opt-out. Knobs are orthogonal.
- **The one-shot `extract` without `members=`** — deliberate, documented, correct.
- **Password model** — the CLI's TTY prompt fell out of `PasswordProvider` in 10
  lines; that's the API working.

## Deliverable map

- `parity.md` — cross-backend audit (P1 headline + the full observable matrix, and
  where the conformance sweep should assert parity but works around it).
- `surface.md` — the 90-name audit: demote/add/rename table, naming coherence,
  smallest-surface proposal.
- `ergonomics.md` — the three canonical loops + CLI case-study gaps (E1–E3).
- `members-scope.md` — the maintainer's non-current-members question.
- `QUESTIONS.md` — decisions that are the maintainer's to make (incl. the two spec
  discrepancies surfaced per the pause-and-ask rule).
