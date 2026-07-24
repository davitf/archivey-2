# In-flight review status (2026-07-20)

Triage after archiving `cli-product/` and OpenSpec `stop-on-failure-not-policy`
(debt-ledger **D5/D6**). Update when a finding is fixed or a question is decided.

## At a glance

| Review | Findings delivered? | Code/docs follow-ups | Ready to archive? |
|--------|---------------------|----------------------|-------------------|
| `debt-ledger/` | yes (2026-07-20; refreshed 2026-07-24) | **S2/S3/T1 paid** (#184); **Q1 implemented** (#171); open: D1/D2/D3/T2/T3/D4/T7 + DD4/rapidgzip + **N1 pyppmd-UAF** + **N3 archive-sweep**; Q2/Q5 open | no |
| `performance/` | yes (#134 + follow-ups) | P3–P5 done; listing L0–L3 + peers (#143/#146/#148); residual band miss; **Q2 decided** (drift gate); **Q4 open** | no |

Archived this pass: `archive/2026-07-19-api-coherence/`,
`archive/2026-07-19-stream-layering/`, `archive/2026-07-20-cli-product/`.
OpenSpec archived: `archive/2026-07-20-stop-on-failure-not-policy/`.

---

## 1. Actionable right now

### From `debt-ledger/` (pay list)

| ID | Action |
|----|--------|
| **D1** | Re-word VISION/philosophy/costs ≤1.3× claim to peer-ratio bands (after Q1/Q2). |
| **D2** | Write `SECURITY.md` / disclosure process. |
| **D3** | Start `CHANGELOG.md` (Q5 form). |
| ~~T1~~ / **T2** | ~~Solid-RAR mutation net~~ **done (#184)**; still: parametrize seek-interleaving over lzip/`.Z`. |
| **T3** | Benchmark-gate RAR / encrypted / accelerator data cases (perf P6 remainder). |
| **D4 / T7 / N3** | `open-issues.md` sweep; corpus-matrix audit; `openspec archive` unify-pass-driver + gzip-zlib-truncation-recovery. |
| **DD4** | Finish `rapidgzip-truncation-investigation` (now 1/13; characterize → narrow/extend/remove) before 0.2.0 — later PR; see change `design.md`. |
| **N1** | pyppmd native UAF (PPMd `[7z]`): mitigated (#188/#189); file upstream report; revisit CI `--allow-exit-after-green`. |

### From `performance/`

| ID | Action |
|----|--------|
| **P7 residual** | ZIP many-small ~3.7× (above 2–3×); 7z ~2.0–2.2× (above 1.25×). Next: **L3** large RAR listing fixture; **L5** lazy derivation (needs OpenSpec) — or publish honest numbers (debt-ledger Q2). |
| **P6 remainder** | RAR / encrypted / accelerator *data* harness cases still missing (= debt-ledger T3). |
| **VISION/docs** | Re-word ≤1.3× claim to match Q1 now that **Q2** is (a) (= debt-ledger D1). |

---

## 2. Still needs decisions

### `debt-ledger/QUESTIONS.md`

| Q | Finding | Status |
|---|---------|--------|
| **Q1** | Perf wall-budget enforcement (perf Q2) | **decided** — nightly drift vs previous JSON (a); absolute bands informational |
| **Q2** | ZIP listing above band: L5 pre-release vs publish honest number | lean: publish honest number; L5 follow-up |
| **Q3** | S2+S3: entry gate vs pay pre-release | **decided** — (b) pay now; OpenSpec `unify-pass-driver` |
| **Q4** | rapidgzip-truncation rides past 0.2.0? | **decided** — PAY before 0.2.0; implement later |
| **Q5** | CHANGELOG form | lean: committed `CHANGELOG.md` |

### `performance/QUESTIONS.md`

| Q | Finding | Status |
|---|---------|--------|
| **Q2** | **P1** wall-budget enforcement | **decided** (= debt-ledger Q1 (a)) |
| **Q4** | Verify-skip knob (lean leave-as-is; close when archiving) | open |

---

## 3. Future / archive-copy (still live under `performance/`)

| ID | Notes |
|----|-------|
| **P8** | rapidgzip AUTO threshold may be conservative for seek. |
| **P9** | Measurement blind spots (7z password-confirm; RAR solid rewind). |
| Extract residual | Safety floor; realistic ~1.9× already in band. |
| **L5** | Lazy `ArchiveMember` derivation — OpenSpec when commissioned. |
| Topic 6 | Decode-engine performance — `backlog.md` (includes stream Q4). |

---

## Parked when archiving (do not re-open those dirs)

| Item | Where |
|------|-------|
| api-coherence **Q5** (`verify` / `VerifyReport`) | `IDEAS.md` |
| api-coherence digest fill | **Done** #160 — OpenSpec archived `2026-07-19-surface-stored-stream-digests` |
| api-coherence **D1** (ANTI / non-current list marks) | Folded into `cli-product/` → **done** in archive |
| stream-layering **Q4** (`SlicingStream.readinto`) + optional `VerifyingStream` delete | `backlog.md` Topic 6 |
| api-coherence **Q7** | Done #157 — already archived OpenSpec |
| cli-product **P4** `--json` | Wait for `hash` / member schema — `IDEAS.md` / debt-ledger DD7 |
| cli-product **Q4** `--raw` / TTY-only quoting | debt-ledger DD8 (additive; recommended style already applied) |

Full table also in `backlog.md` → "Parked from archived deep reviews".

---

## Already addressed (selected)

| Item | Where |
|------|-------|
| stream-layering F1/F2/D1/D2; Q1–Q3 | #137 |
| api-coherence Q1–Q6 impl | #154 |
| ExtractionStatus rename polish | #156 |
| api-coherence Q7 `members_report` | #157 |
| Stored stream digests (lzip multi + Adler omit) | #160 |
| Listing L0–L3 + peers + 7z byte-cursor | #143/#146/#148 |
| Perf P3–P5, decode-feed, O8 | #136/#139/#141 |
| cli-product P1–P3/P5–P14/D1 | #144 follow-ups + #163/#165 |
| OpenSpec `stop-on-failure-not-policy` | #165 → archived 2026-07-20 |
