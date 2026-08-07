# seekable-decompressor-streams — cost-based rewind delta

## MODIFIED Requirements

### Requirement: Index-less rewinds emit diagnostic data

The predicate for `STREAM_REWIND_REDECOMPRESSES` SHALL be **the decoded progress the
rewind discards**, not the codec's identity:

```
redecode_distance = position_before_seek − nearest_seek_point_at_or_before(target)
```

That is, the bytes that must be decoded again to return to where the caller was. It is
measured from the **pre-seek position**, not from the target: the seek call itself may
decode almost nothing — `seek(10)` after reading a gigabyte decodes ten bytes — while
throwing away a gigabyte of progress, and it is the discarded gigabyte that makes a seek
loop quadratic.

When a backward seek's `redecode_distance` reaches
`REWIND_REDECODE_WARN_BYTES` (an **absolute** byte threshold), the system SHALL report
`STREAM_REWIND_REDECOMPRESSES` with codec, before/after offsets, the distance, and
accelerator name or `None`. Forward and no-op seeks SHALL report nothing.

The threshold SHALL be absolute rather than relative to the jump distance. A relative
rule goes quietest exactly where the absolute cost is highest: on a 1 GB single-block
`.xz`, seeking from the end back to 900 MB re-decodes 900 MB while jumping only ~100 MB,
a ratio no sane relative threshold would flag.

The distance SHALL be computed against the seek index **as it exists at seek time**, and
obtaining it MUST NOT trigger index construction — a diagnostic that built an index would
change the cost it reports.

**The predicate is uniform across codecs, including accelerated ones.** A format that
*can* carry an index does not always *have* a useful one, and neither does an
accelerator: a single-block `.xz` has one seek point (the origin), and `rapidgzip`'s
`available_block_offsets()` on a `gzip.compress` output is sparse enough that a backward
seek can re-decode megabytes with the accelerator engaged. Both are the same event and
SHALL be reported the same way.

| Stream shape | Nearest resume point | Reported |
| --- | --- | --- |
| No index at all | the origin | yes, once progress passes the threshold |
| Single-block xz / one-member lzip / `.Z` with no CLEAR codes | the origin | yes, same as above |
| Multi-block xz, multi-member lzip | the containing block | only when the progress since that block passes the threshold |
| Accelerated gzip/bzip2, dense index | the containing block | as above |
| Accelerated gzip/bzip2, sparse index | up to a block back | yes, once past the threshold |
| Any codec, less progress discarded than the threshold | — | no |
| `STORED` (no decompression) | — | never; nothing is re-decoded |

A stream that exposes no seek-point table SHALL be treated as resuming from the origin.
For a *decompressing* stream — the only kind this event applies to — that is the truth
rather than a guess, and it is what keeps the index-less codecs (stdlib LZMA Alone,
brotli, lz4) reporting.

The event SHALL live on the stream operation and cumulative owning-reader
aggregate, never on `CostReceipt` or `ArchiveInfo`. Gzip/bzip2/deflate/zlib context names the
`[seekable]` accelerator when a rapidgzip path was eligible; brotli, lz4, and zstd record no
accelerator. Stdlib zstd SHALL rewind in place like other index-less codecs. When a
deflate/zlib stream falls back to stdlib `zlib` (accelerator absent, `OFF`, or below the
`AUTO` threshold), its rewind SHALL name the `[seekable]` accelerator, consistent with the
gzip fallback.

**Recording is deduplicated; escalation is not.** The occurrence SHALL be *recorded* at
most once per stream, and the configured `DiagnosticPolicy` SHALL be evaluated on **every**
qualifying seek, so a `RAISE` policy stops the second expensive rewind as well as the
first. See `diagnostics` for the general rule.

#### Scenario: slow rewind matrix

| Case | Expected |
| --- | --- |
| One index-less stream performs many backward seeks | One recorded `STREAM_REWIND_REDECOMPRESSES`; later rewinds add no duplicate record |
| Rewind diagnostic resolves to `RAISE`, first qualifying seek | `DiagnosticRaisedError` raised from that seek |
| Rewind diagnostic resolves to `RAISE`, **second** qualifying seek | `DiagnosticRaisedError` raised again — the guard does not disarm |
| Only forward/no-op seeks occur | No occurrence |
| Full rewind on a **single-block** `.xz` | One occurrence — the format could have carried an index; this file does not |
| Rewind within one block of a multi-block `.xz` | No occurrence |
| Rewind of fewer than `REWIND_REDECODE_WARN_BYTES` on any codec | No occurrence |
| Rewind across a sparse gap in an engaged `rapidgzip` index | One occurrence naming the accelerator |
| zstd stream rewinds via stdlib backend | Re-decompresses from start in place; one occurrence when above the threshold |
| Stdlib-fallback zlib/deflate stream rewinds | One occurrence naming the `[seekable]` accelerator |
| Stream with no seek-point table (stdlib LZMA Alone, brotli, lz4) | Resume point is the origin; one occurrence once progress passes the threshold |
| `STORED` member seeks backward | No occurrence, at any distance |
