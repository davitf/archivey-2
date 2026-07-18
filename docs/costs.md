# Access costs and pitfalls

Archivey’s defaults keep the common path cheap and fail loudly when you ask for something
expensive. This page is the “how not to shoot yourself in the foot” guide.

## Read `reader.cost`

Every open archive exposes a machine-readable receipt:

| Field | Meaning |
| --- | --- |
| `listing_cost` | `INDEXED` / `REQUIRES_SCANNING` / `REQUIRES_DECOMPRESSION` |
| `access_cost` | `DIRECT` (member N independent) or `SOLID` (may need earlier bytes) |
| `stream_capability` | `SEEKABLE` source vs `FORWARD_ONLY` |
| `solid_block_count` | Distinct solid blocks, when known |

Cost never changes what is *legal* — it describes what your access pattern will *pay*.

### RAR listing cost

RAR reports `listing_cost=INDEXED`: the native parser walks all file headers at open
time and builds the member table in memory before `members()` is called. The optional
**Quick Open** record (a pre-built central directory in some RAR5 archives) is read but
is not the primary source — every archive header is still traversed, so the open-time
cost scales with member count. Once open, `members()` / `get()` return from the
in-memory table at O(1) cost.

## Solid archives: prefer one forward pass

On solid 7z / RAR (and compressed TAR, which is solid for random member access), opening
members out of order can **re-decode the same block** for each `open()`.

**Do this:**

```python
for member, stream in reader.stream_members():
    consume(stream)   # one decode of each solid block
```

**Avoid this on solid archives** (unless you accept the cost):

```python
for name in wanted_names:
    with reader.open(name) as s:   # may restart the solid block each time
        ...
```

`AccessCost.SOLID` and `solid_block_count` tell you when this matters.
`MemberStreams.CONCURRENT` does **not** remove solid open-order cost — it only makes
overlapping streams correct.

## Seeking inside compressed members

Without `MemberStreams.SEEKABLE`, member streams report `seekable() is False` and
`seek()` raises `io.UnsupportedOperation`. That is intentional: seek indexes and
accelerators are not built until you ask.

With `SEEKABLE`:

- XZ / lzip can seek via native indexes
- gzip / zlib / raw deflate / bzip2 can use `[seekable]` (`rapidgzip`) when installed
- otherwise a backward seek may **re-decompress from the start** (loud diagnostic, not
  silent)

Under `ArchiveyConfig.use_rapidgzip=AUTO` (the default), rapidgzip is selected only when
seekability is declared **and** the known compressed input is at least
`RAPIDGZIP_AUTO_MIN_COMPRESSED_SIZE` (1 MiB). Smaller members stay on stdlib `zlib`/`gzip`
so archives of many tiny entries do not pay per-stream accelerator setup. Set
`use_rapidgzip=ON` to force the accelerator regardless of size, or `OFF` to disable it.

Declare seek only when you need it (e.g. parquet-in-zip random reads).

## Concurrent member streams

Default: at most one live member stream. A second overlapping `open()` raises
`ConcurrentAccessError` (a usage error — not an `ArchiveyError`).

```python
open_archive(src, member_streams=MemberStreams.CONCURRENT)
```

After members are materialized, workers may `open()` different members concurrently.
Same-stream access still needs caller synchronization. Reader-wide passes
(`__iter__` / `stream_members` / `extract_all`) remain single-owner.
`streaming=True` cannot combine with `CONCURRENT`.

## Non-seekable sources

`streaming=False` (default) **fails fast** if the format needs seek and the source is a
pipe. Archivey will not silently buffer the whole archive into memory or a temp file.
Use `streaming=True` for pipes/sockets.

ZIP (stdlib) and ISO always need seek today — even `streaming=True` cannot open them
from a pure pipe.

## Streaming mode is one pass

With `streaming=True`, the first of `__iter__` / `stream_members` / `extract_all`
consumes the pass. A second call raises — including after an early `break`. Use
`scan_members()` to finish/drain when you need a full list after a partial pass.

## Passwords and confirmation cost

Multiple password candidates can trigger confirmation reads. ZipCrypto **STORED** members
are the expensive niche: a wrong candidate that passes the weak open check may force a
full-member CRC scan. Prefer a single known password when reading huge stored encrypted
members.

## Accelerators and process aborts

The `[seekable]` path uses `rapidgzip` (gzip / zlib / raw deflate + bzip2). Do not close
the caller-owned source underneath a live accelerator-backed stream — some upstream
defects can abort the process rather than raise. Details:
[internal known issues](internal/known-issues.md).

## Checklist

| Situation | Prefer |
| --- | --- |
| Hash / process every member | `stream_members()` or `__iter__` |
| Solid archive, many named opens | Reorder to archive order, or one streaming pass |
| Need `seek()` on a member | `MemberStreams.SEEKABLE` (+ `[seekable]` for gz/bz2/zlib/deflate) |
| Thread pool of member readers | `MemberStreams.CONCURRENT` after `members()` |
| stdin / socket | `streaming=True` |
| “Just unzip it safely” | `archivey.extract(src, dest)` |
