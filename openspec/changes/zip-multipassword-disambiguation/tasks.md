# Tasks — multi-candidate password disambiguation

> Specs-only proposal. These tasks describe the implementation when the change is accepted
> and scheduled; nothing is implemented here. The minimal correctness fix (confirm a ZIP
> candidate by reading before accepting) already landed on this branch (PR #53); the tasks
> below extend it into the full ladder. Run tools through `uv` (`uv run pytest`,
> `uv run pyrefly check`, `uv run ty check`, `uv run ruff`).

## 1. Cross-format contract (`archive-reading`)

- [ ] 1.1 In `_PasswordCandidates.attempt`, split "candidate passed a preliminary check"
      from "candidate confirmed": record known-good / return the stream only after the
      unit's authoritative check has confirmed the password.
- [ ] 1.2 Let a backend declare its per-open check strength, so strong-check backends
      (7z AES, RAR5) skip the ladder and weak-check backends (ZipCrypto) engage it.

## 2. ZIP ladder (`format-zip`)

- [ ] 2.1 Per-open filter: keep only candidates passing ZipCrypto's verification byte
      (`open()`); **if exactly one survives, accept it without decoding** (removes the
      extra full read the PR #53 fix does in the ordinary two-password case).
- [ ] 2.2 Cheap decode probe: for a compressed member, decode a first block under each
      surviving candidate and drop decompressor failures.
- [ ] 2.3 Size-gated full decode + CRC (budget ≤ 16 MiB, config-overridable); drop CRC
      failures. Above the budget, do not full-read every candidate.
- [ ] 2.4 Residual heuristics: neighbour-member password affinity; optional content/MIME
      plausibility (opt-in, lowest priority).
- [ ] 2.5 Unresolved residual: fail-fast on a genuine full-CRC collision; guess-with-record
      only when a large member exceeded the decode budget.
- [ ] 2.6 Preserve: single-candidate fast path unchanged; a genuinely corrupt archive still
      reported as `CorruptionError`, not an encryption problem.

## 3. Surfacing the outcome (depends on C2 warnings-as-data)

- [ ] 3.1 When the reader disambiguated among multiple candidates or guessed, record a
      structured occurrence/warning (via the C2 mechanism when it lands; `logging` interim).

## 4. Tests

- [ ] 4.1 Extend `tests/test_zip_multipassword.py`: lone-survivor fast path does no full
      read; compressed vs stored disambiguation; size-budget boundary; neighbour-affinity
      resolution; genuine-collision fail-fast; corrupt-archive-still-corruption.
- [ ] 4.2 Reuse `tests/zipcrypto.py` (verification-byte collision finder) for the
      false-accept fixtures. Green in all three dependency configurations.
