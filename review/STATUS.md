# In-flight review status (2026-07-19)

Triage of the four top-level reviews after cross-checking findings against
merged PRs through **#157**. Update this file when a finding is fixed or a
question is decided; archive a review directory only when every actionable
item is fixed or consciously deferred here / in `backlog.md`.

## At a glance

| Review | Findings delivered? | Code/docs follow-ups | Ready to archive? |
|--------|---------------------|----------------------|-------------------|
| `stream-layering/` | yes (#137) | **done** (F1/F2/D1/D2); Q4 parked → future | **almost** — park Q4 then archive |
| `performance/` | yes (#134 + follow-ups) | P3–P5 done; listing L0–L3 + peers (#143/#146/#148); residual band miss + **Q2/Q4 open** | no |
| `api-coherence/` | yes (#133) | **Q1–Q7 decided + implemented** (#153–#157); Q5 deferred; digest *fill* → OpenSpec | **almost** — park Q5 + digest fill then archive |
| `cli-product/` | yes (#144) | **none yet** — P1–P14 + Q1–Q7 all open | no |

---

## 1. Actionable right now

Work that does not need a new maintainer decision (direction already recorded,
or a clear fix with no spec conflict).

### From `cli-product/` (findings in #144 — no code yet)

Small / unambiguous blockers that don't wait on Q1–Q3:

| ID | Action |
|----|--------|
| **P5** | Ctrl-D at password prompt → catch `EOFError`, treat as no password (tiny). |
| **P3** | Escape control bytes in member names on all CLI output (style still Q4, but escaping itself is agreed-shape). |
| **P6 (message)** | Replace raw errno reprs with prose for missing file / bad path; "did you mean a verb?" can wait. |
| **P7 worst cases** | Library/CLI message cleanup: truncated-zip `BadZipFile` prose, enum-name leaks, zipcrypto "Wrong password" with no password (#131 D8 residue). Ownership confirmed in cli Q7. |
| **P9** | Rapidgzip AUTO-gate warning text: distinguish "not installed" vs "not engaged". |
| **P10–P13** | Help epilog examples; hoist/summary micro-copy; argparse `patterns` required wording; reserved-flag asymmetry. |

### From `performance/` (Q1 direction + listing worklist)

| ID | Action |
|----|--------|
| **P7 residual** | ZIP many-small still ~3.7× (above 2–3×); 7z open+list ~2.0–2.2× (above 1.25× native). Next levers: **L3** large RAR listing fixture; **L5** lazy derivation (needs OpenSpec). L4 deferred (no ≥10% lever). |
| **P6 remainder** | RAR / encrypted / accelerator *data* harness cases still missing (listing peers done in #143). |
| **VISION/docs** | Re-word ≤1.3× claim to match Q1 once **Q2** (enforcement) is chosen. |

### Process

| Item | Action |
|------|--------|
| **`stream-layering/`** | Park Q4 → archive the directory. |
| **`api-coherence/`** | Park Q5 (`IDEAS.md` already) + digest fill (`surface-stored-stream-digests`) → archive. |
| **`clarify-extraction-status-names`** | OpenSpec change tasks all `[x]` (#156) — archive the change when convenient. |

---

## 2. Still needs decisions

### `cli-product/QUESTIONS.md` (all open — blocks most of that review)

| Q | Finding | Why blocked |
|---|---------|-------------|
| **Q1** | **P1** extract abort-on-first-error | Continue-on-reject/fail vs STOP; exit 3 vs 1; `--stop-on-error` timing. |
| **Q2** | **P4** `--json` | 0.2.0 vs first 0.2.x vs wait for `hash` verb. |
| **Q3** | **P2** no-match filters | Exit code on zero matches; `(did you mean -d?)` hint. |
| **Q4** | **P3** control-byte quoting style | Backslash vs U+FFFD; TTY-only vs always. |
| **Q5** | **P14** `info` cost/access line | In-scope for CLI now? |
| **Q6** | **P14** install capability view | `--version -v` / `formats` / defer. |
| **Q7** | P7/P9 library message owners | Confirm library vs CLI ownership (recommend fix in library). |

### `performance/QUESTIONS.md`

| Q | Finding | Why blocked |
|---|---------|-------------|
| **Q2** | **P1** wall-budget enforcement | Nightly drift vs 2× band vs informational. Recommendation: (a)+(c). |
| **Q4** | Verify-skip knob | Perf case ~nil post-#137; lean leave-as-is. |

### `stream-layering/QUESTIONS.md`

| Q | Status |
|---|--------|
| Q1–Q3 | **Done** (#137) |
| **Q4** | Park (future) — see §3 |

### `api-coherence/QUESTIONS.md`

| Q | Status |
|---|--------|
| **Q1–Q6** | **Decided + implemented** (#153/#154); E3 rename polish in #156 |
| **Q5** | **Decided: defer** past 0.2.0 — parked in `IDEAS.md` |
| **Q7** | **Decided + implemented** (#157 / archived `partial-members-and-errors`) |

---

## 3. Future / archive-copy targets

When archiving a review, copy these into `backlog.md`, `IDEAS.md`, or a
follow-up brief — not current-round 0.2.0 blockers unless noted.

### From `performance/`

| ID | Notes |
|----|-------|
| **P8** | rapidgzip AUTO threshold (1 MiB) may be conservative for seek. |
| **P9** | Measurement blind spots (7z password-confirm; RAR solid rewind via unrar pipe). |
| Extract residual | Safety floor (`mkstemp`+rename); realistic ~1.9× already in band. |
| **L5** | Lazy `ArchiveMember` derivation — OpenSpec; only path to ~1× many-small ZIP list. |
| Topic 6 | Decode-engine performance — `backlog.md`. |

### From `stream-layering/`

| ID | Notes |
|----|-------|
| **Q4** | Real `SlicingStream.readinto` — park until extract is shown `readinto`-bound. Optional: delete thin `VerifyingStream` later. |

### From `api-coherence/`

| ID | Notes |
|----|-------|
| **D1** | CLI list marks for `ANTI` / non-current — fold into **`cli-product/`** (still open there). |
| **E2 / Q5** | Library `verify` / `VerifyReport` — already in `IDEAS.md`. |
| **Stored stream digests** | zlib Adler-32 + lzip multi-member combine — OpenSpec **`surface-stored-stream-digests`** (typing done in #154). |

### From `cli-product/` (polish / later)

| ID | Notes |
|----|-------|
| **P4** | `--json` if Q2 chooses post-0.2.0. |
| **P8** | `test` archive-wide failure counts honesty. |
| **P14** | capability / cost views if Q5/Q6 defer. |

### Already on `backlog.md`

Topics 4–5 (test strategy + debt ledger), Topic 6 (decode-engine), Topic 7
(outside-in adoption), salvage/best-effort mode. (Q7 partial-members entry is
**done** — remove/strike when editing backlog.)

---

## Already addressed (do not re-open)

| Review | Item | Where |
|--------|------|-------|
| stream-layering | F1, F2, D1, D2; Q1–Q3 | #137 (+ #138) |
| performance | P3 selective solid | #136 |
| performance | P4, P5 gate holes; decode-feed; Q3, Q6 | #139 |
| performance | Q5 H1 shape | #136 |
| performance | Q1 direction (listing = peer ratios) | #140 |
| performance | O8 empty wrong-password 7z | #141 |
| performance | Listing peers + ZIP/TAR model-build (L0) | #143 |
| performance | Listing L1/L2/L3 (bulk 7z names, slots, volume skip) | #146 |
| performance | 7z byte-cursor header parse | #148 |
| api-coherence | Q1–Q6 decisions recorded | #153 |
| api-coherence | Q1–Q6 implementation (P1, P2, S1–S3, E1, E3, hashes typing, WriteError/`[7z-write]`) | #154 |
| api-coherence | E3 rename polish `SKIPPED`→`NOT_OVERWRITTEN`, rejected→`BLOCKED` | #156 |
| api-coherence | Q7 `members_report` / partial listing | #157 |
| cli-product | Findings delivered (P1–P14, Q1–Q7) | #144 |
| archive/ | five earlier reviews | see `README.md` |
