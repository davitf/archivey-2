# Tasks — Unify per-codec logic behind a single StreamCodec descriptor

> Behavior-preserving refactor. Run tools through `uv` (`uv run pytest`,
> `uv run pyrefly check`, `uv run ty check`, `uv run ruff`). The existing
> detection / single-file / registry suites are the regression net — they must stay
> green **unchanged** (any required test edit is a signal the refactor changed behavior).

## 1. Define the descriptor

- [ ] 1.1 Add a `StreamCodec` descriptor in `internal/streams/codecs.py` with: `codec` /
      `stream_format`, `open`, `translate`, `magic: tuple[MagicSignature, ...]`,
      `content_probe` (flag or probe fn), `extensions`, `extract_metadata` (optional),
      and `requirement: MissingComponent | None`.
- [ ] 1.2 Build the descriptor registry from the current `_REGISTRY` + `_STREAM_FORMAT_CODECS`
      + the magic/probe/extension data currently on `SingleFileBackend` + the
      `_CODEC_REQUIREMENT` table. Keep `MissingComponent` where it lives (or move it to a
      neutral module to avoid a registry↔codecs import cycle).
- [ ] 1.3 Keep `Codec` / `resolve_codec` / `open_codec_stream` / `codec_for_stream_format`
      working as thin accessors over the descriptors (no caller churn outside the four files).

## 2. Route detection through the descriptors

- [ ] 2.1 `detect_format()` aggregates stream-codec magic + content probes from the
      descriptor registry, merged with the container backends' `MAGIC`/`EXTENSIONS`.
- [ ] 2.2 Confirm identical results: strong/weak magic ordering, the zlib weak+probe path,
      the Brotli magic-less probe, conflict warnings — all unchanged.

## 3. Route the single-file reader through the descriptors

- [ ] 3.1 `SingleFileBackend.FORMATS`/`EXTENSIONS`/`MAGIC`/`CONTENT_PROBE_FORMATS` derive
      from the descriptors (no hand-listed tables).
- [ ] 3.2 `SingleFileReader` calls `descriptor.extract_metadata(...)` instead of the local
      `_METADATA_HOOKS`; the gzip FNAME/mtime and xz/lzip size hooks move onto their
      descriptors. Member name inference + the one-member shape are unchanged.

## 4. Route registry availability through the descriptors

- [ ] 4.1 `format_availability()` reads a single-codec format's `requirement` from its
      descriptor; delete `_CODEC_REQUIREMENT`. The multi-codec container rules (ZIP/7z/TAR)
      read codec availability through the same descriptors.

## 5. Verify

- [ ] 5.1 `uv run pytest` green with **no changes** to the existing detection / single-file /
      registry / zip tests.
- [ ] 5.2 Add `tests/test_codec_descriptor.py`: register a synthetic descriptor and assert it
      is detectable, readable as a one-member archive, and availability-reported — with no
      edits elsewhere.
- [ ] 5.3 `uv run pyrefly check` + `uv run ty check` clean; `uv run ruff check` clean.
- [ ] 5.4 Zero-dep core still importable with no third-party packages (the descriptor
      registry must not eagerly import optional codec libs).
- [ ] 5.5 Sync the four spec deltas (compressed-streams, format-detection, backend-registry,
      format-single-file-compressors).
