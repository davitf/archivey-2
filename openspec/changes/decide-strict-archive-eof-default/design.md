## Context

`ArchiveyConfig.strict_archive_eof` (default `False`) governs what happens after a full
TAR member pass when the POSIX two-block null trailer is missing or invalid
(`tar_reader._verify_tar_eof`):

- `False` → emit `ARCHIVE_EOF_MARKER_MISSING` (count / retain / log / callback per
  diagnostic policy); pass completes.
- `True` → same diagnostic delivery rules, then escalate to `TruncatedError` (takes
  precedence over `DiagnosticRaisedError`, including when disposition is `IGNORE`).

Provenance:

- **Phase 5 design** (`openspec/changes/archive/2026-07-07-phase-5-public-api/design.md`
  §4): default False on purpose — trailer-less / `cat`-joined tars are common; GNU tar
  warns; a raise at end-of-pass is awkward for `extract` after a successful run; under
  `OnError.CONTINUE` there is no member to attribute the failure to.
- **Deep review W1** (`review/deep-unknown-unknowns.md`): stdlib `tarfile` treats a
  corrupt member header *after the first* as clean EOF. The EOF-marker check is the only
  backstop; under default False, mid-archive corruption that shortens the listing surfaces
  only as WARNING. Suggested considering RA-only default True; longer-term native TAR.
- **Gotchas triage** (`docs/internal/open-issues.md` P1): asked whether to flip the
  default before user Gotchas teach the wrong story. Maintainer deferred the call into
  this change.

Same diagnostic, two jobs:

| Cause | How common | Ideal outcome |
| --- | --- | --- |
| Missing / short / nonzero trailer on an otherwise complete listing | Common in wild | Warn (GNU-tar-like) or soft fail |
| Stdlib silent mid-archive EOF (corrupt non-first header) | Rare but high-stakes for inventory | Hard fail / salvage — today indistinguishable from missing trailer |

Until a native TAR walker owns header iteration, **one bool cannot be honest about both
without hurting someone.**

## Goals / Non-Goals

**Goals:**

- Record every viable default / split / soft-fail option with trade-offs.
- Pick one stance for v1 (or explicitly keep False and teach).
- Align specs, user Gotchas / `formats.md`, and (if useful) CLI with that stance.
- Point post-v1 native TAR as the structural fix.

**Non-Goals:**

- Implementing a native TAR header walker in this change.
- Changing ZIP / gzip “trailing junk” checks (the knob is named archive-level for later
  extension; TAR is still the only implementation).
- Salvage / best-effort read mode (`IDEAS.md`) — related founding need, separate change.

## Investigations

### Current behavior (verified in tree)

| Case | Default (`False`) | `strict_archive_eof=True` |
| --- | --- | --- |
| Valid two-block trailer | OK | OK |
| Minimal valid trailer (`tar -b1` style) | OK | OK (regression locked) |
| Missing / short / nonzero second marker block | WARNING + diagnostic; pass completes | `TruncatedError` after delivery |
| Mid-archive corrupt header (stdlib silent EOF) | Same WARNING path if trailer check fails | Same `TruncatedError` |
| Diagnostic code `IGNORE` + strict True | Count increments; still `TruncatedError` | (specced precedence) |

Tests: `tests/test_tar.py` (EOF section), `tests/test_archivey_config.py`,
`tests/test_diagnostics.py` (IGNORE vs strict).

### Why default-True hurts extract

The trailer can only be checked **after** the last member. Under strict True:

1. `extract_all` / `stream_members` may write/yield every recoverable member successfully.
2. Then `_verify_tar_eof` raises `TruncatedError`.
3. Callers see “failure” after a successful extract; `OnError.CONTINUE` has no member row
   for an archive-level EOF event (diagnostics own that surface today).

That is why Phase 5 preferred warn-by-default and opt-in strict for validators.

### RA vs streaming split is only a partial fix

Most inventory is path / random-access — also where truncated-but-readable tars appear.
Making only RA strict helps pipes stay lenient but still breaks seekable trailer-less
files and still has extract-at-end awkwardness on paths.

## Decisions

### 1. Options under consideration (maintainer picks one)

**Option A — Keep default False (status quo)**  
No API change. Rely on existing WARNING.  
**Pros:** Compatible; matches GNU tar; Phase 5 intact.  
**Cons:** Inventory honesty remains opt-in; easy to miss if callers ignore logs/diagnostics.

**Option B — Default True everywhere**  
**BREAKING.**  
**Pros:** Strong honesty for inventory and “safe” defaults.  
**Cons:** Breaks common truncated / trailer-less tars; extract-after-success failure; fights
Phase 5 and GNU tar norms.

**Option C — Default True for random-access only; streaming stays False**  
**BREAKING** for path/RA opens.  
**Pros:** Pipes stay lenient; files treated as a trust boundary.  
**Cons:** Seekable truncated tars still break; extract-at-end still awkward; two defaults to
teach; mid-corrupt vs missing-trailer still conflated.

**Option D — Keep default False; teach loudly; CLI strict wedge** *(recommended pending call)*  
Library default unchanged. User Gotchas + `formats.md` teach
`ArchiveyConfig(strict_archive_eof=True)` for inventory/dedupe. CLI (`cli-v1`) exposes
strict EOF on `archivey test` and/or `--strict-eof`. Revisit default only with native TAR.  
**Pros:** No behavior break; puts honesty where “check this archive” users live; aligns with
diagnostics-as-data (don’t assume logs).  
**Cons:** Core `open_archive` still lenient; apps that never set config / never use CLI stay
exposed unless they read Gotchas.

**Option E — Default True + soft extract**  
Library default True for `members()` / full iter, but `extract_all` records archive-level
EOF on the extraction report / diagnostics and does **not** abort after successful member
writes (unless a stricter extract policy is set).  
**Pros:** Inventory hard-fails; extract UX improved vs B.  
**Cons:** New split semantics (list vs extract); more API surface; still breaks truncated
RA listing; design+spec heavy for v1.

### 2. Provisional recommendation (not locked)

**Prefer Option D for v1** unless the maintainer explicitly wants path opens to be a hard
trust boundary (then **C**, accepting extract-at-end and truncated-file fallout).

Rationale: the real gap is “callers don’t look at diagnostics,” which docs + CLI address
without re-litigating Phase 5. Native TAR is what can eventually split the two jobs of the
diagnostic. Flipping the bool now optimizes for one audience and surprises the other.

**Rejected for v1 (unless maintainer overrides):** B (too harsh on wild tars), E (too much
new semantics before native TAR).

### 3. Spec / docs posture while the call is open

Provisional spec deltas in this change assume **Option D**. If B/C/E wins, replace those
deltas before `/opsx:apply` (defaults, extract soft-fail, RA/stream split).

User Gotchas (separate docs work) should mention, regardless of option:

- Today’s silent-shorten risk + the diagnostic / strict knob.
- “May improve with a native TAR reader later” (post-v1; irreducible until then).

### 4. Cross-link open-issues P1

After the decision locks, update `docs/internal/open-issues.md` P1: either “closed —
kept False + teach/CLI” or “closed — default changed to …”.

## Risks / Trade-offs

| Risk | Mitigation |
| --- | --- |
| Choosing D and inventory users still miss the knob | Gotchas + formats + CLI default-on for `test`; optional recipe in usage |
| Choosing C/B and breaking trailer-less corpora | Escape `strict_archive_eof=False`; corpus/CI fixtures; release note |
| Docs teach Option D then default flips later | This change owns the decision; Gotchas cite the config field, not “archivey always warns” |
| Conflating missing trailer with mid-corrupt forever | Explicit non-goal; native TAR in `IDEAS.md` / open-issues P3 |

## Open Questions

1. **Which option (A–E)?** Maintainer call — blocks apply of non-D paths.
2. **If D:** should `archivey test` default to strict EOF, or only `--strict-eof`?
   (Defer detail to `cli-v1` once D is locked.)
3. **If C:** is the split `streaming=False` → strict, or “source seekable” → strict?
   (Prefer access-mode `streaming` flag for teachability.)
4. **If E:** what exact extract report field / status carries archive-level EOF?
