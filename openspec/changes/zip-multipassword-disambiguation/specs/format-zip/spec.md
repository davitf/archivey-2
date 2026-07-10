# format-zip — ZipCrypto multi-candidate disambiguation delta

## ADDED Requirements

### Requirement: Multi-candidate disambiguation for traditional ZipCrypto

For a member encrypted with traditional ZipCrypto (PKWARE), whose per-open password check
is a single verification byte while the authoritative check is the member's CRC-32 (and,
for a compressed member, completion of the decompressor), the ZIP reader SHALL fully read
and validate a candidate that passes the byte check before accepting it whenever another
candidate may be tried under `archive-reading` → "Confirm candidates when a weak check
permits retries".

During validation, plaintext SHALL be copied in bounded chunks to a spooled temporary
stream with a fixed in-memory threshold and disk spillover. On success, the reader SHALL
rewind and return that retained plaintext; it SHALL NOT reopen and decompress the winning
member. On failure, source closure SHALL be attempted and temporary-stream closure SHALL
run even if source closure raises. This bounds RAM but not validation time or temporary
disk: both can remain proportional to the uncompressed member size.

The candidate-validation failure set SHALL include only `zipfile.BadZipFile` whose message
identifies `"Bad CRC-32 for file ..."`, `zlib.error`, `lzma.LZMAError`, and exactly the
BZIP2 decoder's `OSError("Invalid data stream")`. Local-header mismatch, bad local-header
magic, overlap, and other structural `BadZipFile` failures SHALL remain `CorruptionError`
and stop candidate iteration. Other `OSError` values SHALL propagate unchanged.

If one or more candidates reach validation but no candidate succeeds, the reader SHALL
raise `EncryptionError` explaining that the passwords may be wrong or the encrypted member
may be corrupt. This is intentionally not a promise to distinguish those equivalent
failure observations. The reader SHALL never return a candidate selected by order,
neighbour/content heuristics, or guess-with-warning.

With one distinct static candidate, including duplicate copies of it, the reader SHALL
retain its normal lazy stream with no eager full read. A read-time integrity failure on
that path SHALL be translated as corruption in the ordinary way.

#### Scenario: colliding wrong candidate is rejected for every stdlib ZIP codec

- **WHEN** a STORED, DEFLATE, BZIP2, or LZMA ZipCrypto member is opened with a wrong candidate that passes the verification byte followed by the correct candidate
- **THEN** full validation rejects the wrong candidate and the reader returns the correct candidate's retained validated bytes

#### Scenario: single candidate keeps the fast path

- **WHEN** a ZipCrypto member is opened with one distinct static candidate password
- **THEN** the reader does not perform an eager full-member read to confirm it; the member streams as before

#### Scenario: the winning member is decoded once

- **WHEN** a colliding wrong candidate is followed by a correct candidate
- **THEN** the correct candidate is opened and decoded once, and the caller reads the validated plaintext from the rewound spool

#### Scenario: corrupt encrypted data cannot be distinguished from a collision

- **WHEN** multiple candidates are available and a candidate passes the verification byte but the encrypted member fails decompression or CRC
- **THEN** if no candidate validates, `EncryptionError` states that the passwords may be wrong or the member may be corrupt and returns no bytes

#### Scenario: unrelated OSError is not a candidate failure

- **WHEN** validation encounters an `OSError` other than BZIP2's exact `"Invalid data stream"` decoder message
- **THEN** that `OSError` propagates unchanged and the failed stream is closed

#### Scenario: structural BadZipFile is not password ambiguity

- **WHEN** opening an encrypted member reports a local-header or other structural `BadZipFile` failure
- **THEN** the reader raises `CorruptionError` immediately rather than trying another password or reporting candidate exhaustion

#### Scenario: source close failure still releases the spool

- **WHEN** closing a candidate source raises after its validation spool was created
- **THEN** spool closure still runs and the source-close exception propagates
