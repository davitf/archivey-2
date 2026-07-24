# Doc ↔ spec ↔ code drift + the deferred-decision register

Original refs: `main` @ `7bb862b`. **Status refresh 2026-07-24** against
`main` @ `bdf5ffd`. Live OpenSpec: `unify-pass-driver` ✓ Complete,
`gzip-zlib-truncation-recovery` ✓ Complete, `rapidgzip-truncation-investigation`
1/13, `seekable-gzip-and-block-writing` 0/24.

## D1 — VISION ≤1.3× claim — **DONE (#191)**

`VISION.md` / `docs/philosophy.md` / `docs/costs.md` now publish **aspirational
peer-ratio bands** plus a measured table from nightly realistic 2026-07-23
(ZIP open+list **4.44×**, extract **2.38×**, 7z **2.13×**, RAR **2.39×**).
L5 deferred to `IDEAS.md`. Wall-*drift* enforcement landed earlier (#171).

## D2 — no SECURITY.md (PAY)

Still absent. **PAY before 0.2.0**; OSS-Fuzz may trail.

## D3 — no CHANGELOG (PAY)

Still absent on `main`. Open PR **#176** (was conflicting — rebase). **PAY.**

## D4 — `open-issues.md` stale (PAY)

P1 (Option F) still under "candidates to fix" with dead change-path refs;
suggested-first-cuts still says "apply it". **PAY** 15-min sweep.

## D5 / D6 — **DONE (2026-07-20)**

## D7 — completed OpenSpec changes unarchived (PAY)

| Change | Status | Action |
|--------|--------|--------|
| `unify-pass-driver` | ✓ Complete (#184) | Archive + sync |
| `gzip-zlib-truncation-recovery` | ✓ Complete (#183) | Archive + sync |

## What is *not* drifting (fine)

CLI docs, ExtractionStatus, `OnError.STOP`, threat-model spot checks, OpenSpec
sync for shipped changes. `docs/grab-bag/` historical — KEEP.

## Adjacent landings

- #183 / ADR #186 — stdlib gzip recovery + integrity-from-reads (not DD4).
- #188/#189 — `pyppmd` mitigations (**N1** KEEP).

## Deferred-decision register

| ID | Decision | Verdict |
|---|---|---|
| **DD1** | Wall-budget enforcement | **DONE** #171 |
| DD2 | Verify-skip knob | **KEEP** (leave-as-is); close when archiving `performance/` |
| **DD3** | L5 vs honest/aspirational bands | **DONE** #191 — Q2 (b) |
| **DD4** | rapidgzip truncation investigation | **PAY** — #177 |
| DD5 | seekable-gzip-and-block-writing | **KEEP** post-0.2.0 |
| DD6 | Salvage mode | **KEEP** for 0.2.0 |
| DD7/DD8 | CLI `--json` / `--raw` | **KEEP** |
| DD9–DD12 | Threat-model / C3 / Q5 / C4 | **KEEP** |
| **N1** | pyppmd residual | **KEEP** |
