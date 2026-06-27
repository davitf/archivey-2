# Tasks — Unify per-codec logic behind a single StreamCodec descriptor

> Behavior-preserving refactor. Run tools through `uv` (`uv run pytest`,
> `uv run pyrefly check`, `uv run ty check`, `uv run ruff`). The existing
> detection / single-file / registry suites are the regression net — they must stay
> green **unchanged** (any required test edit is a signal the refactor changed behavior).

## 1. Define the descriptor

- [x] 1.1 Add a `StreamCodec` descriptor in `internal/streams/codecs.py` with: `codec` /
      `stream_format`, `open`, `translate`, `magic: tuple[MagicSignature, ...]`,
      `content_probe` (flag or probe fn), `extensions`, `extract_metadata` (optional),
      and `requirement: MissingComponent | None`.
- [x] 1.2 Build the descriptor registry from the current `_REGISTRY` + `_STREAM_FORMAT_CODECS`
      + the magic/probe/extension data currently on `SingleFileBackend` + the
      `_CODEC_REQUIREMENT` table. Keep `MissingComponent` where it lives (or move it to a
      neutral module to avoid a registry↔codecs import cycle).
      → Moved `MissingComponent` to `internal/types.py` (re-exported from `registry`) to
      break the registry↔codecs cycle; `_DESCRIPTORS` replaces `_REGISTRY`.
- [x] 1.3 Keep `Codec` / `resolve_codec` / `open_codec_stream` / `codec_for_stream_format`
      working as thin accessors over the descriptors (no caller churn outside the four files).

## 2. Route detection through the descriptors

- [x] 2.1 `detect_format()` aggregates stream-codec magic + content probes from the
      descriptor registry, merged with the container backends' `MAGIC`/`EXTENSIONS`.
      → `SingleFileBackend.MAGIC`/`CONTENT_PROBE_FORMATS`/`EXTENSIONS` now derive from the
      descriptors, which the registry's live `magic_entries()` aggregation already reads.
- [x] 2.2 Confirm identical results: magic ordering, the zlib probe path, the Brotli probe,
      conflict warnings — all unchanged. Full detection suite green unchanged.
- [x] 2.3 Make `content_probe` the actual probe **function** (not a bool) and unify the two
      single-file recognition paths: remove the `weak` `MagicSignature` flag, move zlib to a
      `content_probe` that gates on its CMF/FLG header before decoding, and move the
      decode-a-prefix primitive onto the codec layer. `ReadBackend.CONTENT_PROBE_FORMATS` →
      `CONTENT_PROBES` ((format, probe) pairs); `detect_format()` runs exact magic → probes →
      extension. Observable detection outcomes unchanged.

## 3. Route the single-file reader through the descriptors

- [x] 3.1 `SingleFileBackend.FORMATS`/`EXTENSIONS`/`MAGIC`/`CONTENT_PROBE_FORMATS` derive
      from the descriptors (no hand-listed tables).
- [x] 3.2 `SingleFileReader` calls `descriptor.extract_metadata(...)` instead of the local
      `_METADATA_HOOKS`; the gzip FNAME/mtime and xz/lzip size hooks move onto their
      descriptors. Member name inference + the one-member shape are unchanged.
      → The hooks take a `MetadataContext` (peek_header + probe_decompressed_size) so the
      codec layer needs no dependency on the reader.

## 4. Route registry availability through the descriptors

- [x] 4.1 `format_availability()` reads a single-codec format's `requirement` from its
      descriptor; delete `_CODEC_REQUIREMENT`. The multi-codec container rules (ZIP/7z/TAR)
      read codec availability through the same descriptors.
      → Via `codec_requirement(codec)`; `is_codec_available()` reads the descriptor + live
      `_OPTIONAL_SENTINELS`.

## 5. Verify

- [x] 5.1 `uv run pytest` green with **no changes** to the existing detection / single-file /
      registry / zip tests. (467 pre-existing tests pass unchanged.)
- [x] 5.2 Add `tests/test_codec_descriptor.py`: register a synthetic descriptor and assert it
      is detectable, readable as a one-member archive, and availability-reported — with no
      edits elsewhere.
- [x] 5.3 `uv run pyrefly check` + `uv run ty check` clean; `uv run ruff check` clean.
- [x] 5.4 Zero-dep core still importable with no third-party packages (the descriptor
      registry must not eagerly import optional codec libs).
      → Descriptors store function references only; sentinels are lazy lambdas (the existing
      `_optional()` pattern), so registry-build adds no eager optional import.
- [x] 5.5 Sync the four spec deltas (compressed-streams, format-detection, backend-registry,
      format-single-file-compressors). (Deltas match the implementation as written.)
