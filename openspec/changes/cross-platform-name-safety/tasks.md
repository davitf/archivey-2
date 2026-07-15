## 1. Spike: name-safety policy decisions (settled)

- [x] 1.1 Reject vs reversible-escape for unrepresentable names → **sanitize** (percent-escape
      non-UTF-8 bytes as `%XX`, escape literal `%` as `%25`, non-decodable bytes only,
      deterministic on every OS, collision-tracked); recorded in design.md + ADR 0013
- [x] 1.2 `STANDARD`-level cut for trailing dot/space → **allow** (reject reserved + `:`
      only; STRICT covers the crafted `foo.`/`foo` merge); recorded in design.md + ADR 0013

## 2. O2 — collision determinism

- [x] 2.1 Add a `casefold(NFC(path))` → written-path map to `ExtractionCoordinator`
- [x] 2.2 Route casefold/NFC collisions through `OverwritePolicy` under `STRICT`/`STANDARD`
      on all platforms (`TRUSTED` defers to the exact-path/local-OS behavior); record on
      `ExtractionResult` (`requested_path`) and emit an `EXTRACTION_NAME_COLLISION`
      diagnostic (including under `REPLACE`, so the merge is not silent)
- [x] 2.3 Add `OverwritePolicy.RENAME` — ` (N)` before the final suffix (`photo (1).jpg`),
      incrementing to the first name free on disk and in the collision map; lands before the
      CLI phase so `extract` can offer rename-on-collision

## 3. O3/O4 — portable-name enforcement

- [x] 3.1 Reserved-name / trailing-dot-space / `:` checks keyed on `ExtractionPolicy`
      (STRICT rejects all on every platform; STANDARD rejects reserved + `:`, allows
      trailing dot/space; TRUSTED defers)
- [x] 3.2 Typed `UnportableNameError` (a `FilterRejectionError`) for a rejected name;
      integrates with `OnError` (records `REJECTED`)

## 4. O7 — representability (sanitize)

- [x] 4.1 Implement the percent-escape scheme under `STRICT`/`STANDARD` (non-UTF-8 bytes
      only, deterministic on every OS); keep `TRUSTED` faithful-bytes
- [x] 4.2 Feed sanitized spellings through the O2 collision map (task 2.1)

## 5. Public surface

- [x] 5.1 Add `requested_path: Path | None = None` to `ExtractionResult` (appended, frozen,
      backward-compatible); document `requested_path != path` as the rename signal
- [x] 5.2 Add the `EXTRACTION_NAME_COLLISION` diagnostic code + `NameCollisionContext`

## 6. Tests + verify

- [x] 6.1 Cross-platform matrix asserted deterministically on all platforms (not gated on
      runner OS): collisions, reserved, trailing dot/space, `:`, surrogateescape sanitize,
      RENAME, per-level `TRUSTED`-defers behavior
- [x] 6.2 Confirm no overlap/conflict with `adversarial-string-corpus-contract` (bidi/NUL) —
      archived; owns NUL-in-link-target + the EILSEQ typed-error path, both orthogonal and
      preserved (`TRUSTED` still translates the write-time refusal)
- [x] 6.3 `openspec validate --strict cross-platform-name-safety`
