# Live (streaming) decompression-ratio bomb guard

## Why

The decompression-ratio bomb guard is **static-denominator only**. The per-member ratio needs
`member.compressed_size` (known for ZIP, `None` for TAR members), and the archive-wide ratio
needs `reader.compressed_source_size` (known for a size-probeable compressed archive, `None`
for a pipe). When **both are unknown** — the common case of a `streaming=True` `tar.gz` (or any
compressed archive) read from a **non-seekable pipe** — the ratio check never activates, and
the only decompression-bomb protection is the absolute `max_extracted_bytes` cap.

That is the weakest configuration and exactly the one an attacker picks: unknown total size,
enormous expansion. A 2 GiB default cap still writes up to 2 GiB from a few KiB of input before
tripping, whereas a ratio guard would stop a 1000:1 bomb almost immediately.

The ratio does not actually need the total size up front. It can be measured **live**: compare
the **compressed bytes consumed** from the underlying source against the **uncompressed bytes
written**, and trip once that running ratio crosses `max_ratio` (past the activation floor).
This works for a pipe, because the running consumed-byte count is available even when the total
never will be.

## What Changes

### `compressed-streams` (ADDED) — a "bytes consumed" signal from the decompression layer

The decompression stream layer SHALL expose a monotonically increasing count of **compressed
bytes consumed from the underlying source** so far (e.g. an `input_bytes_consumed` property on
the wrapped stream, backed by a counting reader around the raw source). The coordinator/tracker
reads uncompressed output already (what it writes); this surfaces the compressed side so a live
ratio can be computed. It is cheap (a counter incremented as raw bytes are read) and available
even on a non-seekable source.

### `safe-extraction` (ADDED) — live streaming decompression ratio

`BombTracker` SHALL, when neither `member.compressed_size` nor `compressed_source_size` gives a
usable denominator, compute a live ratio `uncompressed_written / compressed_consumed_so_far`
using the stream's consumed-byte signal, and raise `ExtractionError` once it exceeds `max_ratio`
after the cumulative output passes `ratio_activation_threshold`. Because compressed bytes cannot
be cleanly attributed to a single member in a solid/streamed container, the live ratio is a
**cumulative / archive-wide** guard (it extends the existing archive-wide ratio with a live
denominator), not a new per-member one. It **complements** the static checks: whichever guard
has a usable denominator may trip first.

## Impact

- **Closes the bomb-protection gap** for `streaming=True` compressed archives from pipes, where
  today only `max_extracted_bytes` applies.
- **Affected code:** the stream/decompressor layer (`internal/streams/*` — add the consumed
  counter), `internal/extraction.py` (`BombTracker` live-ratio path; the coordinator passes the
  member/source stream's consumed signal in), and tests.
- **Depends on / coordinates with:** `phase-4-safe-extraction` (#28) — this layers on the
  `BombTracker` that change introduces. Land after it.
- **Not a behavior change for known-size sources:** ZIP (per-member `compressed_size`) and
  size-probeable compressed archives keep using their static denominators; the live path only
  engages when those are absent.

## Open questions (see design.md)

- Exactly where the consumed counter lives (per member stream vs the outer archive source) and
  how the coordinator obtains it uniformly across backends.
- Whether the live ratio is purely cumulative (recommended) or also attempted per-member when a
  member stream has its own isolated compressed substream (e.g. a non-solid entry).
