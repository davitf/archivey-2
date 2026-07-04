# Tasks — Inner-TAR probe reads a full block (large-block `.tar.bz2` detection)

> Run tools through uv: `uv run pytest`, `uv run pyrefly check`, `uv run ty check`,
> `uv run ruff`. Independent of other in-flight changes; incorporates the sequential-backend
> forcing from the rapidgzip inner-tar fix (superset — see proposal).

## 1. Reproduce (red)

- [x] 1.1 Add detection tests for a `.tar.bz2` whose first bzip2 block exceeds the 4 KiB
      detection prefix (incompressible leading member): from a seekable `BytesIO`, and from a
      `PeekableStream`-wrapped non-seekable source (asserting the source is not consumed).
      Add a bare large-block `.bz2` (non-tar) test that must stay bare `BZ2` (no false
      positive). Confirm the two tar tests fail against the prefix-only probe.

## 2. Probe reads the actual source (green)

- [x] 2.1 `_probe_inner_tar` takes a `peek_more: Callable[[int], bytes]`. It decodes the
      peeked prefix first; on `TruncatedError` (the block-codec "need more input" signal) it
      reads up to `_INNER_TAR_MAX_PROBE_BYTES` (1 MiB) from the source and retries once.
      Force the sequential backend (`StreamConfig(streaming=True)`) so rapidgzip isn't engaged
      on the bounded prefix.
- [x] 2.2 Thread `peek_more` from `detect_format` (a closure over `_peek_prefix(source, …)`,
      which restores a seekable source's position and buffers a `PeekableStream`) through
      `_resolve_single_file_or_tar` to the probe. Applies uniformly to every single-file
      compressor (no bzip2 special-casing).
- [x] 2.3 `_INNER_TAR_MAX_PROBE_BYTES` sized/commented for a worst-case filled bzip2 level-9
      block (≤ ~904 KB compressed) with margin.

## 3. Verify

- [x] 3.1 New detection tests pass; existing inner-tar / deferred / peekable / seekable tests
      stay green.
- [x] 3.2 End-to-end: a large-block `.tar.bz2` opens via `open_archive` as a TAR with its
      members (not a single bare-`bz2` blob).
- [x] 3.3 `uv run pyrefly check` + `uv run ty check` + `uv run ruff` clean; full suite green.

## 4. Spec

- [x] 4.1 Update the `format-detection` "Compressed streams are probed for an inner TAR"
      requirement: the probe reads from the source up to one maximum block for a
      block-transform codec, bounded, without consuming the source; add bzip2 large-block
      scenarios (tar → `TAR_BZ2`, non-tar bare `.bz2` stays bare).
