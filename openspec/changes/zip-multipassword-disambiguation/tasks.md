# Tasks — multi-candidate password disambiguation

This change contains the full ZipCrypto disambiguation ladder. The currently-pushed code
implements an interim spooling approach that the bounded-confirmation tasks below
replace; diagnostics-dependent and future-format work stays separate and unchecked. Run
tools through `uv` (`uv run pytest`, `uv run pyrefly check`, `uv run ty check`,
`uv run ruff`).

## 1. Codec gibberish-rejection investigation

- [x] 1.1 Measure how quickly stdlib DEFLATE, BZIP2, and LZMA reject wrong-key ZipCrypto
      output (bytes consumed until failure, distribution over many wrong keys); record
      the findings in `design.md`.
- [x] 1.2 Pin the findings with regression tests: for each codec, wrong-key confirmation
      fails within the confirmation bound with a wide margin (deterministic via the
      collision finder in `tests/zipcrypto.py`).
- [x] 1.3 Calibrate the STORED compressibility probe: chunk size, fast-compressor choice
      (e.g. zlib level 1), the conservative accept margin, and the minimum member size
      below which the probe is skipped; verify wrong-key chunks never reach the margin.

## 2. Bounded confirmation implementation

- [x] 2.1 Require confirmation for multiple distinct static candidates and provider
      answers while preserving the one-distinct-static-candidate lazy path.
- [x] 2.2 Replace the interim full-read/spool validation with a bounded decompressed
      prefix (internal constant, ~1 MiB) for DEFLATE/BZIP2/LZMA members; members within
      the bound get exact full validation. Remove `SpooledTemporaryFile` usage.
- [x] 2.3 Implement the STORED disambiguation: raw ciphertext byte-range read via the
      local header and a minimal internal ZipCrypto keystream (do not import
      `zipfile._ZipDecrypter`); first the accept-only compressibility probe on the first
      chunk, then — when inconclusive — the shared pass with per-candidate parallel
      CRC-32 accumulation continuing from that chunk, winner by central-directory CRC
      match, ties by candidate order.
- [x] 2.4 Re-open the confirmed candidate fresh through `zipfile` for the caller's
      stream; record it known-good only after confirmation; retain no confirmation
      plaintext.
- [x] 2.5 Treat BZIP2's exact `OSError("Invalid data stream")`, DEFLATE/LZMA failures,
      and CRC failures as candidate-confirmation failures while propagating unrelated
      `OSError` unchanged.
- [x] 2.6 On exhausted ambiguous confirmation, raise `EncryptionError` stating both
      possible causes (wrong passwords or corrupt encrypted data); never select an
      unvalidated guess.
- [x] 2.7 Restrict candidate-dependent `BadZipFile` handling to CRC mismatch; preserve
      structural/local-header failures as `CorruptionError`.
- [x] 2.8 Distinguish candidate exhaustion from provider-raised `EncryptionError`; close
      rejected-candidate streams before trying the next candidate.

## 3. Tests

- [x] 3.1 Cover colliding wrong-before-right candidates for STORED, DEFLATE, BZIP2, and
      LZMA members.
- [x] 3.2 Cover all-wrong collisions and corrupt encrypted data under the explicit
      wrong-password-or-corruption exhaustion contract.
- [x] 3.3 Cover static candidates, provider retries, duplicate values, known-good reuse,
      and one-distinct-candidate no-eager-read behavior.
- [x] 3.4 Verify unrelated `OSError` propagates after the failed stream is closed; cover
      structural `BadZipFile` and provider callback failure.
- [x] 3.5 Verify confirmation of a member larger than the bound is bounded: no temporary
      file is created and at most the bounded prefix is decompressed per candidate.
- [x] 3.6 Verify the STORED path: a compressible-plaintext member accepts from the first
      chunk without a full read; an incompressible-plaintext member falls back to the
      shared CRC pass (one ciphertext read total); the caller's stream is fresh and
      CRC-checked; CRC-match ties resolve by candidate order; the probe never rejects.
- [x] 3.7 Verify a candidate accepted by prefix confirmation whose data is corrupt beyond
      the prefix fails the caller's read as `CorruptionError` (parity with the
      single-candidate path).

## 4. Specs and design

- [x] 4.1 Specify bounded prefix confirmation, the STORED single-pass, fresh re-open for
      the caller, provider laziness, exception mapping, and the irreducible
      classification ambiguity.
- [x] 4.2 Add the general `archive-reading` "Bounded implicit temporary storage"
      requirement with the documented-per-format-strategy carve-out.

## 5. Verification

- [x] 5.1 Run focused tests, full tests in all three dependency configurations, Ruff,
      Pyrefly, ty, and strict OpenSpec validation.

## 6. Deferred changes (not part of this implementation)

- [ ] 6.1 Design structured password-disambiguation diagnostics only after the
      warnings-as-data API exists; diagnostics must not authorize guesses.
- [ ] 6.2 Revisit cross-format candidate confirmation when native 7z/RAR readers exist and
      their actual password/integrity signals can be tested.
