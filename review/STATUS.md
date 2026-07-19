# In-flight review status (2026-07-19)

Triage after archiving `api-coherence/` and `stream-layering/` (parked leftovers
recorded) and archiving completed OpenSpec changes
`clarify-extraction-status-names` + `surface-stored-stream-digests`. Update when
a finding is fixed or a question is decided.

## At a glance

| Review | Findings delivered? | Code/docs follow-ups | Ready to archive? |
|--------|---------------------|----------------------|-------------------|
| `performance/` | yes (#134 + follow-ups) | P3–P5 done; listing L0–L3 + peers (#143/#146/#148); residual band miss; **Q2/Q4 open** | no |
| `cli-product/` | yes (#144) | **P1–P3/P5–P14/D1 done** (Q1–Q3/Q5–Q6 decided; P4 deferred) | yes (after merge) |

Archived this pass: `archive/2026-07-19-api-coherence/`,
`archive/2026-07-19-stream-layering/`.

---

## 1. Actionable right now

### From `cli-product/` (findings in #144)

| ID | Action | Status |
|----|--------|--------|
| **P5** | Ctrl-D at password prompt → catch `EOFError`. | **done** |
| **P3** | Escape control bytes in member names (backslash style; Q4 style still open for `--raw`/TTY-only). | **done** (recommended style) |
| **P6 (message)** | Prose instead of raw errno for missing file / bad path. | **done** |
| **P7 / P9** | Library/CLI message cleanup (truncated-zip prose, enum leaks, zipcrypto no-password; rapidgzip AUTO-gate warning text). | **done** |
| **P10–P13** | Help examples; hoist micro-copy; argparse `patterns` wording; reserved-flag asymmetry. | **done** |
| **D1** | List marks for `ANTI` / non-current (from archived api-coherence). | **done** |
| **P1** | Extract CONTINUE + `--stop-on-error` + exit 3 (Q1). | **done** |
| **P2** | No-match include warnings + extract/test exit 1 + `-d` hint (Q3). | **done** |
| **P4** | `--json` for scripting audience. | **deferred** (Q2) — wait for `hash` / member schema |
| **P14** | `info` access/cost line + `--version -v` capability matrix (Q5/Q6). | **done** |
| **P8** | `test` summary reports `K not tested` when an indexed stream aborts early. | **done** |

cli-product code/docs follow-ups from #144 are complete for this pass (**P4**
deferred by Q2).

### From `performance/`

| ID | Action |
|----|--------|
| **P7 residual** | ZIP many-small ~3.7× (above 2–3×); 7z ~2.0–2.2× (above 1.25×). Next: **L3** large RAR listing fixture; **L5** lazy derivation (needs OpenSpec). |
| **P6 remainder** | RAR / encrypted / accelerator *data* harness cases still missing. |
| **VISION/docs** | Re-word ≤1.3× claim to match Q1 once **Q2** is chosen. |

---

## 2. Still needs decisions

### `cli-product/QUESTIONS.md`

| Q | Finding | Status |
|---|---------|--------|
| **Q1** | **P1** extract abort-on-first-error | **decided** — CONTINUE (+ `--stop-on-error`); exit 3 for policy-only blocks |
| **Q2** | **P4** `--json` timing | **decided** — wait for `hash` / member schema (no minimal JSON in 0.2.0) |
| **Q3** | **P2** no-match filters exit code | **decided** — warn; extract/test exit 1; list exit 0; `-d` hint |
| **Q4** | **P3** control-byte quoting style | lean applied (escape everywhere / backslash); `--raw`/TTY-only still open |
| **Q5 / Q6** | **P14** `info` cost line / install capability view | **decided** — `info` prints `access:` from `CostReceipt`; `--version -v` format matrix |
| **Q7** | P7/P9 library vs CLI ownership | **done** (library) |
| **Q8** | STOP+policy exit `3` vs `1` | **open** — see `cli-product/QUESTIONS.md` Q8 table |

### `performance/QUESTIONS.md`

| Q | Finding |
|---|---------|
| **Q2** | **P1** wall-budget enforcement |
| **Q4** | Verify-skip knob (lean leave-as-is) |

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
| api-coherence **D1** (ANTI / non-current list marks) | Folded into `cli-product/` |
| stream-layering **Q4** (`SlicingStream.readinto`) + optional `VerifyingStream` delete | `backlog.md` Topic 6 |
| api-coherence **Q7** | Done #157 — already archived OpenSpec |

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
