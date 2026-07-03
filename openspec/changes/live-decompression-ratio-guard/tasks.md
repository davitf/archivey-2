# Tasks — Live (streaming) decompression-ratio guard

> Run tools through uv: `uv run pytest`, `uv run pyrefly check`, `uv run ty check`,
> `uv run ruff`. Depends on `phase-4-safe-extraction` (#28) — layers on its `BombTracker`.
> Land after it merges.

## 1. Stream layer: compressed-bytes-consumed signal

- [ ] 1.1 Add a thin counting reader that wraps the raw compressed source and increments a
      running total on each `read()`; expose it as `input_bytes_consumed` on the decompression
      stream wrapper. Cheap, non-seekable-safe, no change to bytes read/decoded.
- [ ] 1.2 Surface the archive's outer running total on the reader as
      `compressed_bytes_consumed: int | None` (parallel to `compressed_source_size`); `None`
      for an uncompressed container / directory. Ensure member streams served from the same
      outer compressed stream (solid/streamed) feed the **same** counter (cumulative).
- [ ] 1.3 Confirm accelerator backends (`rapidgzip`, `indexed_bzip2`) either report a
      consumption count that tracks real input pressure, or gate the live guard to the plain
      sequential decoders (per design D-open-questions).
- [ ] 1.4 Stream-layer tests: consumed count grows monotonically on a piped `.gz`; `None` for
      uncompressed/directory; observing the count doesn't perturb decoded output.

## 2. BombTracker: live ratio

- [ ] 2.1 Give `BombTracker` a consumed-bytes sampler (a zero-arg callable returning
      `int | None`) alongside `compressed_source_size`. In `count()`, when
      `compressed_source_size` is `None` but the sampler returns a positive value, evaluate the
      live ratio `_total_bytes / consumed` past the activation floor against `max_ratio`; raise
      the always-stop `ExtractionError` on exceed. Skip when the static archive-wide denominator
      is known (no double-count) or the sampler is `None`/0.
- [ ] 2.2 The coordinator passes the reader's `compressed_bytes_consumed` sampler into the
      tracker once per extraction call.

## 3. Tests

- [ ] 3.1 Streaming `.tar.gz` zip-bomb from a non-seekable pipe is caught by the **live** ratio
      before the absolute `max_extracted_bytes` cap.
- [ ] 3.2 Plain (uncompressed) `.tar` from a pipe never trips the live ratio; cumulative byte
      cap still applies.
- [ ] 3.3 A size-probeable `.tar.gz` uses the **static** archive-wide ratio (live path not
      engaged; ratio not double-counted).
- [ ] 3.4 The live ratio halts even under `OnError.CONTINUE`.

## 4. Gates

- [ ] 4.1 `uv run pyrefly check` + `uv run ty check` + `uv run ruff` clean.
- [ ] 4.2 Full suite green; new streaming-bomb scenarios pass.
