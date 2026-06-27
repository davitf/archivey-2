# Unify per-codec logic behind a single StreamCodec descriptor

## Why

The knowledge about a single-stream codec (gzip, bzip2, xz, zstd, lz4, lzip, zlib,
brotli, unix-compress) is currently **scattered across four modules**, so adding or
changing one codec means editing all of them:

- `internal/streams/codecs.py` — the open function + the exception translator +
  the optional-package sentinel (`is_codec_available`).
- the format backends (`formats/single_file_reader.py`, and later `tar_reader.py`) —
  the `MAGIC` signatures, `EXTENSIONS`, and `CONTENT_PROBE_FORMATS` declared as data.
- `formats/single_file_reader.py` — the per-codec **metadata hooks**
  (`_gzip_metadata`, `_sized_stream_metadata`, …).
- `internal/registry.py` — the `_CODEC_REQUIREMENT` table mapping a codec to its
  package / extra / install hint / unlocked capability.

`detect_format()` and `SingleFileReader` are *almost* format-agnostic (the Stage-2
review moved weakness into `MagicSignature` and probes into `CONTENT_PROBE_FORMATS`),
but the last per-format pieces — which codec a magic implies, what metadata to extract,
which library unlocks it — still live in three different places. A reviewer asked for a
single descriptor that encapsulates all of it (PR #13 review), so that detection and the
single-file reader become **fully data-driven** and a new standalone codec is "add one
descriptor", not "touch four files".

## What Changes

Introduce a single **`StreamCodec` descriptor** (working name) in the
`compressed-streams` layer — one per single-stream codec — bundling everything the
rest of the library needs to know about it:

| Field | Replaces | Used by |
|-------|----------|---------|
| `codec` / `stream_format` | `Codec`, `_STREAM_FORMAT_CODECS` | everywhere |
| `open(source, params, config)` | `_CodecSpec.open` | `open_codec_stream` |
| `translate(exc)` | `_CodecSpec.translate` | `ArchiveStream` |
| `magic: tuple[MagicSignature, ...]` (exact only) | backend `MAGIC` entries for stream codecs | detection |
| `content_probe: Callable[[bytes], bool] \| None` | `CONTENT_PROBE_FORMATS` + the detector's generic `_content_probe` + the `weak` magic flag | detection |
| `extensions: tuple[str, ...]` | backend `EXTENSIONS` for stream codecs | detection / naming |
| `extract_metadata(reader_ctx, member)` | `SingleFileReader._METADATA_HOOKS` | single-file reader |
| `requirement: MissingComponent \| None` | `registry._CODEC_REQUIREMENT` + `is_codec_available` sentinel | registry availability |

A single descriptor registry (keyed by `Codec` / `StreamFormat`) is the source of
truth. Then:

- **`detect_format()`** aggregates stream-codec magic + content probes **from the
  descriptors**, alongside the *container* magics the format backends still declare
  (ZIP's `PK..`, TAR's `ustar`, ISO's `CD001`). Container vs. stream-codec detection
  stays one combined table; only the source of the stream-codec rows moves.
- **`SingleFileBackend` / `SingleFileReader`** derive `FORMATS` / `EXTENSIONS` /
  `MAGIC` / `CONTENT_PROBES` and the metadata extraction from the descriptors
  instead of hand-listing them. The reader becomes codec-agnostic: infer the member
  shell, then call `descriptor.extract_metadata(...)`.
- **`backend-registry`** computes a single-file format's tri-state support and install
  hint from the descriptor's `requirement`, dropping the parallel `_CODEC_REQUIREMENT`
  table. The compositional ZIP/7z/TAR-over-codec rules are unchanged — they already
  read codec availability through the same descriptors.

As part of making the descriptor the single source of a codec's recognition, the
`content_probe` is the **actual probe function** (not a bool flag), and the two ways a
single-file codec was recognized are unified into one: the `weak` `MagicSignature` flag is
**removed**, and zlib — its only user — moves to a `content_probe` that gates on its 2-byte
CMF/FLG header before decoding (the same decode-a-prefix mechanism Brotli already used).
Detection becomes: exact magic, then content probes, then extension.

This is **observably behavior-preserving**: same detected formats, confidence, and
`detected_by`; same availability, metadata, and errors. The *mechanism* by which zlib is
recognized changes (probe instead of weak-magic + probe), but every detection outcome is
identical. It is explicitly *not* a place to change which library backs each codec — that
is the separate `compression-library-evaluation` change.

### Scope boundaries

- **Container formats stay separate.** ZIP/TAR/ISO/7z/RAR are *container* backends
  (`ReadBackend`), not stream codecs; they keep their own `MAGIC`/`EXTENSIONS` and are
  unaffected except that the detector now merges two well-defined sources (container
  backends + codec descriptors).
- **The filter-only codecs** (Delta, the BCJ family) are not standalone streams and get
  no detector/metadata fields — they remain coder-chain components (Phase 7).
- **No new codecs** and **no library swaps** here.

## Specs

The full delta requirements (with scenarios) live in this change's `specs/` directory and
are what `openspec validate` checks:

- `specs/compressed-streams/spec.md` — **ADDED** "A codec is described by one StreamCodec descriptor".
- `specs/format-detection/spec.md` — **MODIFIED** magic/extension/probe tables aggregated from backends *and* codec descriptors; the content probe is a per-format function; the `weak` magic flag is removed and zlib is recognized by a content probe (magic-byte table + content-probe requirements updated accordingly).
- `specs/backend-registry/spec.md` — **MODIFIED** codec availability + install hints come from the descriptor (drop `_CODEC_REQUIREMENT`).
- `specs/format-single-file-compressors/spec.md` — **MODIFIED** per-codec metadata comes from the descriptor.

All four preserve observable behavior (same detection outcomes, availability, metadata,
errors); they reword *where the per-codec data lives* and unify the two single-file
recognition paths into one function-based content probe.

## Impact

- **Affected code:** `internal/streams/codecs.py` (the descriptor + registry),
  `internal/detection.py` (aggregate from descriptors), `formats/single_file_reader.py`
  (derive tables + metadata from descriptors), `internal/registry.py` (availability via
  descriptors; drop `_CODEC_REQUIREMENT`).
- **Spec deltas:** `compressed-streams`, `format-detection`, `backend-registry`,
  `format-single-file-compressors` (all behavior-preserving rewordings of "where the data
  lives").
- **Tests:** existing detection / single-file / registry suites must stay green
  unchanged (the refactor asserts no behavior change); add a small test that a synthetic
  descriptor becomes detectable + readable + availability-reported with no other edits.
- **Depends on / coordinates with:** `compression-library-evaluation` — that change may
  change which library a descriptor's `open`/`translate` point at, but the descriptor
  shape defined here is where those choices live.
- **Risk:** breadth of the touch. Mitigated by keeping it strictly behavior-preserving
  and leaning on the existing green suites as the regression net.
