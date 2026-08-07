# The rewind diagnostic becomes cost-based

## Why

`STREAM_REWIND_REDECOMPRESSES` fires on **codec identity**, decided once at open: xz,
lzip and unix-compress are treated as "has an index, never warn", because those *formats*
can carry one. A format that *can* carry an index does not always *have* a useful one:

```
single-block .xz, 1 MB of incompressible data
  seek points in the stream : [(0, 0)]     ← one, just the origin
  rewind warning configured : None
  seek(end → offset 10)     : no diagnostic emitted
```

That seek re-decompressed a megabyte from byte zero and the library said nothing. A
single block is the **common** case, not a contrived one — `lzma.compress()` produces
one, and so does `xz` without threading.

**The consequence that matters is not the missing message.** To reach this diagnostic at
all you must have opted into seekable member streams, so a passive advisory mostly tells
you something you already knew. Its real job is the **tripwire**: a `DiagnosticPolicy`
can turn it into a raised error so a batch job aborts instead of silently going
quadratic. Armed today, you are protected on `.lzma` and un-accelerated `.bz2`, and
silent on a single-block `.xz` that re-decodes the whole stream on every backward seek.
It fails exactly where you would depend on it.

**And the accelerated path has the same hole.** Measured while implementing this (O1
left it as an open measurement):

```
rapidgzip 0.16, gzip.compress of 5 MB of random data, after a full read
  available_block_offsets() : {80: 0, 33579936: 4196202, 40012384: 5000000}
  cost of the call          : ~0.01 ms
```

Three index points over 5 MB. A backward seek to offset 4 MB re-decodes 4 MB from the
origin, with rapidgzip engaged and the current rule saying nothing at all. So
"accelerator present ⇒ cheap" is exactly as wrong as "format can carry an index ⇒ cheap",
and the spacing *is* retrievable, cheaply — which settles O1's open question in favour of
one uniform predicate rather than a per-codec taxonomy with an accelerator exception.

## What Changes

- **The predicate is the seek's actual re-decode distance**, computed at seek time:
  `target − nearest preceding seek point`, compared against an **absolute** byte
  threshold. One rule; no codec taxonomy.
- **The threshold is absolute, not relative.** Recorded because the review initially
  argued the other way and the counterexample is decisive: on a 1 GB single-block `.xz`,
  seeking from the end back to 900 MB re-decodes 900 MB but only *jumps* ~100 MB — ratio
  0.11×, under any sane relative threshold. **Relative goes quietest exactly where
  absolute cost is highest.** The caller cares about wall time, which tracks bytes
  re-decoded, and absolute matches the existing `RAPIDGZIP_AUTO_MIN_COMPRESSED_SIZE`
  precedent, so there is one threshold vocabulary rather than two.
- **Record once, escalate always.** The diagnostic is still *recorded* at most once per
  stream — bounded output, readable audit trail — but the **policy is evaluated on every
  qualifying seek**, so a `RAISE` policy stops the second expensive seek too. The two
  jobs were only ever coupled by implementation convenience: deduplication is a
  presentation concern for the report, escalation is control flow for a caller who
  explicitly asked to be stopped.
- **`seekable-decompressor-streams`** — the clause "XZ, lzip, and unix-compress indexed
  seeks SHALL NOT emit this event" is what has to move, and does.
- **`diagnostics`** — the record-once/escalate-always split is written down as the
  general rule for once-per-stream codes, not a local exception (O1 asked for it either
  way; `STREAM_REWIND_REDECOMPRESSES` is currently its only user).

## Impact

- Specs: `seekable-decompressor-streams`, `diagnostics`.
- Code: `src/archivey/internal/streams/archive_stream.py`,
  `decompressor_stream.py`, `codecs.py`, `config.py` (the threshold),
  `internal/diagnostics_collector.py` (escalate-without-recording).
- Docs: `docs/access-and-cost.md`, `docs/errors-and-diagnostics.md`.
- Caller-visible: streams that used to say nothing now emit on an expensive rewind;
  streams that emitted on a *cheap* rewind (a short `.lzma`) now stay quiet.
