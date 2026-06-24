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
| `magic: tuple[MagicSignature, ...]` (incl. `weak`) | backend `MAGIC` entries for stream codecs | detection |
| `content_probe: bool` (or a probe fn) | `CONTENT_PROBE_FORMATS` | detection |
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
  `MAGIC` / `CONTENT_PROBE_FORMATS` and the metadata extraction from the descriptors
  instead of hand-listing them. The reader becomes codec-agnostic: infer the member
  shell, then call `descriptor.extract_metadata(...)`.
- **`backend-registry`** computes a single-file format's tri-state support and install
  hint from the descriptor's `requirement`, dropping the parallel `_CODEC_REQUIREMENT`
  table. The compositional ZIP/7z/TAR-over-codec rules are unchanged — they already
  read codec availability through the same descriptors.

This is a **behavior-preserving refactor**: same detection results, same availability,
same metadata, same errors. It is explicitly *not* a place to change which library
backs each codec — that is the separate `compression-library-evaluation` change.

### Scope boundaries

- **Container formats stay separate.** ZIP/TAR/ISO/7z/RAR are *container* backends
  (`ReadBackend`), not stream codecs; they keep their own `MAGIC`/`EXTENSIONS` and are
  unaffected except that the detector now merges two well-defined sources (container
  backends + codec descriptors).
- **The filter-only codecs** (Delta, the BCJ family) are not standalone streams and get
  no detector/metadata fields — they remain coder-chain components (Phase 7).
- **No new codecs** and **no library swaps** here.

## Specs

Proposed deltas (kept here until accepted, per the "propose in `changes/`, don't edit
shipped specs ad hoc" rule). Authoritative specs touched:

### compressed-streams — ADDED Requirement: A codec is described by one StreamCodec descriptor

The system SHALL represent each single-stream codec as a single descriptor object that
carries its open function, exception translator, magic signatures (including the `weak`
flag), whether it is recognized by a content probe, its standalone file extensions, an
optional metadata extractor that fills `ArchiveMember` fields, and its optional-dependency
requirement (package / extra / external tool + install hint + unlocked capability). A new
standalone codec SHALL become fully readable and detectable by registering one descriptor,
without edits to the detector, the single-file reader, or the registry's availability code.

#### Scenario: adding a standalone codec is a one-descriptor change

- **WHEN** a new single-stream codec descriptor is registered (open fn, translator, magic/probe, extension, requirement)
- **THEN** `detect_format()` recognizes it, `SingleFileBackend` reads it as a one-member archive, and `format_availability()` reports its support — with no other code changes

### format-detection — MODIFIED Requirement: Magic/extension/probe tables are aggregated from backends *and* codec descriptors

The detector SHALL build its magic, extension, and content-probe tables from two data
sources — the container format backends (`ReadBackend.MAGIC`/`EXTENSIONS`) and the
stream-codec descriptors — with no per-format `detect()` logic in either. Stream-codec
magic/probe rows that were previously declared on `SingleFileBackend` SHALL come from the
descriptors instead, with identical results.

### backend-registry — MODIFIED Requirement: Codec availability + install hints come from the descriptor

A format's compositional support and its missing-component install hints SHALL be derived
from the codec descriptors' `requirement` fields, replacing the separate
`_CODEC_REQUIREMENT` table, so a codec's package/extra/hint is declared in exactly one
place.

### format-single-file-compressors — MODIFIED Requirement: Per-codec metadata comes from the descriptor

The single multi-format `SingleFileBackend` SHALL obtain each format's metadata extraction
from its codec descriptor's `extract_metadata` hook rather than a reader-local dispatch
table, keeping the reader codec-agnostic (the "one backend, per-codec hooks" structure is
preserved; only the hooks' home moves).

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
