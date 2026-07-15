## 1. Spike: name-safety policy decisions (settled)

- [x] 1.1 Reject vs reversible-escape for unrepresentable names → **sanitize** (percent-escape
      non-UTF-8 bytes as `%XX`, escape literal `%` as `%25`, non-decodable bytes only,
      deterministic on every OS, collision-tracked); recorded in design.md + ADR 0013
- [x] 1.2 `STANDARD`-level cut for trailing dot/space → **allow** (reject reserved + `:`
      only; STRICT covers the crafted `foo.`/`foo` merge); recorded in design.md + ADR 0013

## 2. O2 — collision determinism

- [ ] 2.1 Add a `casefold(NFC(path))` → written-path map to `ExtractionCoordinator`
- [ ] 2.2 Route casefold/NFC collisions through `OverwritePolicy` under `STRICT`/`STANDARD`
      on all platforms (`TRUSTED` defers to the exact-path/local-OS behavior); record on
      `ExtractionResult` (`requested_path`) and emit an `EXTRACTION_NAME_COLLISION`
      diagnostic (including under `REPLACE`, so the merge is not silent)
- [ ] 2.3 Add `OverwritePolicy.RENAME` — ` (N)` before the final suffix (`photo (1).jpg`),
      incrementing to the first name free on disk and in the collision map; lands before the
      CLI phase so `extract` can offer rename-on-collision

## 3. O3/O4 — portable-name enforcement

- [ ] 3.1 Reserved-name / trailing-dot-space / `:` checks keyed on `ExtractionPolicy`
      (STRICT rejects all on every platform; STANDARD rejects reserved + `:`, allows
      trailing dot/space; TRUSTED defers)
- [ ] 3.2 Typed `ExtractionError` for a rejected name; integrate with `OnError`

## 4. O7 — representability (sanitize)

- [ ] 4.1 Implement the percent-escape scheme under `STRICT`/`STANDARD` (non-UTF-8 bytes
      only, deterministic on every OS); keep `TRUSTED` faithful-bytes
- [ ] 4.2 Feed sanitized spellings through the O2 collision map (task 2.1)

## 5. Public surface

- [ ] 5.1 Add `requested_path: Path | None = None` to `ExtractionResult` (appended, frozen,
      backward-compatible); document `requested_path != path` as the rename signal
- [ ] 5.2 Add the `EXTRACTION_NAME_COLLISION` diagnostic code

## 6. Tests + verify

- [ ] 6.1 Cross-platform matrix asserted deterministically on all platforms (not gated on
      runner OS): collisions, reserved, trailing dot/space, `:`, surrogateescape sanitize,
      RENAME, per-level `TRUSTED`-defers behavior
- [ ] 6.2 Confirm no overlap/conflict with `adversarial-string-corpus-contract` (bidi/NUL)
- [ ] 6.3 `openspec validate --strict cross-platform-name-safety`
