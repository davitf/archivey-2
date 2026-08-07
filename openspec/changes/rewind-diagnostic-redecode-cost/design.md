# Design — the rewind diagnostic becomes cost-based

## Decisions

### The predicate

```
redecode_distance = target_offset − nearest_seek_point_at_or_before(target).decompressed_offset
```

Every case falls out of that one expression:

| Stream | Nearest point | Result |
|---|---|---|
| No index at all | the origin | whole prefix — loud, as today |
| **Single-block xz / one-member lzip** | the origin | whole prefix — **loud, where today it is silent** |
| Multi-block xz, multi-member lzip | the containing block | bounded — quiet, as today |
| Accelerated gzip with a dense index | the containing block | bounded — quiet |
| **Accelerated gzip with a sparse index** | up to a block away | **loud, where today it is silent** |

The distance must be computed against the index **as it exists at seek time**, and asking
for it must not *build* one: a diagnostic that triggers an index scan would change the
cost it is reporting on.

### Absolute threshold, not relative

Revision 1 of the review leaned relative ("you re-decoded more than the distance you
jumped"), on the grounds that it captures *disproportionate* work. The counterexample is
decisive and is recorded here because this is the kind of thing that gets relitigated:

> A 1 GB single-block `.xz`. Seek from the end back to offset 900 MB. The nearest seek
> point is the origin, so the re-decode cost is 900 MB — enormous. But the *jump* is only
> ~100 MB, so the ratio is ~0.11×, well under any sane relative threshold, and the
> tripwire stays silent.

Relative measures inefficiency. The caller cares about wall time, which tracks bytes
re-decoded. Absolute also matches `RAPIDGZIP_AUTO_MIN_COMPRESSED_SIZE`, already in the
config for the same "below this it is cheap enough that saying so is noise" reason — one
threshold vocabulary instead of two.

The value is 1 MiB, deliberately the same number as the rapidgzip AUTO floor. They are
different quantities (compressed input size vs decompressed re-decode distance) but the
same judgement: below roughly a megabyte the work is not worth a caller's attention.

### `rapidgzip` index spacing: retrievable, so the predicate stays uniform

O1 left this as work rather than a decision: "check the API, and if the spacing isn't
retrievable, that path keeps the current accelerator-presence rule and the specification
has to say the predicate is not uniform across codecs."

Measured against rapidgzip 0.16:

```
available_block_offsets() -> {compressed_bit_offset: decompressed_offset}
  cost: ~0.01 ms on an already-open stream (0.08 ms before any read)
  gzip.compress(5 MB of random bytes), after a full read:
      {80: 0, 33579936: 4196202, 40012384: 5000000}
```

It is retrievable and it is cheap, so the predicate **is** uniform and the specification
says so. That measurement also strengthens the finding: three points over 5 MB means a
backward seek into the first gap re-decodes up to 4 MB with the accelerator engaged, and
today's rule — "an accelerator is present, therefore quiet" — misses it exactly the way
the codec-identity rule misses single-block xz.

`available_block_offsets()` is used rather than `block_offsets()`: the latter forces the
full index, which is the same "the diagnostic changed the cost" problem as above. An
incomplete index makes the reported distance an *over*-estimate at worst, which errs
toward telling the caller.

### Record once, escalate always — and it is the general rule

`DiagnosticCollector` gains `escalate_only()`: resolve a code's policy and raise if it is
`RAISE`, without counting, retaining, logging or calling back. The rewind path calls
`emit()` the first time and `escalate_only()` on every qualifying seek after that.

This is written into `diagnostics` as **the rule for any once-per-stream code**, not as a
local exception, because the alternative leaves the next deduplicated code inheriting an
undecided question. `STREAM_REWIND_REDECOMPRESSES` is currently the only once-per-stream
code in the library, so the rule has exactly one user today — which is the right time to
write it down, not a reason not to.

The justification is that the two behaviours answer different callers. A report reader
wants bounded, readable output: twenty identical rewind entries are noise. A caller who
set `RAISE` wants to be *stopped*, and a guard that disarms after firing once is not a
guard.

### Where the cost is computed

`ArchiveStream` owns the diagnostic but not the index. `DecompressorStream` owns the
index but is not the public handle, and the accelerator path has neither. So the question
is asked through a narrow protocol — `redecode_distance(target) -> int | None` — that
each layer answers for itself:

| Layer | Answer |
|---|---|
| `DecompressorStream` | `target − _find_best_seek_point(target).decompressed_offset`, over the existing table |
| `_AcceleratorStream` (rapidgzip) | `target −` the largest `available_block_offsets()` value `≤ target` |
| `ArchiveStream` | delegates to its inner (streams nest) |
| anything else | `None` — unknown cost, emit nothing |

`None` means "no answer", not "free": a stream that cannot say what a rewind costs is not
one we should guess about, and the old behaviour for those was to warn on codec identity,
which is precisely the thing being removed.

## Rejected alternatives

**Keep the codec taxonomy and special-case single-block xz.** Fixes one instance of a
general problem. Multi-member lzip, `.Z` without CLEAR codes and sparse accelerator
indexes are the same bug with different names.

**A relative threshold.** See above — it goes quietest where the cost is highest.

**Emit on every qualifying seek.** Unbounded report growth on exactly the workload that
provokes it (a quadratic seek loop emits once per seek), for no information after the
first.

**Make `escalate_only` the behaviour of `emit()` under a `dedup=` flag.** Would put the
dedup bookkeeping in the collector, which does not know what "per stream" means — the
stream does. The split keeps each fact where it is known.
