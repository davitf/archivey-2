## Context

Threat-model O5 / PLAN Phase 6 entry gate: mutation harness + Hypothesis landed;
coverage-guided Atheris for native parsers did not. Existing
`tests/fuzz_sevenzip_parser.py` is env-gated mutation, not Atheris.
`docs/internal/threat-model.md` still lists Hypothesis as open (stale) and
couples Atheris with OSS-Fuzz/`SECURITY.md` at release — this change splits them.

Maintainer decisions (explore 2026-07): bursty activity → fuzz on **push to
`main`** + **`workflow_dispatch`**, not always-on nightly; partitioned ~120s
budget; headers **and** open+members; include `detect_format` and shallow
ZIP/TAR/ISO; defer `SECURITY.md` / OSS-Fuzz / accelerator sandbox.

## Goals / Non-Goals

**Goals:**

- Shared Atheris infra with seeds, budgets, crash artifacts, repro commands.
- Partitioned main-push job that deep-stresses 7z headers and also exercises
  detection + leaf open/list paths.
- RAR target scaffold that skips cleanly until the backend registers.
- Spec/threat-model updates so the gate is normative, not tribal knowledge.

**Non-Goals:**

- OSS-Fuzz onboarding.
- `SECURITY.md` / disclosure process (follow-up).
- Accelerator hang sandbox (document accelerators-off only).
- Full extract inside Atheris (mutation harness already covers extract).
- Stream/codec-only targets (follow-on).
- Making Atheris a PR-matrix requirement.

## Investigations

### Why not only 7z/RAR

Mutation fuzz already sweeps open/list/read/extract across formats. Atheris ROI
is highest on dense pure-Python parsers archivey owns (7z today, RAR next) and
on tricky entry points (`detect_format`). ZIP/TAR mostly parse via stdlib; still
worth a **shallow** open+members slice for wrapper/error-translation bugs. ISO
needs a **hard timeout** (known pycdlib hang class).

### CI discovery vs flake

Atheris going red means crash/hang/timeout/OOM — not a soft assertion flake.
Short budgets make **finding** non-reproducible across identical commits (lucky
path exploration). Mitigation: upload crashing input + print one-line repro;
treat red-without-diff as “keep the artifact,” not “ignore.”

### Packaging: `[fuzz]` extra vs dependency group

Explore preferred a `[fuzz]` extra. `packaging-and-extras` requires user-facing
extras to map to `src/` runtime imports and parks test-only deps in PEP 735
groups. **Atheris is never imported from `src/`**, so a runtime extra would
violate that contract (and risk leaking into `[all]`).

## Decisions

### 1. Trigger: push to `main` + `workflow_dispatch`

Bursty/dormant workflow: run when main moves; manual deepen anytime. Rejected
always-on nightly (waste + stale failures) and “nightly if dirty” as the *only*
gate (separates fault from notice). Optional dirty-nightly soak can land later.

### 2. Dependency: PEP 735 `fuzz` group (not a runtime extra)

```toml
[dependency-groups]
fuzz = ["atheris>=…"]
```

CI: `uv sync --group fuzz` (plus whatever core/`[all]` the targets need).
**Rejected:** user-facing `[fuzz]` extra without a packaging allowlist. If a
pip-discoverable extra is wanted later, add an explicit “tooling extras,
excluded from `[all]`” packaging rule first.

### 3. Partitioned ~120s main-push budget (defaults)

| Slice | Seconds | Target |
| --- | --- | --- |
| 7z headers | 55 | `parse_sevenzip_archive` (or equivalent) |
| 7z open+members | 25 | `open_archive` + list/materialize |
| detect_format | 15 | peekable prefix → `detect_format` |
| ZIP+TAR open+members | 15 | shallow |
| ISO open+members | 10 | shallow + hard wall timeout |

`workflow_dispatch` / env overrides MAY lengthen slices. Extract is out of the
Atheris job. Accelerators forced **off**.

### 4. Target set and RAR scaffold

Implement shared runner; register targets above; RAR metadata (+ open when
available) registered but **skipped** until the backend is importable/registered.
Streams/codecs deferred.

### 5. Success / failure contract

Success: budget expires with only typed `ArchiveyError` or clean returns — no
raw exceptions, no hang past slice timeout, no process abort. Failure: non-zero
exit; upload repro bytes/artifact; print local re-run command. Job is required
on `main` for visibility (red X on the merge commit).

### 6. Relationship to existing harnesses

Keep `tests/test_mutation_fuzz.py` and `tests/fuzz_sevenzip_parser.py` (mutation /
`ARCHIVEY_FUZZ`). Atheris is additive coverage guidance, not a replacement.
Hypothesis remains the property layer (`testing-contract` already specs it).

### 7. Defer SECURITY.md

Disclosure docs are release packaging, not required to land the harness.

## Risks / Trade-offs

| Risk | Mitigation |
| --- | --- |
| Non-reproducible discovery on short budgets | Crash artifacts + repro command; dispatch longer runs |
| ISO/accelerator hangs kill the job | Accelerators off; ISO slice hard-killed |
| Atheris / libFuzzer platform friction | Linux `ubuntu-latest` only for the fuzz workflow |
| Budget starvation of 7z headers | Fixed partition; headers get the largest slice |
| Packaging confusion (`[fuzz]` vs group) | Spec + CONTRIBUTING one-liner |

## Open Questions

None for this proposal.
