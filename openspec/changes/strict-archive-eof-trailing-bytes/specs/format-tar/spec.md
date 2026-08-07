# format-tar — strict trailing-bytes delta

## ADDED Requirements

### Requirement: Under strict EOF, nothing but zeros may follow the trailer

`strict_archive_eof` is documented as the knob for "a provably complete listing". With
`strict_archive_eof=True`, after the two-block null end-of-archive trailer, **every
remaining byte to EOF SHALL be zero**; the first non-zero byte SHALL emit
`ARCHIVE_TRAILING_DATA` and escalate to `CorruptionError`, after the diagnostic's normal
count/retention/log/callback ordering. With `strict_archive_eof=False` behaviour is
unchanged, **including the cost**: no scan runs and no diagnostic is emitted.

The check SHALL run only on the success path of the trailer verification — after a
complete two-block null trailer has been confirmed — so it never competes with the
`absent` / `short` / `nonzero` classifications of the trailer itself.

Consequences, all intended:

| Input, `strict_archive_eof=True` | Result | Why |
| --- | --- | --- |
| Trailer then zero padding (`tar` writes 10 KiB records) | Accepted | Padding is the overwhelmingly common case; this is why the rule is "nothing but zeros", not "EOF immediately" |
| Trailer then any non-zero byte | `CorruptionError` | The file carries something the listing did not account for |
| Two concatenated tars | `CorruptionError` | They *are* two archives and only the first was listed; a caller who asked for a provably complete listing should be told |
| An ISO read as TAR | `CorruptionError` | Its zeros stop at 32768, where the volume descriptors begin; ~48 KiB of real data follows, so it is not zeros to EOF. Without the flag it stays an empty listing covered by `EMPTY_ARCHIVE` / `EXPLICIT_FORMAT_LISTED_EMPTY` |
| A legitimately empty tar (10240 zero bytes) | Accepted | All zeros |

It SHALL raise `CorruptionError` rather than `TruncatedError`: nothing is truncated —
the file is *longer* than the listing accounts for — and the adjacent rejected-header case
in the same check already answers that shape of evidence with `CorruptionError`.

**Cost.** The check reads to EOF, so `strict_archive_eof` goes from O(512 bytes) to
O(tail length). On a non-seekable source that is a real scan, and on a compressed tar the
tail must be decompressed to be inspected. This is why the rule is gated on the flag
rather than emitted unconditionally, and it SHALL be documented on the flag.

#### Scenario: strict trailing-bytes matrix

| Case | `strict_archive_eof=False` | `strict_archive_eof=True` |
| --- | --- | --- |
| Valid tar, trailer, EOF | No diagnostic | No diagnostic |
| Valid tar + 4 KiB of zeros | No diagnostic | No diagnostic |
| Valid tar + 4 KiB of `b"JUNK"` | No diagnostic *(unchanged)* | `ARCHIVE_TRAILING_DATA` → `CorruptionError` |
| Valid tar + zeros + one non-zero byte + zeros | No diagnostic | `ARCHIVE_TRAILING_DATA` → `CorruptionError` |
| Two tars concatenated | No diagnostic; first listed | `ARCHIVE_TRAILING_DATA` → `CorruptionError` |
| Legitimately empty tar (10240 zeros) | No diagnostic | No diagnostic |
| 32 KiB of zeros opened as TAR (a zero-filled file, not an ISO) | No diagnostic | No diagnostic; all zeros. `EMPTY_ARCHIVE` covers it |
| A real ISO opened as TAR | No diagnostic; empty listing | `CorruptionError` — its data past the system area is not zeros |
| Missing / short trailer | Existing `ARCHIVE_EOF_MARKER_MISSING` behaviour | Existing `TruncatedError`; the trailing-bytes scan does not run |
| `.tar.gz` with trailing junk | No diagnostic and no decompression of the tail | Tail decompressed; `CorruptionError` |
