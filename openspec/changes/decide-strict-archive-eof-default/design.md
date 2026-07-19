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

A single monolithic bool cannot serve both jobs without hurting someone. But the check
already computes a discriminating signal — `observed_kind` — that lets us split the two
*without* a native TAR walker (see "The `observed_kind` signal" below). The residual the
signal cannot resolve (`absent`/`short`: complete-trailer-less vs. truncated-at-boundary)
stays ambiguous until native TAR (P3); that residual is what the opt-in flag still owns.

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

### Current behavior (pre-Option-F inventory; superseded by Decision 2)

At the time of the option survey, `_verify_tar_eof` treated every trailer failure the
same (warn by default / `TruncatedError` under strict), with no probe:

| Case | Default (`False`) | `strict_archive_eof=True` |
| --- | --- | --- |
| Valid two-block trailer | OK | OK |
| Minimal valid trailer (`tar -b1` style) | OK | OK (regression locked) |
| Missing / short / nonzero second marker block | WARNING + diagnostic; pass completes | `TruncatedError` after delivery |
| Mid-archive corrupt header (stdlib silent EOF) | Same WARNING path if trailer check fails | Same `TruncatedError` |
| Diagnostic code `IGNORE` + strict True | Count increments; still `TruncatedError` | (specced precedence) |

Tests at survey time: `tests/test_tar.py` (EOF section), `tests/test_archivey_config.py`,
`tests/test_diagnostics.py` (IGNORE vs strict). Post-Option-F behavior is the matrix under
"Where the flag and the reader actually change the outcome" below.

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

### The `observed_kind` signal (the cheap split, no native TAR needed)

`_verify_tar_eof` runs **after** `tarfile` stops iterating, and `tarfile` never reports
*why* it stopped: hitting the real trailer, hitting a corrupt non-first header
(`InvalidHeaderError`/`TruncatedHeaderError`/`EmptyHeaderError` at `offset != 0` with
`ignore_zeros=False`), and running out of data all return `None` and set `_loaded`. Intent
is unobservable. The **only** signal is what physically sits at the file position when the
pass ends, already captured as `ArchiveEofContext.observed_kind`:

| `observed_kind` | what the trailer read sees | most likely cause |
| --- | --- | --- |
| `absent` (0 bytes) | true EOF | trailer-less-but-**complete** tar (common, legit) · truncation exactly at a member boundary |
| `short` (<512) | partial trailing block | rare damaged tail after a consumed block |
| `nonzero` (512, not all-zero) | a block that is neither a trailer nor a parseable header, **with more data present** | `tarfile` bailed on a bad block early → genuine mid-archive corruption / silent shorten |

Two facts make `nonzero` a trustworthy hard-fail trigger:

1. **A conformant, complete tar essentially never yields `nonzero`.** Every well-formed tar
   ends in ≥2 null blocks; even minimal `tar -b1` has exactly two, so the trailer read lands
   on a null block and returns `OK` before `observed_kind` is ever set. Proper-trailer +
   trailing junk stops cleanly on the trailer (junk unread); `cat`-joined tars either stop at
   the first trailer or parse straight through as one archive. The only way to reach `nonzero`
   is a non-trailer block sitting where a header/trailer should be — i.e. `tarfile` gave up
   early. GNU tar flags the same shapes ("A lone zero block", "Skipping to next header").
2. When `tarfile` stops on a corrupt non-first header it leaves the handle positioned in live
   data, so the trailer read picks up that data → `nonzero`. (The trailing-block check
   inspects the block *after* the one that stopped `tarfile`, so as a *proxy* it is reliable
   for mid-archive corruption but misses a corrupt header in the file's final block —
   that case degrades to `absent` under the trailing check alone.) Random access closes that
   final-block gap with `_EofProbeStream` (Decision 2); streaming still has the gap.

So `nonzero` ≈ "the tar iteration finished early on an invalid block" — exactly the case
worth raising on by default. `absent`/`short` remain the irreducibly ambiguous bucket that
must stay lenient by default to honor Phase 5 / GNU tar, and that the opt-in flag escalates
for callers who need provable completeness.

**What `absent`/`short` does *not* cover (verified against stdlib, both `r:` and `r|`):**
truncation *inside* a member's data or a partial header block already hard-fails **during
iteration**, independent of the flag — `tarfile`'s lazy seek-and-probe raises
`ReadError: unexpected end of data`, which the backend translates to `TruncatedError`. So
the residual is not "all truncation"; it is specifically **"the stream ended cleanly on a
member boundary but the two-zero-block trailer is absent/incomplete."** That case is
*byte-identical* between a deliberately trailer-less complete tar and a tar cut off exactly
after a whole member — TAR stores no archive length, no member count, and no end sentinel
other than the trailer whose absence is the question — so **no reader, seek, or rolling
buffer can disambiguate it.** The ambiguity is intrinsic to the format, not an artifact of
the stdlib backend; a native TAR walker (P3) improves precision and salvage on the
*detectable* cases, but does not make this residual decidable, which is why the flag
survives a native reader.

### Where the flag and the reader actually change the outcome

`{stdlib tarfile, native P3}` × `{strict_archive_eof False, True}`, across end conditions.
Cells verified against stdlib in-tree; native = the P3 walker's expected behavior. Only the
**bold** rows contain any variation across the four cells.

| End condition | tarfile · False | tarfile · True | native · False | native · True |
| --- | --- | --- | --- | --- |
| Valid two-block trailer | OK | OK | OK | OK |
| **Trailer-less complete / truncated exactly at member boundary** (byte-identical) | warn | **`TruncatedError`** | warn | **`TruncatedError`** |
| Truncated mid-member-data | `TruncatedError` | `TruncatedError` | `TruncatedError` | `TruncatedError` |
| Truncated mid-header (partial block) | `TruncatedError` | `TruncatedError` | `TruncatedError` | `TruncatedError` |
| Corrupt non-first header, data follows (`nonzero`) | `CorruptionError` | `CorruptionError` | `CorruptionError` | `CorruptionError` |
| Corrupt header in the _final_ block, random-access (via `_EofProbeStream`) | `CorruptionError` | `CorruptionError` | `CorruptionError` | `CorruptionError` |
| **Corrupt header in the _final_ block, streaming** (probe unavailable) | **warn** | **`TruncatedError`** | **`CorruptionError`** | **`CorruptionError`** |

Two facts fall out, and they are nearly orthogonal — each knob is load-bearing in exactly
one narrow spot:

- **`strict_archive_eof` changes the outcome only for the `absent`/`short` bucket** — a
  stream that ended cleanly on a member boundary with no valid trailer (trailer-less-complete
  *or* truncated-at-boundary, indistinguishable). `False` → warn, `True` → `TruncatedError`.
  In every other row — valid trailer, any mid-stream truncation, `nonzero` corruption,
  random-access final-header corruption — the verdict is fixed and the flag is inert.
- **stdlib vs native change the pass/fail verdict only for corruption that still evades
  the probe** — chiefly a corrupt header in the archive's *final* block under
  **streaming**, where tarfile's `_Stream` hides header reads so the offset probe is
  unavailable and the trailing-block check misclassifies the stop as `absent`. Random
  access catches that case via `_EofProbeStream` (including after a GNU sparse member).
  A native walker would raise `CorruptionError` in streaming too. For all truncation and
  for corruption that leaves trailing data both readers agree; native's extra value there
  is precision (exact offset) and salvage, not a different pass/fail on the RA path.

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

**Option D — Keep default False; teach loudly; CLI strict wedge**  
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

**Option F — Signal-aware default + keep the opt-in** *(LOCKED + IMPLEMENTED, see Decision 2)*  
Classify the end-of-archive on what tarfile actually stopped on, not a single monolithic bool:
- **Rejected header → `CorruptionError`, regardless of the flag.** When tarfile stops the
  scan on a block it could not parse as a header (a corrupt member header after the first,
  treated as a silent early end), a conformant tar never produces this. Detected via the
  `_EofProbeStream` stop-block capture (below) in random access — including when the bad
  header is the archive's *final* block and after a GNU sparse member — and via the
  trailing-block proxy in streaming.
- **Missing / short trailer → flag-governed.** A stream that ended cleanly on a member
  boundary without a valid two-block trailer (`observed_kind` `absent`/`short`) → warn by
  default (Phase 5 / GNU-tar compatible), `TruncatedError` under `strict_archive_eof=True`.
- **Detectable truncation is out of scope** — truncation inside member data or across a
  partial header already raises `TruncatedError` during iteration (stdlib "unexpected end of
  data"), in both modes, flag-independent.
- **Surfacing (via `partial-members-and-errors` / #157):** the escalation is a terminal
  listing error. `members()` / `scan_members()` raise it (complete-or-raise); `members_report()`
  returns the recovered prefix + `error`; `__iter__` yields the prefix then raises. `extract_all`
  on random access **fails closed** (extract-prep materializes the list before writing → raises
  before any output), while streaming writes salvageable members then raises. No soft-extract
  report field (that stays Option E / a future salvage change).
**Pros:** Closes the *detectable*-corruption slice of the inventory-honesty gap (P1) without a
native TAR walker; does **not** break trailer-less / `cat`-joined tars; keeps the flag for the
ambiguous residual; no `config.py` default change, no signature change; random-access extract
never leaves partial output from a corrupt archive.  
**Cons:** Minor behavior change — archives that today only *warn* on a rejected header now
raise; a rejected *final* header is caught only in random access (streaming's `_Stream` hides
the block — P3); no lenient escape for a caller who *wants* to read a corrupt tar without an
exception until a salvage/best-effort mode exists (`IDEAS.md`).

#### `_EofProbeStream` — the stop-block capture (implemented)

A thin read/seek proxy wraps the seekable fileobj handed to tarfile in random-access mode and
records the `(offset, bytes)` of the most recent read (empty reads included). Right after the
header scan, `_capture_eof_probe` inspects that read: `TarFile.next()` always attempts one
more header block before returning `None`, so `last_read` *is* the stop block. A full
non-null block there is a rejected header → corruption. This does **not** key on
`offset_data + roundup(size)` — that formula uses logical size and is wrong for GNU sparse
(logical ≫ packed), which previously false-negatived final-header corruption into a
missing-trailer warning. The snapshot is taken during the scan so later member extraction
moving the shared handle cannot corrupt it. It is **passive forward capture**: no backward
seek, so no re-decompression on a compressed source.

### 2. Decision (LOCKED + IMPLEMENTED — Option F)

**Option F is chosen and implemented for v1.** The real gap P1 named is "callers don't look at
diagnostics," but for the *detectable* corruption case the safe answer is not "hope they read
logs" (Option A/D) nor "break every trailer-less tar" (Option B/C) — it is to **raise on the
signal we can trust and stay lenient on the signal we cannot**. The `_EofProbeStream`
stop-block capture gives that split today; native TAR (P3) is still what will eventually
close the streaming final-header gap. So `False` no
longer means "silent on everything" — it means "silent only on the ambiguous-EOF residual,"
and the flag's job narrows to "escalate that residual too."

- **`config.py` default stays `False`.** Not breaking on defaults for the common corpus.
- **Breaking-ness:** minor — only genuinely-malformed (rejected-header) tars change from warn
  to raise. Blast radius excludes the trailer-less / `cat`-joined corpus. Ship with a release
  note; the only "read it anyway" escape is a future salvage mode, called out as a known gap.
- **Exception types:** rejected header → `CorruptionError` (a bad block is present); `absent`/
  `short` under strict → `TruncatedError` (data ran out). This splits today's uniform
  `TruncatedError` escalation.
- **Streaming limitation:** a rejected *final* header is not caught in streaming (documented in
  `docs/internal/known-issues.md` + user Gotchas; P3 closes it).
- **CLI (`cli-v1`, cross-note):** `archivey test` defaults to strict (validator = maximally
  paranoid); plain reads use the signal-aware default.

**Rejected:** A/D (leave `nonzero` silent — fails the founding inventory-honesty need), B/C
(break the wild trailer-less corpus), E (soft-extract report semantics — more surface than v1
needs before native TAR).

### 3. Spec / docs posture

Spec deltas in this change now assume **Option F** (three-bucket behavior in `format-tar`;
narrowed flag + new default taught in `documentation`). `config.py` default and
`archive-reading` config signature are unchanged, so no `archive-reading` delta is needed.

User Gotchas (separate docs work) should mention, regardless of option:

- Today’s silent-shorten risk + the diagnostic / strict knob.
- “May improve with a native TAR reader later” (post-v1; irreducible until then).

### 4. Cross-link open-issues P1

Reword `docs/internal/open-issues.md` P1 to "decided — Option F (signal-aware default: raise
on `nonzero`, keep flag for the `absent`/`short` residual)"; point back at this change. P3
(native TAR) still owns the `absent`/`short` structural fix.

## Risks / Trade-offs

| Risk | Mitigation |
| --- | --- |
| `nonzero`→raise breaks a caller relying on reading a malformed tar | Narrow blast radius (excludes trailer-less/`cat`-joined); release note; salvage mode tracked in `IDEAS.md` as the future escape |
| A false-positive `nonzero` on a legit archive | Analysis shows conformant tars never reach `nonzero` (≥2 null trailer blocks stop the check first); guard with a `tar -b1` + trailing-padding regression fixture |
| Extract-at-end raise after successful `nonzero` writes surprises callers | Surfaced via `partial-members-and-errors` (#157): `members_report()` exposes the recovered prefix + error; `__iter__` yields then raises (both modes); RA `extract_all` fails closed (materializes before any write); streaming writes salvageable members then raises. |
| `absent`/`short` truncation still silently shortens inventory | Explicit residual; `strict_archive_eof=True` escalates it; native TAR (P3) is the structural fix |
| Docs teach the new default then it changes again | This change owns the decision; docs cite the `observed_kind` split + config field, not "archivey always warns" |

## Open Questions

1. ~~Which option (A–E)?~~ **Resolved:** Option F (Decision 2).
2. **CLI (`cli-v1`):** should `archivey test` hard-code strict EOF, or expose `--strict-eof`
   over a strict default? Defer detail to `cli-v1`; this change only records the intent.
3. **Salvage escape hatch:** a caller who wants to read a `nonzero` tar *without* an
   exception has none until a best-effort / salvage mode exists (`IDEAS.md`). Track there,
   not here.
