# ZipCrypto multi-password collision disambiguation

## Why

The candidate-password model (`archive-reading` → "Password candidates and provider")
says the reader tries candidates in order and remembers successful passwords. Traditional
ZIP encryption makes success unusually hard to identify: `zipfile.open()` checks only one
verification byte, so roughly 1/256 wrong passwords pass the check.

The decompressor and CRC-32 check run only while the member is read. Accepting the first
candidate whose weak check succeeds can therefore shadow a later correct password and
surface a spurious `CorruptionError`. This was the intermittent failure in the
`encrypted-multi` corpus entry.

There is also an irreducible classification limit. A wrong password that passes the byte
check and a correct password applied to corrupt encrypted data can produce the same
decompressor/CRC failure. The reader must not claim it can always distinguish them.

## What Changes

- When multiple distinct static candidates exist, or a provider may supply retries, the
  ZIP reader fully decodes each candidate that passes the weak byte check and accepts it
  only after EOF/CRC validation.
- The winning plaintext is retained in a stdlib `SpooledTemporaryFile` and returned from
  the same validation pass. RAM use is bounded; large plaintext spills to disk. Validation
  still consumes time and temporary storage proportional to the member size.
- Candidate source closure is attempted and the spool is closed even if source closure
  raises. BZIP2's decoder-specific `OSError("Invalid data stream")`, plus
  DEFLATE/LZMA/CRC failures, reject a candidate; unrelated `OSError` instances propagate
  unchanged. Structural/local-header `BadZipFile` remains `CorruptionError`.
- One distinct static password (including duplicate copies of the same value) keeps the
  existing lazy streaming behavior.
- If candidate validation fails and no candidate succeeds, `EncryptionError` states both
  possible causes: wrong password(s) or corrupt encrypted data. There is no new public
  error category. With one distinct static candidate, ordinary read-time corruption
  remains `CorruptionError`.
- Tests cover STORED, DEFLATE, BZIP2, and LZMA collisions; corrupt encrypted data;
  all-wrong collisions; structural `BadZipFile`; provider callback failure; duplicate
  values; known-good reuse; single-candidate laziness; disk rollover with partial reads;
  source/spool cleanup when closure raises; and one-pass handling of the winner.

Not changing detection, the public `password=` API, or the `PasswordProvider` contract.

## Deferred Work

This focused change does not define a generic “disambiguation ladder,” heuristics,
guess-with-warning behavior, or authentication properties for unimplemented 7z/RAR
readers. Cross-format policy must wait for those readers' actual integrity signals and
for a concrete structured-diagnostics API. Any future optimization must still avoid
returning unvalidated guesses.

## Impact

- Affected specs: `archive-reading` (added requirement), `format-zip` (added requirement).
- Affected code: `internal/password.py`, `internal/backends/zip_reader.py`,
  `tests/zipcrypto.py`, `tests/test_password.py`, and `tests/test_zip_multipassword.py`.
- Runtime dependencies: none; spooling uses only the standard library.
