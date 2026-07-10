# Design — multi-candidate password disambiguation

## The problem, precisely

A cipher's *per-open* password check and its *authoritative* integrity check can differ in
strength. For ZipCrypto they differ enormously:

- **Per-open check**: a single verification byte (the high byte of the CRC, or of the DOS
  mod-time when a data descriptor is used). `zipfile.ZipFile.open(info, pwd=…)` raises
  `RuntimeError("Bad password")` when it mismatches. ~1/256 of wrong passwords pass it.
- **Authoritative check**: the CRC-32 over the decompressed data (and, for a compressed
  member, the decompressor itself rejecting a corrupt stream). `zipfile` performs this only
  as the member is *read*, at EOF — outside the candidate trial.

Accepting a candidate on the per-open check alone therefore lets a wrong candidate win
~1/256 of the time; the correct candidate is never tried and the CRC failure resurfaces
later as a spurious `CorruptionError`.

## Decision: validate sequentially and spool the winner

For one ZipCrypto member:

1. `_PasswordCandidates` supplies distinct values in its existing order: known-good,
   remaining static candidates, then provider answers.
2. A candidate failing `ZipFile.open()`'s verification byte is rejected cheaply.
3. When confirmation is required, a candidate passing that byte is read to EOF. Each
   plaintext chunk is written to `SpooledTemporaryFile(max_size=8 MiB)`. EOF forces both
   decompressor completion and `zipfile`'s CRC check.
4. On validation failure, source closure is attempted and spool closure runs in a nested
   `finally`, including when source closure itself raises. On success, the source stream
   is closed, the spool rewinds, and that same spool is returned to the caller. If source
   closure raises, the spool remains owned by cleanup and is closed. The winning candidate
   is decrypted exactly once.
5. `_PasswordCandidates.attempt()` records only the password whose callback returned the
   validated spool, so known-good reuse preserves its existing semantics.

The spool bounds memory, not total work or storage. A large member can consume temporary
disk proportional to its uncompressed size, and each colliding wrong candidate may do the
same until its failure is observed. This cost is explicit because CRC-32 is available only
at EOF: a first-block probe or size cap cannot prove a candidate correct, and exposing
bytes before validation would reintroduce silent wrong-password output.

## When confirmation is required

- Two or more distinct values across known-good and static candidates require
  confirmation.
- Duplicate static values count once, so `[password, password]` retains the
  single-candidate lazy path.
- A provider requires confirmation even before it has returned two values. Providers are
  lazy and potentially unbounded; the reader cannot enumerate one to prove that no retry
  exists. If an answer fails validation, the provider receives the next attempt.
- `_PasswordCandidates.attempt()` marks only normal candidate exhaustion with an internal
  `EncryptionError` subtype. An `EncryptionError` raised by the provider callback itself
  bypasses that marker and propagates unchanged, even after an earlier candidate failed
  validation.
- A single distinct static candidate retains normal streaming. Any decompressor/CRC
  failure then follows the ordinary ZIP translator and surfaces as `CorruptionError`.

## Specific validation failures

DEFLATE raises `zlib.error`, LZMA raises `lzma.LZMAError`, CRC mismatch raises
`zipfile.BadZipFile("Bad CRC-32 for file ...")`, and stdlib BZIP2 raises the unusual
`OSError("Invalid data stream")`. Only that CRC message is candidate-dependent:
local-header mismatch, bad local-header magic, overlap, and other structural
`BadZipFile` failures are independent of decrypted bytes and remain `CorruptionError`.
Only the exact BZIP2 message is decoder-owned. Arbitrary `OSError` remains an I/O/runtime
failure and propagates unchanged. Unsupported methods and unexpected exceptions also
retain their existing specific translation.

## Honest exhaustion semantics

After the weak byte check, these two inputs are observationally equivalent:

- a wrong colliding password decrypting valid encrypted bytes; and
- the correct password decrypting corrupt encrypted bytes.

Both can fail in the decompressor or at CRC. If at least one candidate reached this
ambiguous validation failure and no candidate succeeds, the reader raises
`EncryptionError` whose message says that the passwords may be wrong **or** the encrypted
member may be corrupt. `EncryptionError` is the smallest coherent existing public
contract for candidate-search exhaustion; adding a subtype that claims either cause would
be false precision. If no candidate passes the weak check, the normal wrong-password
`EncryptionError` remains unchanged.

The reader never resolves ambiguity through candidate order, neighbouring members,
content plausibility, or a warning-backed guess.

## Deferred diagnostics-dependent work

This change is ZIP-specific. It makes no authentication claim about future native 7z or
RAR implementations; their proposals already describe different format signals and must
be evaluated when code exists. A future structured diagnostic could expose that
disambiguation occurred, but no warning API is invented here and it would not make
unvalidated guessing safe. Future performance work must preserve full confirmation or
introduce an explicit caller policy with an equally explicit inability-to-confirm result.
