# Doc ↔ spec ↔ code drift + the deferred-decision register

All references: `main` @ `7bb862b`. Mechanical baseline: `openspec validate
--all` green (27/27); at review time the three live changes listed as
`stop-on-failure-not-policy ✓ Complete`, `seekable-gzip-and-block-writing 0/24`,
`rapidgzip-truncation-investigation 1/11`. (**D5** archived that complete change
on 2026-07-20.)

## D1 — the VISION ≤1.3× performance claim no longer matches either the measurements or the maintainer's own re-scoping (PAY — top of the ledger)

`VISION.md:74-76` still states the budget as "**≤ 1.3×** stdlib wall-time for
the common paths (open/list/read/extract…)". The performance review measured
ZIP open+list at ~3.7–4× post-optimization, 7z listing ~2.0–2.2× vs py7zr,
ZIP many-small extract ~3.7× (`review/performance/QUESTIONS.md` Q1,
`budget-table.md`), and the maintainer **already re-scoped the claim** (Q1
direction, 2026-07-18): metadata ops get *ratio bands vs the relevant peer*
(ZIP/TAR listing ≤2–3×, native 7z/RAR ≈ par), decompression-dominated paths
keep ≤1.3×. None of that re-scoping has reached `VISION.md`, `docs/philosophy.md`,
or `docs/costs.md` — and `philosophy.md` explicitly invites skeptics to
benchmark. Shipping 0.2.0 with the old sentence publishes a falsifiable claim
the project's own numbers falsify.

This is the highest freezes-at-release item on the ledger: a public claim,
quoted in announcement posts and package metadata, is the single hardest thing
to walk back. **PAY before 0.2.0**: re-word VISION + philosophy + costs to the
Q1 band structure. Only the *enforcement* wording (is the band CI-gated or
measured-and-published?) waits on performance Q2 — the band honesty does not.
Residual: ZIP open+list is still **above** its own re-scoped 2–3× band; either
L5 (lazy `ArchiveMember` derivation, needs an OpenSpec) lands pre-release or
the published band must say where ZIP actually sits today (see `QUESTIONS.md`
Q1/Q2).

## D2 — no SECURITY.md / disclosure process, and it gates the release's own marketing (PAY)

Threat-model O5.4 and `PLAN.md`'s release bundle both say it: OSS-Fuzz
onboarding and a `SECURITY.md` with a disclosure process are "before any
public 'safe' claim". VISION's load-bearing claim #2 *is* a safety claim.
`SECURITY.md` is a day's work and freezes reputationally at release (a
security-positioned library launching without a disclosure path is a story
adopters remember); OSS-Fuzz onboarding can trail the release. **PAY
(SECURITY.md) before 0.2.0; OSS-Fuzz may follow.**

## D3 — no CHANGELOG (PAY — cheap) — **DONE (2026-07-20)**

Paid with debt-ledger Q5: committed Keep a Changelog `CHANGELOG.md` (entry-zero
under `[Unreleased]` until `0.2.0` is tagged) plus
`docs/internal/release-checklist.md` for the every-release update loop.
GitHub release notes may mirror; they are not a substitute.

`pyproject.toml` remains at `0.2.0.dev0` until the release cut.

## D4 — `docs/internal/open-issues.md` has gone stale against its own resolutions (PAY — 15-minute sweep)

- **P1** (TAR EOF strictness) is marked "DECIDED + IMPLEMENTED (Option F)" yet
  still sits under "**Product — candidates to fix**", its refs point at
  `openspec/changes/decide-strict-archive-eof-default/` (archived by #162 —
  the live path no longer exists), and "Suggested first cuts" item 1 still
  says "apply it" (`open-issues.md:34-55,185-189`). Move to Closed, fix the
  ref to the archive path.
- The snapshot header says "2026-07-18 against `main` @ `93dc28e`" — fine as
  provenance, but the P1 row contradicts the file's own bucket rules ("when an
  item ships, move it to Closed").
- **P6** refs "PR #101 (still open) /
  `docs/internal/rar-unrar-piping-investigation.md` (when merged)" — verify
  that pointer: the file does not exist in the tree today, so either the PR
  landed without it or the ref is dead either way.

Small, but this file is the designated gotchas-triage register — drift *here*
compounds, because future reviews are told to trust it. **PAY.**

## D5 — `stop-on-failure-not-policy` is complete but unarchived — **DONE (2026-07-20)**

Archived to `openspec/changes/archive/2026-07-20-stop-on-failure-not-policy/`.
Main `cli` / `safe-extraction` specs were already synced (no further sync needed).

## D6 — review lifecycle: `cli-product/` is done-pending-recording — **DONE (2026-07-20)**

Archived to `review/archive/2026-07-20-cli-product/`. Parked leftovers:
**P4/`--json`** → `IDEAS.md` / DD7; **Q4/`--raw`** → DD8.

## What is *not* drifting (checked, fine)

- **CLI docs** are present and current: `docs/usage.md:207-253` documents the
  verb grammar, the safer-extract demo, `--stop-on-error`, and the full exit
  map including `3` (completed-with-blocks) and reserved `≥4` / `--salvage` —
  matching `cli/spec.md` and `cli/exit_codes.py` post-#163/#165.
- **ExtractionStatus renames** (#156) are reflected in `docs/usage.md:143`
  (`SUPERSEDED` vs `NOT_OVERWRITTEN`) and `safe-extraction.md` (`BLOCKED`).
- **`OnError.STOP` failures-only** (#165) reached `gotchas.md:116`,
  `safe-extraction.md:54-57`, the spec delta, and the CLI docs in the same
  change — the sync discipline worked.
- **Threat-model register** statuses spot-checked against code: O2/O3/O4/O7
  "implemented" claims match `internal/filters.py`/extraction behavior and
  tests; O8's mitigation (zero-file decoded header ⇒ `EncryptionError`)
  matches `format-7z` and `test_header_encrypted_empty_decoded_header_rejected`.
- **Specs validate strict-clean** and the spec↔docs↔code sample probes
  (exit codes, EOF Option F, digests) agree. The `archive-writing` spec
  describes an unbuilt Phase 9 capability by design — labeled, not drift.
- **`docs/grab-bag/`** is explicitly labeled historical/non-normative in
  `CLAUDE.md` and the nav. Triage remains "later" per CLAUDE.md — acceptable
  to KEEP past 0.2.0 (internal docs, no freeze), noted for completeness.

## The deferred-decision register (each needs its verdict recorded once)

| ID | Decision | Where it lives | Verdict |
|---|---|---|---|
| **DD1** | Performance **Q2** — where the wall budget is enforced (nightly-vs-previous JSON, 2× band on read_all, or informational) | `review/performance/QUESTIONS.md` Q2 | **DECIDED (2026-07-20)** — (a) nightly wall-ratio drift vs previous successful JSON; skip re-publish + ≥30d forced re-measure; absolute bands informational. |
| DD2 | Performance **Q4** — verify-skip knob | same, Q4 | **KEEP** (lean leave-as-is already recorded; perf case ~nil post-#137). Record as closed-no-knob. |
| DD3 | ZIP listing above its own band — land **L5** or publish the honest number | STATUS "residual band miss" | **DECIDE pre-0.2.0** (part of D1; `QUESTIONS.md` Q2). |
| DD4 | `rapidgzip-truncation-investigation` (1/11) — the shipped ISIZE backstop is a heuristic built on admittedly incomplete knowledge (`proposal.md`) | in-flight change | **KEEP for 0.2.0 with justification**: accelerators are opt-in, AUTO additionally gated on verifiable decompressed size, threat model already scopes accelerators out of the defended surface; refining the backstop post-release is non-breaking. Keep the change open; don't let 0.2.0 *close* it silently. |
| DD5 | `seekable-gzip-and-block-writing` (0/24, spec-only, Phase 8) | in-flight change | **KEEP** — post-0.2.0 feature by plan; additive. |
| DD6 | Salvage / best-effort read mode — the founding use case, unbuilt; reads are all-or-error | `backlog.md`, `IDEAS.md`, reserved `--salvage` | **KEEP for 0.2.0** — already an explicit, recorded sequencing decision (PLAN: post-0.2.0); CLI grammar + `members_report()` shape keep it additive. Confirm `docs`/README don't over-promise it (checked: gotchas/philosophy phrase it as recoverable-prefix + honest error, which #157 delivers). |
| DD7 | CLI **P4** `--json` | cli-product Q2, decided | **KEEP** — recorded: wait for `hash`/member schema; exit codes and grammar already reserve the space. |
| DD8 | CLI **Q4** remainder — `--raw` / TTY-only quoting | cli-product Q4 | **KEEP** — additive flag; recommended style already applied everywhere. |
| DD9 | Threat-model residuals: O1 unbounded `read()`/`open()` sizes; O6 nested-archive recipe; O7 un-escape helper; O8 hardenings (consume-entire-header etc.) | `threat-model.md` | **KEEP** — each is additive post-release (config knob, doc recipe, helper, parser strictness); all are already recorded with rationale. The register itself is the justification artifact — keep it authoritative. |
| DD10 | C3 metadata fidelity (xattrs/ACLs) | threat-model / IDEAS | **KEEP** — binds at writing-spec time (Phase 9), decision recorded. |
| DD11 | api-coherence **Q5** `verify`/`VerifyReport` | `IDEAS.md` | **KEEP** — deferred past 0.2.0, recorded; additive API. |
| DD12 | Free-threaded claim scope (core-only CI job) | threat-model C4 + `ci.yml:168` | **KEEP** — the honest scoping is *already published* ("optional backends not claimed covered"); that is the justification. See tests T4. |
