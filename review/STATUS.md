# In-flight review status (2026-07-24)

Triage after **#191** (D1/Q2) and ledger refresh against `main` @ `bdf5ffd`.

## At a glance

| Review | Findings delivered? | Code/docs follow-ups | Ready to archive? |
|--------|---------------------|----------------------|-------------------|
| `debt-ledger/` | yes (2026-07-20; **refreshed 2026-07-24**) | **DONE:** D1 #191, Q1/DD1 #171, S2/S3+T1 #184, D5/D6 #170. **Open:** D2, D3 (#176), DD4 (#177), T2/T3/T7, D4, D7, T4 half-test. **Q5** open | no |
| `performance/` | yes (#134 + follow-ups) | P3–P5 done; listing L0–L3 + peers; residual **accepted aspirational** (#191); wall Q2 decided (#171); **Q4 open** | no |

Archived earlier: api-coherence, stream-layering, cli-product;
OpenSpec `stop-on-failure-not-policy`.
Pending archive (**D7**): `unify-pass-driver`, `gzip-zlib-truncation-recovery`.

---

## 1. Actionable right now (recommended order)

### From `debt-ledger/`

| ID | Action | Notes |
|----|--------|-------|
| **D2** | Write `SECURITY.md` | No PR yet |
| **D3 / Q5** | Start `CHANGELOG.md` | Open **#176** (rebase) |
| **DD4** | Finish rapidgzip truncation investigation | Open **#177** (rebase); #183 is stdlib-only |
| **T2** | Seek-interleaving for lzip/`.Z` | Cheap |
| **D4 / D7** | `open-issues.md` sweep; archive completed OpenSpec | Housekeeping |
| **T3** | Bench-gate RAR / encrypted / accelerator data | perf P6 |
| **T7** | Corpus-matrix audit | Half-day |
| **T4 half** | Multithread `members_report_if_available` test | Small |

### From `performance/`

| ID | Action |
|----|--------|
| **P7 residual** | **Accepted** (#191) — nightly ZIP **4.44×** / 7z **2.13×** / RAR **2.39×** in `docs/costs.md`; L5 → `IDEAS.md`. |
| **P6 remainder** | = debt-ledger T3 |

---

## 2. Still needs decisions

| Q | Status |
|---|--------|
| debt-ledger **Q1–Q4** | **decided** (+ landed where applicable) |
| debt-ledger **Q5** | **open** — lean committed CHANGELOG; #176 |
| performance **Q4** | **open** — lean leave-as-is |

---

## 3. Future / archive-copy (`performance/`)

| ID | Notes |
|----|-------|
| **P8** | rapidgzip AUTO threshold may be conservative for seek |
| **P9** | Measurement blind spots |
| Extract residual | Nightly ZIP extract **2.38×** (slightly above ~2×) |
| **L5** | Deferred → `IDEAS.md` |
| Topic 6 | Decode-engine performance — `backlog.md` |

---

## Already addressed (selected)

| Item | Where |
|------|-------|
| Nightly wall-ratio drift (Q1) | #171 |
| Unify pass driver + solid-RAR mutation (S2/S3/T1) | #184 |
| Stdlib gzip zlib truncation recovery | #183 |
| ADR 0014 integrity from reads not close | #186 |
| `pyppmd` quiesce-on-close + valgrind UAF gate | #188/#189 |
| D1 VISION/costs/philosophy peer bands (Q2 (b)) | **#191** |
| OpenSpec `stop-on-failure-not-policy` | #165 → archived |
| Listing L0–L3 + peers; perf P3–P5 | #143/#146/#148/#136/#139 |
| api-coherence / stream-layering / cli-product | #137/#154–#160/#163/#165 |
