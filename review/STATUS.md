# In-flight review status (2026-07-23)

Triage after debt-ledger refresh against `main` @ `8cc3ea5`. Update when a
finding is fixed or a question is decided.

## At a glance

| Review | Findings delivered? | Code/docs follow-ups | Ready to archive? |
|--------|---------------------|----------------------|-------------------|
| `debt-ledger/` | yes (2026-07-20; **refreshed 2026-07-23**) | **DONE:** Q1/DD1 #171, D5/D6 #170, S2/S3+T1 #184. **Open:** D1 (#172), D2, D3 (#176), DD4 (#177), T2/T3/T7, D4, D7 (archive completed OpenSpec), T4 half-test. Q2/Q5 open (embodied in #172/#176) | no |
| `performance/` | yes (#134 + follow-ups) | P3–P5 done; listing L0–L3 + peers (#143/#146/#148); residual band miss; **Q2 decided** (drift gate #171); **Q4 open** | no |

Archived earlier: `archive/2026-07-19-api-coherence/`,
`archive/2026-07-19-stream-layering/`, `archive/2026-07-20-cli-product/`.
OpenSpec archived: `archive/2026-07-20-stop-on-failure-not-policy/`.
Pending archive (**debt-ledger D7**): `unify-pass-driver`, `gzip-zlib-truncation-recovery`.

---

## 1. Actionable right now (recommended order)

### From `debt-ledger/` (remaining pay list)

| ID | Action | Notes |
|----|--------|-------|
| **D1 / DD3 / Q2** | Re-word VISION/philosophy/costs; publish measured ratios | Open **#172** (lean = aspirational bands) |
| **D2** | Write `SECURITY.md` / disclosure process | No PR yet |
| **D3 / Q5** | Start `CHANGELOG.md` | Open **#176** (rebase — was conflicting) |
| **DD4** | Finish `rapidgzip-truncation-investigation` | Open **#177** (rebase — was conflicting); #183 is stdlib-only |
| **T2** | Parametrize seek-interleaving over lzip/`.Z` | Cheap; no PR |
| **D4 / D7** | `open-issues.md` P1 sweep; archive completed OpenSpec changes | Housekeeping |
| **T3** | Benchmark-gate RAR / encrypted / accelerator data cases | perf P6 remainder |
| **T7** | Corpus-matrix audit (ISO beyond `basic`; record exclusions) | Half-day |
| **T4 half** | One multithread `members_report_if_available` barrier test | Small |

### From `performance/`

| ID | Action |
|----|--------|
| **P7 residual** | ZIP many-small ~3.7× (above 2–3×); 7z ~2.0–2.2× (above 1.25×). Resolve with debt-ledger Q2 / #172, or commission L5. |
| **P6 remainder** | RAR / encrypted / accelerator *data* harness cases (= debt-ledger T3). |
| **VISION/docs** | Re-word ≤1.3× claim (= debt-ledger D1 / #172). |

---

## 2. Still needs decisions

### `debt-ledger/QUESTIONS.md`

| Q | Finding | Status |
|---|---------|--------|
| **Q1** | Perf wall-budget enforcement (perf Q2) | **decided + done** — #171 |
| **Q2** | ZIP listing above band: L5 vs publish honest/aspirational | **open** — lean (b); embodied in **#172** |
| **Q3** | S2+S3: entry gate vs pay pre-release | **decided + done** — #184 |
| **Q4** | rapidgzip-truncation rides past 0.2.0? | **decided PAY**; impl open — **#177** |
| **Q5** | CHANGELOG form | **open** — lean committed file; embodied in **#176** |

### `performance/QUESTIONS.md`

| Q | Finding | Status |
|---|---------|--------|
| **Q2** | **P1** wall-budget enforcement | **decided** (= debt-ledger Q1 (a) / #171) |
| **Q4** | Verify-skip knob (lean leave-as-is; close when archiving) | open |

---

## 3. Future / archive-copy (still live under `performance/`)

| ID | Notes |
|----|-------|
| **P8** | rapidgzip AUTO threshold may be conservative for seek. |
| **P9** | Measurement blind spots (7z password-confirm; RAR solid rewind). |
| Extract residual | Safety floor; realistic ~1.9× already in band. |
| **L5** | Lazy `ArchiveMember` derivation — OpenSpec when commissioned (or after #172). |
| Topic 6 | Decode-engine performance — `backlog.md` (includes stream Q4). |

---

## Parked when archiving (do not re-open those dirs)

| Item | Where |
|------|-------|
| api-coherence **Q5** (`verify` / `VerifyReport`) | `IDEAS.md` |
| api-coherence digest fill | **Done** #160 |
| api-coherence **D1** (ANTI / non-current list marks) | Folded into `cli-product/` → **done** in archive |
| stream-layering **Q4** (`SlicingStream.readinto`) + optional `VerifyingStream` delete | `backlog.md` Topic 6 |
| api-coherence **Q7** | Done #157 |
| cli-product **P4** `--json` | Wait for `hash` / member schema — `IDEAS.md` / debt-ledger DD7 |
| cli-product **Q4** `--raw` / TTY-only quoting | debt-ledger DD8 |

Full table also in `backlog.md` → "Parked from archived deep reviews".

---

## Already addressed (selected)

| Item | Where |
|------|--------|
| stream-layering F1/F2/D1/D2; Q1–Q3 | #137 |
| api-coherence Q1–Q6 impl | #154 |
| ExtractionStatus rename polish | #156 |
| api-coherence Q7 `members_report` | #157 |
| Stored stream digests (lzip multi + Adler omit) | #160 |
| Listing L0–L3 + peers + 7z byte-cursor | #143/#146/#148 |
| Perf P3–P5, decode-feed, O8 | #136/#139/#141 |
| cli-product P1–P3/P5–P14/D1 | #144 follow-ups + #163/#165 |
| OpenSpec `stop-on-failure-not-policy` | #165 → archived 2026-07-20 |
| Nightly wall-ratio drift (debt-ledger Q1) | #171 |
| Unify pass-stream driver + solid-RAR mutation (S2/S3/T1) | #184 |
| Stdlib gzip zlib truncation recovery | #183 |
| ADR 0014 integrity from reads not close | #186 |
| `pyppmd` quiesce-on-close + valgrind UAF gate | #188/#189 |
