## MODIFIED Requirements

### Requirement: Stored-digest parity across backends

The corpus conformance sweep SHALL assert stored-digest parity: for every applicable
member, each backend SHALL surface the stored digest(s) documented for its format, and
SHALL omit digest keys where the format stores none. This turns silent parity drift ("a
backend quietly stopped populating `crc32`") into a test failure.

The asserted matrix SHALL match the documented policy:

| Format | Member kind | Expected `hashes` keys |
| --- | --- | --- |
| ZIP | FILE / SYMLINK | `crc32` present |
| 7z | FILE | `crc32` present |
| RAR5 | FILE with CRC32 | `crc32` present |
| RAR5 | FILE with Blake2sp only | `blake2sp` present, `crc32` absent |
| single-file GZIP | single member, seekable | `crc32` present |
| single-file GZIP | multi-member or non-seekable | digest keys absent |
| single-file LZIP | seekable lzip index (one or many members) | `crc32` present |
| single-file LZIP | no seekable index | digest keys absent |
| single-file BZ2/XZ/ZLIB/BR/`.Z`, TAR, directory | any | no stored-digest key |

#### Scenario: parity sweep

| Case | Expected |
| --- | --- |
| Backend surfaces its documented stored digest for an applicable member | Sweep passes |
| A backend stops populating a documented digest | Sweep fails |
| A backend populates a digest the format does not store | Sweep fails |
| Multi-member lzip combined `crc32` matches `crc32` of full decompressed concat | Sweep or focused unit test passes |
