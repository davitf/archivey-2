## MODIFIED Requirements

### Requirement: Report single-file compressor properties

The backend SHALL expose these properties for every single-file compressor:

| Property | Value |
| --- | --- |
| Listing cost | `INDEXED`; exactly one member |
| Access cost | `DIRECT`; no inter-member dependency exists |
| Supports write | Yes |
| Requires seek | Random access (`streaming=False`) requires seek; forward-only `streaming=True` accepts non-seekable sources for every supported single-file codec including `.Z` |

Random access over a non-seekable source SHALL fail fast at open with
`StreamNotSeekableError`; the backend MUST NOT buffer an unbounded source to
simulate repeatable reads. Under `streaming=True`, every supported single-file
codec including unix-compress `.Z` SHALL stream from non-seekable sources.

Member-stream seekability is a stream-level property from index- or
accelerator-backed decoders (for example xz indexes, CLEAR seek points for
unix-compress, `indexed_bzip2`, `rapidgzip`, seekable zstd), not an archive-level
`CostReceipt` field.

#### Scenario: property matrix

| Case | Expected |
| --- | --- |
| Open any supported single-file compressor | `listing_cost=INDEXED`; `access_cost=DIRECT` |
| Non-seekable source with `streaming=False` | `StreamNotSeekableError` |
| Non-seekable source with `streaming=True` (including `.Z`) | Opens and `stream_members()` yields data |
| Seekable `.Z` with declared member-stream seekability | Member stream is seekable via CLEAR seek points |
