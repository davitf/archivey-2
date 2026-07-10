# Tasks — multi-candidate password disambiguation

This change contains the focused ZipCrypto fix. Diagnostics-dependent and future-format
work stays separate and unchecked. Run tools through `uv` (`uv run pytest`,
`uv run pyrefly check`, `uv run ty check`, `uv run ruff`).

## 1. Focused ZIP implementation

- [x] 1.1 Require confirmation for multiple distinct static candidates and provider
      answers while preserving the one-distinct-static-candidate lazy path.
- [x] 1.2 Validate ZipCrypto candidates through decompressor completion and CRC, retaining
      the winning plaintext in bounded-memory/disk-spilling stdlib storage.
- [x] 1.3 Return the rewound validated spool without reopening/decrypting the winning
      member, attempt source closure, and always close owned spools during failure cleanup.
- [x] 1.4 Treat BZIP2's exact `OSError("Invalid data stream")`, DEFLATE/LZMA failures, and
      CRC failures as candidate-validation failures while propagating unrelated
      `OSError` unchanged.
- [x] 1.5 On exhausted ambiguous validation, raise `EncryptionError` stating both possible
      causes (wrong passwords or corrupt encrypted data); never select an unvalidated guess.
- [x] 1.6 Restrict candidate-dependent `BadZipFile` handling to CRC mismatch; preserve
      structural/local-header failures as `CorruptionError`.
- [x] 1.7 Distinguish candidate exhaustion from provider-raised `EncryptionError`, and make
      spool cleanup run even when source closure raises.

## 2. Tests

- [x] 2.1 Cover colliding wrong-before-right candidates for STORED, DEFLATE, BZIP2, and
      LZMA members.
- [x] 2.2 Cover all-wrong collisions and corrupt encrypted data under the explicit
      wrong-password-or-corruption exhaustion contract.
- [x] 2.3 Cover static candidates, provider retries, duplicate values, known-good reuse,
      and one-distinct-candidate no-eager-read behavior.
- [x] 2.4 Verify the winner is opened/decompressed once and unrelated `OSError` propagates
      after the failed stream is closed.
- [x] 2.5 Cover structural `BadZipFile`, provider callback failure, disk rollover with
      partial caller reads, and spool cleanup after source-close failure.

## 3. Specs and design

- [x] 3.1 Document bounded-RAM spooling, proportional temporary-disk/time cost, provider
      laziness, specific exception mapping, and the irreducible classification ambiguity.
- [x] 3.2 Remove lone-survivor, heuristic guessing, finite-provider enumeration, and
      unsupported 7z/RAR authentication claims from this change.

## 4. Verification

- [ ] 4.1 Run focused tests, full tests in all three dependency configurations, Ruff,
      Pyrefly, ty, and strict OpenSpec validation.

## 5. Deferred changes (not part of this implementation)

- [ ] 5.1 Design structured password-disambiguation diagnostics only after a concrete
      warnings-as-data API exists; diagnostics must not authorize guesses.
- [ ] 5.2 Revisit cross-format candidate confirmation when native 7z/RAR readers exist and
      their actual password/integrity signals can be tested.
