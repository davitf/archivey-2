## 1. BLAKE2sp hasher

- [x] 1.1 Confirm RAR5 BLAKE2sp parameters (degree 8, unkeyed, 32-byte output) against a fixture + `unrar`
- [x] 1.2 Implement `internal/hashing/blake2sp.py` on `hashlib.blake2s` tree params (8 leaves round-robin 64-byte blocks, root at depth 1); incremental `update`/`digest`/`digest_size`
- [x] 1.3 KAT tests against reference BLAKE2sp vectors

## 2. Wire into verification

- [x] 2.1 Register `blake2sp` in `verify.py::_make_hasher` returning the internal hasher factory
- [x] 2.2 Confirm `_expected_as_bytes` handles the 32-byte stored value; ensure the digest-size path matches

## 3. Fixtures and tests

- [x] 3.1 RAR5 BLAKE2sp-only fixture (intact) — reads clean, no `DIGEST_UNVERIFIABLE`
- [x] 3.2 Corrupted-payload variant — raises `CorruptionError` at terminal read
- [x] 3.3 `unrar`/`rarfile` oracle cross-check on the intact bytes; skip when unavailable

## 4. Spec + verify

- [x] 4.1 Confirm the `compressed-streams` digest-matrix reconciliation (blake2sp computable; unknown-algorithm example) matches behavior
- [ ] 4.2 Run across `[all]`, `[all-lowest]`, `core-only` (BLAKE2sp path must work with no extras)
- [ ] 4.3 `openspec validate --strict rar-blake2sp-verification`
