## ADDED Requirements

### Requirement: Unix-compress uses CLEAR seek points

When seekability is declared and the compressed source is seekable, the system
SHALL register `SeekPoint`s at stream start (after the 3-byte `.Z` header) and at
each LZW CLEAR realignment. A seek SHALL resume from the nearest preceding
seek point with an empty dictionary and MUST NOT emit
`STREAM_REWIND_REDECOMPRESSES`.

Forward decode SHALL NOT call `seek` on the compressed source: CLEAR bit-block
realignment MUST use a bounded in-memory buffer. When seekability is not
declared, the system SHALL NOT retain a CLEAR seek-point table. When the source
is not seekable, the decompressor stream SHALL report `seekable() is False` and
`seek` SHALL raise `io.UnsupportedOperation`.

Unix-compress has no length or checksum trailer. At source EOF, after all
decoded bytes have been delivered, the system SHALL best-effort detect
truncation: if any leftover bits remain after the last complete LZW code and
those bits are nonzero (finished compressors zero-pad), the next `read()` SHALL
raise `TruncatedError`. Zero leftover bits (including a cut exactly on a code
boundary) SHALL end successfully — such truncation remains undetectable.

Unknown reserved header flag bits (`0x60` in the third header byte) SHALL raise
`UnsupportedFeatureError` when the header is parsed.

#### Scenario: unix-compress seek matrix

| Case | Expected |
| --- | --- |
| Seekable `.Z`, `seekable=True`, seek backward across a CLEAR | Resumes from CLEAR/`SeekPoint`; no rewind diagnostic |
| Seekable `.Z`, `seekable=False` | Forward-only; no CLEAR table retained |
| Non-seekable `.Z` pipe, forward read | Decompresses; `seekable()` false |
| Truncated `.Z` with nonzero leftover bits | Yields available bytes; next `read()` raises `TruncatedError` |
| Truncated `.Z` with only zero leftover bits | Yields fewer bytes; no `TruncatedError` (undetectable) |
| Header flag byte has reserved bits `0x60` set | `UnsupportedFeatureError` |
| Corrupt LZW codes | `CorruptionError` |
