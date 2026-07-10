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
  ZIP reader confirms each candidate that passes the weak byte check with **bounded**
  work before accepting it:
  - **Compressed members** (DEFLATE/BZIP2/LZMA): decompress a bounded plaintext prefix
    (~1 MiB, internal constant) and discard it — a wrong ZipCrypto key produces garbage
    the codec rejects far within that bound. Members smaller than the bound get exact
    full validation (EOF forces `zipfile`'s CRC check).
  - **STORED members**: one shared pass over the ciphertext computes every surviving
    candidate's plaintext CRC-32 in parallel (constant memory); the candidate matching
    the central-directory CRC wins. One extra full read total, not one per candidate.
- The confirmed candidate's member is **re-opened fresh** for the caller; no plaintext is
  retained from confirmation. The caller's stream keeps `zipfile`'s ordinary EOF CRC
  check, so bounded confirmation never weakens the read-time contract relative to the
  single-candidate path.
- Add a general `archive-reading` requirement — **Bounded implicit temporary storage**:
  reader operations must not consume memory or temporary disk proportional to member or
  archive size as an implicit side effect. (An earlier revision of this change spooled
  the winning plaintext to a `SpooledTemporaryFile`; this requirement is why that
  approach was rejected.) Per-format materialization strategies declared in a format
  spec (e.g. `format-rar`'s `unrar x` strategy) remain permitted.
- Investigate and document each stdlib codec's rejection behavior on wrong-key garbage
  (how many bytes until DEFLATE/BZIP2/LZMA reject), recording measured margins in the
  design and pinning them with regression tests.
- Candidate-rejection exception scoping is unchanged: only the CRC-mismatch `BadZipFile`
  message, `zlib.error`, `lzma.LZMAError`, and BZIP2's exact
  `OSError("Invalid data stream")` reject a candidate; unrelated `OSError` propagates;
  structural `BadZipFile` remains `CorruptionError`.
- One distinct static password (including duplicate copies of the same value) keeps the
  existing lazy streaming behavior.
- If candidate confirmation fails and no candidate succeeds, `EncryptionError` states
  both possible causes: wrong password(s) or corrupt encrypted data. There is no new
  public error category. With one distinct static candidate, ordinary read-time
  corruption remains `CorruptionError`.
- Tests cover STORED, DEFLATE, BZIP2, and LZMA collisions; corrupt encrypted data;
  all-wrong collisions; structural `BadZipFile`; provider callback failure; duplicate
  values; known-good reuse; single-candidate laziness; bounded confirmation on large
  members; and the STORED single-pass disambiguation.

Not changing detection, the public `password=` API, or the `PasswordProvider` contract.

## Deferred Work

This focused change does not define guess-with-warning behavior, content heuristics, or
authentication properties for unimplemented 7z/RAR readers. Cross-format policy must wait
for those readers' actual integrity signals and for the structured-diagnostics API
(`diagnostics-warnings-as-data`). Any future optimization must still avoid returning
unvalidated guesses.

## Impact

- Affected specs: `archive-reading` (two added requirements: weak-check confirmation and
  bounded implicit temporary storage), `format-zip` (added requirement).
- Affected code: `internal/password.py`, `internal/backends/zip_reader.py`, a minimal
  internal ZipCrypto keystream for the STORED single-pass (independent of `zipfile`'s
  private `_ZipDecrypter`), `tests/zipcrypto.py`, `tests/test_password.py`, and
  `tests/test_zip_multipassword.py`.
- Runtime dependencies: none; everything uses the standard library.
