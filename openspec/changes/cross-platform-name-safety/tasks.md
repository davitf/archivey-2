## 1. Spike: settle the O7 normalization scheme

- [ ] 1.1 Exploration pass: reject vs reversible-escape for unrepresentable names; choose the escape scheme (which bytes, reversibility, Windows-mangling interaction, O2 collision-map interaction); record the decision in design.md
- [ ] 1.2 Confirm the `STANDARD`-level cut for trailing dot/space (allow vs reject)

## 2. O2 — collision determinism (independent of the spike)

- [ ] 2.1 Add a `casefold(NFC(path))` → written-path map to `ExtractionCoordinator`
- [ ] 2.2 Route casefold/NFC collisions through `OverwritePolicy` on all platforms; record on `ExtractionResult`
- [ ] 2.3 Add `OverwritePolicy.RENAME` (`name (1)`) using the collision map; lands before the CLI phase so `extract` can offer rename-on-collision

## 3. O3/O4 — portable-name enforcement

- [ ] 3.1 Reserved-name / trailing-dot-space / `:` checks keyed on `ExtractionPolicy` (STRICT rejects on all platforms; STANDARD subset; TRUSTED defers)
- [ ] 3.2 Typed `ExtractionError` for a rejected name; integrate with `OnError`

## 4. O7 — representability (after 1.1)

- [ ] 4.1 Implement the chosen scheme (reject or reversible-escape) under STRICT; keep TRUSTED faithful-bytes
- [ ] 4.2 Ensure collision-tracking covers sanitized spellings

## 5. Tests + verify

- [ ] 5.1 Cross-platform matrix asserted deterministically on all platforms (not gated on runner OS): collisions, reserved, trailing dot/space, `:`, surrogateescape
- [ ] 5.2 Confirm no overlap/conflict with `adversarial-string-corpus-contract` (bidi/NUL)
- [ ] 5.3 `openspec validate --strict cross-platform-name-safety`
