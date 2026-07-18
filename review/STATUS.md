# In-flight review status (2026-07-18)

Triage of the four top-level reviews after cross-checking findings against
merged PRs (#120, #133–#141). Update this file when a finding is fixed or a
question is decided; archive a review directory only when every actionable
item is fixed or consciously deferred here / in `backlog.md`.

## At a glance

| Review | Findings delivered? | Code/docs follow-ups | Ready to archive? |
|--------|---------------------|----------------------|-------------------|
| `stream-layering/` | yes (#137) | **done** (F1/F2/D1/D2); Q4 parked → future | **almost** — park Q4 then archive |
| `performance/` | yes (#134 + #139/#140/#143/#146) | partial (P3–P5 done; P7 listing L0–L2 done, L3 partial; P1/P2/P6 open) | no |
| `api-coherence/` | yes (#133) | **Q1–Q6 decided + implemented**; digest fill → `surface-stored-stream-digests`; Q5 deferred; **Q7 → `partial-members-and-errors`** | **almost** — park digest fill / Q5 then archive |
| `cli-product/` | **no** — brief only | review not run | no |

---

## 1. Actionable right now

Work that does not need a new maintainer decision (direction already recorded,
or the finding is a clear proposed fix with no spec conflict).

### From `performance/` (Q1 direction recorded 2026-07-18 in #140)

| ID | Action |
|----|--------|
| **P7 / H3** | **partial** — #143 model-build + **#146** L1 (7z bulk UTF-16 names) / L2 (`ArchiveMember` slots + trimmed kwargs) / L3 volume fast-reject. ZIP many-small ~3.7×; 7z open+list probe ~2.0× (was ~3.4×). Still above Q1 bands; L3 large RAR fixture + L4 deferred; L5 needs OpenSpec. |
| **P6 remainder** | **partial** — `py7zr` / `rarfile` / TAR `open_list` peers + Q1 band labels in harness. RAR/encrypted/accel *data* cases still missing. |
| **P2 remainder** | Many-small `read_all` follows the listing story (same per-member machinery as P7). Large-member ZIP read already ≤1.25× after #139; realistic extract ~1.9× (inside ~2× band) — no further extract code pending Q2. |
| **VISION/docs** | Re-word the ≤1.3× claim to match Q1 (decompression-dominated ≤1.3×; listing as peer ratios) once enforcement (Q2) is chosen. |

### From `api-coherence/` (Q1–Q6 decided 2026-07-18 — implemented)

| ID | Action |
|----|--------|
| **P1 / Q1** | **implemented** — `_apply_last_entry_wins_is_current` in `base_reader.py`; ZIP/TAR/all RA formats now set `is_current`; `archive-data-model` spec updated; corpus sweep uses default ERROR policy. |
| **Q2** | **implemented** — `docs/usage.md` duplicate-name section + filter recipe. |
| **P2 / Q3** | **implemented** — `cost.py` docstrings fixed; RAR open-walk note in `docs/costs.md`; RAR row in `test_cost_receipt.py`. |
| **S1 / S3 / Q4** | **implemented** — 13 `*Context` classes + `RAPIDGZIP_AUTO_MIN_COMPRESSED_SIZE` + `WriteError` demoted from `__all__`; `PasswordInput` / `OnDiagnostic` / `HashAlgorithm` / `crc32_digest` / `IoStats` / `enable_measurement` added; `source_name` dropped from `core.__all__`; `MemberSelector` used consistently. |
| **S2 / Q6** | **implemented** — `ArchiveFormat.display_name` property; CLI `_format_label` uses it. |
| **E1** | **implemented** — public `archivey.measurement` with `IoStats` + `enable_measurement`; `ArchiveReader.io_stats()` on the ABC; CLI `common.py` uses public API only. |
| **E3 / Q6** | **implemented** — `ExtractionStatus.SUPERSEDED` for non-current skip; `SKIPPED` is overwrite-skip only. |
| **Q6 hashes** | **typing done** (c7b88b5) — `HashAlgorithm` enum + `Mapping[HashAlgorithm, bytes]`; filling digests deferred to OpenSpec `surface-stored-stream-digests`. |
| **Q6 WriteError / `[7z-write]`** | **implemented** — `WriteError` demoted from `__all__`; `[7z-write]` extra removed from pyproject.toml and `recommended-lite`; `py7zr` kept in dev. |

### Process

| Item | Action |
|------|--------|
| **`cli-product/`** | Run the product review (brief is ready; #120 is merged). |
| **`stream-layering/`** | Mark Q4 deferred → archive the directory (see §3). |

---

## 2. Still needs decisions

Do not implement these until the maintainer answers (pause-and-ask).

### `api-coherence/QUESTIONS.md`

| Q | Status |
|---|--------|
| **Q1–Q6** | **Decided** 2026-07-18 — see §1 and `api-coherence/QUESTIONS.md` |
| **Q7** | **Decided** — OpenSpec `partial-members-and-errors` |

### `performance/QUESTIONS.md`

| Q | Finding | Why blocked |
|---|---------|-------------|
| **Q2** | **P1** wall-budget enforcement | Nightly drift gate vs 2× band vs informational — flake vs honesty. Recommendation: (a)+(c). |
| **Q4** | Verify-skip knob | Perf case now ~nil post-#137; still an API-design call (overlaps api-coherence). Leaning: leave as-is. |

### `stream-layering/QUESTIONS.md`

| Q | Status |
|---|--------|
| Q1–Q3 | **Decided / implemented** in #137 |
| **Q4** | Open only as “park vs do now” — see §3 (recommend park) |

---

## 3. Future / archive-copy targets

When archiving a review, copy these into `backlog.md`, `IDEAS.md`, or a
dedicated follow-up brief — they are not 0.2.0 blockers from the current round.

### From `performance/` (follow-ups)

| ID | Notes |
|----|-------|
| **P8** | rapidgzip AUTO threshold (1 MiB) may be conservative for seek workloads. |
| **P9** | Measurement blind spots (7z password-confirm decode; RAR solid rewind via unrar pipe). |
| Extract residual | Documented safety floor (`mkstemp`+rename / `lstat`); realistic ~1.9× already in band. |
| Topic 6 | Decode-engine performance (`DecompressorStream` / accelerators) — already in `backlog.md`. |

### From `stream-layering/`

| ID | Notes |
|----|-------|
| **Q4** | Real `SlicingStream.readinto` — park until an extract path is shown `readinto`-bound. Optional cleanup: delete unused `VerifyingStream` later. |

### From `api-coherence/`

| ID | Notes |
|----|-------|
| **D1** | CLI list marks for `ANTI` / non-current — belongs to **`cli-product/`** when that review runs. |
| **E2 / Q5** | Library `verify` / `VerifyReport` — deferred past 0.2.0; uncertain whether callers verify without extracting often enough. Park in `IDEAS.md`. |
| **Q7** | Partial members + honest error — **OpenSpec `partial-members-and-errors`** |
| **Stored stream digests** | zlib Adler-32 + lzip multi-member combine — OpenSpec **`surface-stored-stream-digests`** (after hashes typing). |

### Already on `backlog.md` (not from this round’s findings)

Topics 4–5 (test strategy + debt ledger), Topic 6 (decode-engine perf), Topic 7
(outside-in adoption capstone), plus salvage/best-effort mode as a feature gap.

---

## Already addressed (do not re-open)

Cross-check against merged PRs; statuses should also appear in each review’s
`SUMMARY.md` / `QUESTIONS.md`.

| Review | Item | Where |
|--------|------|-------|
| stream-layering | F1, F2, D1, D2; Q1–Q3 | #137 (+ #138 tidy) |
| performance | P3 (selective solid) | #136 |
| performance | P4, P5 (gate holes); decode-feed; Q3, Q6 | #139 |
| performance | Q5 (H1 shape) | #136 |
| performance | Q1 *direction* (listing = peer ratios) | #140 (implementation still open — §1) |
| performance | O8 side-finding (empty wrong-password 7z) | #141 (threat-model mitigated) |
| api-coherence | *(none)* | findings-only #133 |
| cli-product | *(n/a)* | brief only |
| archive/ | all five archived reviews | see `README.md` |
