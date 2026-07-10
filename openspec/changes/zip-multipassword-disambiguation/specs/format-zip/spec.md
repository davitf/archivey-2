# format-zip — ZipCrypto multi-candidate disambiguation delta

## ADDED Requirements

### Requirement: Multi-candidate disambiguation for traditional ZipCrypto

For a member encrypted with traditional ZipCrypto (PKWARE), whose per-open password check
is a single verification byte while the authoritative check is the member's CRC-32 (and,
for a compressed member, completion of the decompressor), the ZIP reader SHALL confirm a
candidate that passes the byte check before accepting it whenever another candidate may be
tried under `archive-reading` → "Confirm candidates when a weak check permits retries".
Confirmation SHALL be bounded per `archive-reading` → "Bounded implicit temporary
storage": it SHALL NOT buffer plaintext proportional to the member size to memory or
temporary disk.

**Compressed members (DEFLATE / BZIP2 / LZMA).** A candidate is confirmed by decompressing
a bounded plaintext prefix (an internal constant on the order of 1 MiB of decompressed
output), discarding the output. A wrong ZipCrypto key feeds the decompressor
high-entropy garbage, which each stdlib codec rejects far within that bound (the
gibberish-rejection investigation in this change's tasks records the measured margins).
If the member's decompressed size is within the bound, the prefix read reaches EOF and
`zipfile`'s CRC check runs, making confirmation exact.

**STORED members.** No decompressor exists to reject garbage and only the full-stream
CRC-32 discriminates, so per-candidate full reads would cost one pass per candidate.
Instead the reader SHALL disambiguate all surviving candidates (those passing the
verification byte) in a **single pass** over the member's ciphertext: decrypt the stream
once per candidate in parallel while streaming, accumulate each candidate's plaintext
CRC-32 in constant memory, and at EOF accept the candidate whose CRC matches the central
directory value. At most one extra full read of the member is performed in total,
regardless of the number of candidates. If more than one candidate's CRC matches (a CRC
collision across keystreams), the earliest match in candidate order is accepted.

**Returning the winner.** After confirmation the reader SHALL open a fresh stream with the
accepted password for the caller (ZIP requires a seekable source, so re-open is always
available) and SHALL record the password as known-good. The caller's stream retains the
format's ordinary read-time integrity checking — `zipfile` verifies the CRC-32 at EOF —
so acceptance by bounded confirmation never weakens the read-time contract relative to
the single-candidate path: plaintext that is wrong beyond the confirmed prefix still
surfaces as `CorruptionError` during the caller's read.

The candidate-confirmation failure set SHALL include only `zipfile.BadZipFile` whose
message identifies `"Bad CRC-32 for file ..."`, `zlib.error`, `lzma.LZMAError`, and
exactly the BZIP2 decoder's `OSError("Invalid data stream")`. Local-header mismatch, bad
local-header magic, overlap, and other structural `BadZipFile` failures SHALL remain
`CorruptionError` and stop candidate iteration. Other `OSError` values SHALL propagate
unchanged. Streams opened for a rejected candidate SHALL be closed before the next
candidate is tried.

If one or more candidates reach confirmation but no candidate succeeds, the reader SHALL
raise `EncryptionError` explaining that the passwords may be wrong or the encrypted member
may be corrupt. This is intentionally not a promise to distinguish those equivalent
failure observations. The reader SHALL never return a candidate selected by order,
neighbour/content heuristics, or guess-with-warning.

With one distinct static candidate, including duplicate copies of it, the reader SHALL
retain its normal lazy stream with no eager confirmation read. A read-time integrity
failure on that path SHALL be translated as corruption in the ordinary way.

#### Scenario: colliding wrong candidate is rejected for every stdlib ZIP codec

- **WHEN** a STORED, DEFLATE, BZIP2, or LZMA ZipCrypto member is opened with a wrong candidate that passes the verification byte followed by the correct candidate
- **THEN** confirmation rejects the wrong candidate and the reader returns a fresh stream opened with the correct candidate

#### Scenario: single candidate keeps the fast path

- **WHEN** a ZipCrypto member is opened with one distinct static candidate password
- **THEN** the reader does not perform a confirmation read; the member streams as before

#### Scenario: confirming a large compressed member is bounded

- **WHEN** a compressed ZipCrypto member much larger than the confirmation bound is opened with multiple candidates
- **THEN** confirmation decompresses at most the bounded prefix per candidate, retains no plaintext in memory or temporary storage, and the caller receives a fresh stream whose CRC is still verified at EOF by the ordinary read path

#### Scenario: stored members are disambiguated in one extra pass

- **WHEN** a STORED ZipCrypto member is opened with several candidates that pass the verification byte
- **THEN** one pass over the ciphertext computes every candidate's plaintext CRC-32 concurrently, the matching candidate is accepted and re-opened fresh for the caller, and no candidate's plaintext is buffered

#### Scenario: multiple stored CRC matches resolve by candidate order

- **WHEN** the single-pass STORED disambiguation finds two candidates whose plaintext CRC-32 both match the stored value
- **THEN** the earliest match in candidate order is accepted

#### Scenario: corruption beyond the confirmed prefix surfaces on the caller's read

- **WHEN** a candidate is accepted after bounded prefix confirmation but the member's data is corrupt beyond the confirmed prefix
- **THEN** the caller's read raises `CorruptionError` at the point the ordinary ZIP read path detects it — identical to the single-candidate behavior

#### Scenario: corrupt encrypted data cannot be distinguished from a collision

- **WHEN** multiple candidates are available and a candidate passes the verification byte but fails confirmation
- **THEN** if no candidate is confirmed, `EncryptionError` states that the passwords may be wrong or the member may be corrupt and returns no bytes

#### Scenario: unrelated OSError is not a candidate failure

- **WHEN** confirmation encounters an `OSError` other than BZIP2's exact `"Invalid data stream"` decoder message
- **THEN** that `OSError` propagates unchanged and the failed stream is closed

#### Scenario: structural BadZipFile is not password ambiguity

- **WHEN** opening an encrypted member reports a local-header or other structural `BadZipFile` failure
- **THEN** the reader raises `CorruptionError` immediately rather than trying another password or reporting candidate exhaustion
