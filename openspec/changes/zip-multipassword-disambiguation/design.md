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

## Decision: bounded confirmation, then a fresh caller stream

An earlier revision of this change read the whole member during confirmation and spooled
the winning plaintext (bounded RAM, disk spillover). That was rejected: it silently
consumes temporary disk proportional to the member size, which violates the
`archive-reading` "Bounded implicit temporary storage" guarantee this change introduces,
and it turns `open()` into an O(member) operation even when the caller reads a header.

The key observation enabling bounded confirmation: **rejecting a wrong candidate does not
require proving the winner correct.** Confirmation is a *rejection filter*; the
authoritative CRC still runs on the caller's own stream. `zipfile` re-verifies the CRC-32
at EOF on the fresh stream returned to the caller, so an accepted-but-wrong candidate (or
the correct password over corrupt data) fails the caller's read as `CorruptionError` —
exactly the failure mode the single-candidate path has today. Bounded confirmation never
converts a guess into a silent success.

Per ZipCrypto member needing disambiguation:

1. `_PasswordCandidates` supplies distinct values in its existing order: known-good,
   remaining static candidates, then provider answers.
2. A candidate failing `ZipFile.open()`'s verification byte is rejected cheaply (~255/256
   of wrong passwords).
3. **Compressed member (DEFLATE/BZIP2/LZMA):** decompress up to a bounded plaintext
   prefix (constant, on the order of 1 MiB of decompressed output), discarding output. A
   wrong key hands the decompressor high-entropy garbage:
   - DEFLATE: random input hits an invalid block type, invalid code lengths, or a
     stored-block LEN/NLEN complement mismatch within a handful of bytes on average;
   - BZIP2: the `BZh` stream magic and block magic fail immediately;
   - LZMA (raw, in-ZIP framing): the properties bytes / range coder reject early.
   The gibberish-rejection investigation task measures these margins empirically and
   pins them with regression tests, so the ~1 MiB bound is evidence-backed rather than
   folklore. If the member's decompressed size is within the bound, the prefix read
   reaches EOF and `zipfile` checks the CRC — confirmation is then exact, and the
   ambiguity message below is fully accurate.
4. **STORED member:** there is no decompressor to reject garbage; only the full-stream
   CRC discriminates. Reading the member once per candidate would cost up to N full
   passes. Instead, one shared pass over the raw ciphertext decrypts with every surviving
   candidate in parallel — ZipCrypto keystreams are byte-cheap — accumulating each
   candidate's plaintext CRC-32 in constant memory. At EOF, the candidate matching the
   central-directory CRC wins (ties — astronomically unlikely cross-keystream CRC
   collisions — resolve to the earliest candidate in order). Total cost: one extra full
   read, regardless of candidate count. This needs raw ciphertext access (a byte-range
   read of the member's data area via the local header) and a ZipCrypto keystream; the
   keystream is ~20 lines (see `tests/zipcrypto.py`) and is implemented in archivey
   rather than reaching into `zipfile`'s private `_ZipDecrypter`.
5. **Acceptance:** re-open the member fresh through `zipfile` with the winning password
   (ZIP sources are always seekable) and return that stream; record the password as
   known-good. No plaintext is retained from confirmation. The winner costs at most one
   bounded prefix (or one shared CRC pass) plus the caller's own read.
6. `_PasswordCandidates.attempt()` records only the password whose callback returned the
   confirmed stream, so known-good reuse preserves its existing semantics.

Residual inexactness is confined to members larger than the bound: a wrong candidate
whose garbage decompresses cleanly for the full prefix (no realistic probability for the
stdlib codecs), or a correct candidate over data corrupt only beyond the prefix. Both
surface on the caller's read as `CorruptionError`, matching single-candidate behavior.

## When confirmation is required

- Two or more distinct values across known-good and static candidates require
  confirmation.
- Duplicate static values count once, so `[password, password]` retains the
  single-candidate lazy path.
- A provider requires confirmation even before it has returned two values. Providers are
  lazy and potentially unbounded; the reader cannot enumerate one to prove that no retry
  exists. If an answer fails confirmation, the provider receives the next attempt.
- `_PasswordCandidates.attempt()` marks only normal candidate exhaustion with an internal
  `EncryptionError` subtype. An `EncryptionError` raised by the provider callback itself
  bypasses that marker and propagates unchanged, even after an earlier candidate failed
  confirmation.
- A single distinct static candidate retains normal streaming. Any decompressor/CRC
  failure then follows the ordinary ZIP translator and surfaces as `CorruptionError`.

## Specific confirmation failures

DEFLATE raises `zlib.error`, LZMA raises `lzma.LZMAError`, CRC mismatch raises
`zipfile.BadZipFile("Bad CRC-32 for file ...")`, and stdlib BZIP2 raises the unusual
`OSError("Invalid data stream")`. Only that CRC message is candidate-dependent:
local-header mismatch, bad local-header magic, overlap, and other structural
`BadZipFile` failures are independent of decrypted bytes and remain `CorruptionError`.
Only the exact BZIP2 message is decoder-owned. Arbitrary `OSError` remains an I/O/runtime
failure and propagates unchanged. Unsupported methods and unexpected exceptions also
retain their existing specific translation. Streams opened for a rejected candidate are
closed before the next candidate is tried.

## Honest exhaustion semantics

After the weak byte check, these two inputs are observationally equivalent:

- a wrong colliding password decrypting valid encrypted bytes; and
- the correct password decrypting corrupt encrypted bytes.

Both can fail in the decompressor or at CRC. If at least one candidate reached this
ambiguous confirmation failure and no candidate succeeds, the reader raises
`EncryptionError` whose message says that the passwords may be wrong **or** the encrypted
member may be corrupt. `EncryptionError` is the smallest coherent existing public
contract for candidate-search exhaustion; adding a subtype that claims either cause would
be false precision. If no candidate passes the weak check, the normal wrong-password
`EncryptionError` remains unchanged.

The reader never resolves ambiguity through candidate order, neighbouring members,
content plausibility, or a warning-backed guess. (The order tie-break in the STORED
single-pass applies only among candidates whose full-stream CRC *matched* — those are
confirmations, not guesses.)

## The bounded-storage guarantee

This change adds the general `archive-reading` requirement that reader operations must
not consume memory or temporary storage proportional to member/archive size as an
implicit side effect. It exists because the spooling revision demonstrated how easily
that behavior slips in as an implementation convenience. Format-level materialization
strategies that inherently need proportional storage (e.g. `format-rar`'s documented
`unrar x` serving strategy) remain permitted because they are declared in the format's
own spec — the guarantee targets *silent* consumption, not documented strategies.

## Deferred diagnostics-dependent work

This change is ZIP-specific. It makes no authentication claim about future native 7z or
RAR implementations; their proposals already describe different format signals and must
be evaluated when code exists. A future structured diagnostic (`diagnostics-warnings-as-data`
defines the mechanism; a follow-up defines a password-disambiguation code) could expose
that disambiguation occurred, but no warning API is invented here and it would not make
unvalidated guessing safe.
