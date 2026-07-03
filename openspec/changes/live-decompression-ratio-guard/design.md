# Design — Live (streaming) decompression-ratio guard

## The core idea

A decompression ratio is `uncompressed / compressed`. Today both numbers are only used in
their *final* form: `member.compressed_size` (per member) or `compressed_source_size` (whole
archive) as the denominator, `bytes written` as the numerator. But the denominator has a
**running** value too — the number of compressed bytes the decompressor has pulled from its
source so far — and the ratio can be sampled against that as data flows. A static
`compressed_size` is just the final value of that running counter; the live counter is strictly
more available (a pipe yields the running value but never the total).

```
             raw compressed source (pipe / file)
                        │  counting reader → input_bytes_consumed  (denominator, live)
                        ▼
                   decompressor
                        │  decompressed chunks
                        ▼
          coordinator writes chunk → tracker.count(len)          (numerator, live)
                        │
             live ratio = total_written / input_bytes_consumed
             trip if  > max_ratio  (after activation floor)
```

## Decisions

### D1 — The consumed counter lives on a counting wrapper around the raw source, surfaced on the reader

Wrap the raw compressed source (the byte stream the decompressor reads from) in a thin counting
reader that increments on every `read()`. Surface the running total as a **live property on the
reader**, e.g. `compressed_bytes_consumed: int | None`, parallel to the existing
`compressed_source_size` (which is the *cheap total*; this is the *live consumed*). `None` when
there is no single compressed source to count (uncompressed container, directory).

`BombTracker` is handed a zero-argument sampler (a callable returning the current consumed
count, or `None`) once per extraction, alongside `compressed_source_size`, and reads it inside
`count()`.

### D2 — It is a cumulative / archive-wide guard, not per-member

In a **solid** or **streamed** container a single compression stream spans all members, so you
cannot cleanly attribute "compressed bytes consumed" to one member — the boundary between
members is inside the compressed stream. So the live ratio is evaluated on the **cumulative**
output vs the **cumulative** consumed bytes, extending the existing *archive-wide ratio*: use
the static `compressed_source_size` as the denominator when it is known, otherwise fall back to
the live consumed count. Non-solid formats (ZIP) already have a per-member `compressed_size` and
do not need this path.

### D3 — Complements the static guards; same limit and activation floor

The live ratio uses the same `max_ratio` and `ratio_activation_threshold` as the static checks,
and runs alongside them — the per-member ratio (when `compressed_size` is known) and the live
archive-wide ratio are independent; whichever crosses first trips. The live path engages only
when the archive-wide **static** denominator is absent, so a size-probeable `.tar.gz` keeps its
existing (cheaper, total-based) archive-wide check and does not double-count.

### D4 — Safe on uncompressed sources

For a plain (uncompressed) `.tar` from a pipe, consumed ≈ written, so the live ratio sits near
1:1 and never trips — exactly as the static archive-wide ratio "never trips for an uncompressed
container." No special-casing needed; reporting a consumed count for every source is safe.

### D5 — Engagement condition

The live archive-wide ratio activates only when: the outer stream is compressed (a consumed
counter is available and distinct from the output), the static `compressed_source_size` is
`None`, and cumulative output has passed the activation floor. Otherwise it is skipped and the
existing guards apply unchanged.

## Open questions to resolve during apply

- **Counter placement across backends.** For a compressed TAR the counter belongs on the codec
  stream feeding `tarfile`; confirm the wrapper sits at the right layer (below the decompressor,
  above the raw source) for every compressed-container path, and that `stream_members`' member
  streams share that same outer counter (they must, for the cumulative framing to hold).
- **Accelerator backends.** `rapidgzip` / `indexed_bzip2` may read ahead or seek; confirm the
  consumed count still reflects real input pressure (or gate the live guard to the plain
  sequential decoders where consumption tracks output causally).
- **Per-member opportunistic use.** Whether to *also* compute a per-member live ratio when a
  member stream is a genuinely isolated compressed substream (non-solid, unknown
  `compressed_size`) — deferred unless a real format needs it.

## Non-goals

- No change to `read()` / `open()` (bomb limits stay extraction-only, per `safe-extraction`).
- No new public config surface — reuses `max_ratio` / `ratio_activation_threshold`.
- Not the `openat2`/permission topics from other changes.
