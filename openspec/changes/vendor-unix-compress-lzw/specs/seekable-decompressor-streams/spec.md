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

Unix-compress has no length or checksum trailer: source EOF SHALL end the stream
successfully even if a partial trailing code remains; the system MUST NOT raise
`TruncatedError` solely because the bitstream ended mid-code.

#### Scenario: unix-compress seek matrix

| Case | Expected |
| --- | --- |
| Seekable `.Z`, `seekable=True`, seek backward across a CLEAR | Resumes from CLEAR/`SeekPoint`; no rewind diagnostic |
| Seekable `.Z`, `seekable=False` | Forward-only; no CLEAR table retained |
| Non-seekable `.Z` pipe, forward read | Decompresses; `seekable()` false |
| Truncated `.Z` (cut bitstream) | Yields fewer bytes; no `TruncatedError` |
| Corrupt LZW codes | `CorruptionError` |
