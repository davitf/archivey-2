# Ergonomics — the three canonical loops, judged via the CLI

The merged CLI is the first real second consumer. Method: trace every place it
reached past the public surface or built machinery the library could have offered.
Overall: **the CLI validates the API** — `list` is a 40-line module, extraction maps
flags→enums 1:1, the TTY password prompt fell out of `PasswordProvider` in 10 lines.
Three real gaps (E1–E3) and some small friction below.

## E1 (Medium) — measurement/IO accounting is public in behavior, internal in API

`--track-io` needs: `enable_measurement()` (context manager) and three counters —
`bytes_decompressed`, `compressed_bytes_consumed`, `source_seek_count`. All live on
internal surfaces, so the CLI does this (`cli/common.py:15-16, 56-68`):

```python
from archivey.internal.base_reader import BaseArchiveReader
from archivey.internal.measurement import enable_measurement
...
if not isinstance(reader, BaseArchiveReader):
    print("track-io: counters unavailable for this reader", ...)
```

An `isinstance` check against an internal class, guarding properties that have full
docstrings (`base_reader.py:557-1009`) — these counters are *designed* as public
observables (they're the runtime companion to `CostReceipt`, VISION's "honest cost
signals") but never got a public home. Any downstream tool wanting "how much did that
cost me?" must copy this pattern.

Recommendation: promote a minimal read side to the ABC — e.g. a single
`reader.io_stats() -> IoStats | None` (frozen dataclass with the three fields;
`None` when measurement was off) plus a public `archivey.enable_measurement()`.
Alternatively fold enablement into `ArchiveyConfig` (a `measure_io: bool`) so no
context manager is needed. Either is small; pre-release cost: free. Leaving it
internal is also *defensible* (measurement is a diagnostic tool), but then the CLI —
shipped in the same wheel — is permanently an internal-API consumer, which normalizes
the pattern for everyone reading its source as example code.

## E2 (Medium) — the "verify" job has no library primitive

`archivey test` is the third canonical CLI verb, and it required the most delicate
code in the whole CLI (`cli/test_cmd.py:56-73`): a manual `iter()`/`next()` loop so
that open-time failures count as FAIL, with this comment:

> Once the generator raises, further next() yields StopIteration — remaining members
> are lost (library limitation for solid / poisoned streams).

Two library-side observations:

1. **A full-read integrity check is a first-class job** (VISION: damaged input is a
   first-class citizen; stored digests verify on read). Today it's ~70 lines of
   caller code with subtle generator semantics. A `reader.verify(members=..., on_progress=...)
   -> VerifyReport` (or an `on_error=CONTINUE` mode for `stream_members`) would make
   the CLI verb a formatter — and give library users the same capability, which none
   of them will hand-roll correctly (the StopIteration-after-raise trap is real).
2. **`stream_members` has no per-member error recovery.** `extract_all` has
   `OnError.CONTINUE`; the streaming iterator has nothing, so one bad member header
   ends the pass even where the backend could skip to the next member (non-solid
   formats). If a verify primitive lands, it needs exactly this, so the design
   question is shared. (For solid streams, losing the rest is honest — the report
   should say so, which is what a `VerifyReport` could carry.)

Not a 0.2.0 blocker (the API *shape* wouldn't change, only grow), but flagged because
the CLI proves the gap with working code today. → Q5.

## E3 (Low) — `ExtractionStatus.SKIPPED` means two different things

`SKIPPED` is recorded both for "destination existed under `OverwritePolicy.SKIP`"
(`extraction_types.py:84`) and for "member is non-current" (`extraction.py:351-354`).
A report consumer (like the CLI's summary, which prints `skipped: <name>` for both)
can only distinguish by re-deriving `result.member.is_current`. **Decided (Q6): split into distinct statuses** (not a `reason` field) — overwrite-skip
vs non-current-skip are different caller concerns. Name at implement
(`SUPERSEDED` / `NON_CURRENT` / …). (The third skip-ish concept — user-filter skip —
produces *no* result row, which is defensible but worth one docstring sentence on
`extract_all`.)

## The three canonical jobs, walked

### 1. List + hash for dedupe (the founding use case)

```python
with archivey.open_archive(path) as reader:      # 1 import
    for member in reader:
        if member.is_file and "crc32" in member.hashes: ...
```

Two lines of ceremony; stored-vs-computed fallback documented as a recipe in
`usage.md` with the per-format matrix one link away. **Discoverability is good** —
`hashes` is on the dataclass with a docstring naming the key convention. Two notes:

- `hashes` is `Mapping[str, int | bytes]` today (crc32 int, blake2sp bytes; string
  keys, **no** algorithm enum) — every consumer needs a formatting/normalizing
  branch (the CLI grew `format_hash_value`, `cli/format.py:46-49`). **Decided
  (Q6 typing): `Mapping[HashAlgorithm, bytes]`** — add the enum (`CRC32` /
  `BLAKE2SP` / `ADLER32`); crc32 becomes 4-byte `bytes`. **Filling** zlib Adler /
  multi-lzip CRC: separate OpenSpec change `surface-stored-stream-digests`.
- The recipe iterates *all* members — including superseded 7z revisions and RAR
  history rows, silently hashing dead content. After Q1/Q2, add one `is_current`
  line to the recipe (see `members-scope.md`).

### 2. Safe extract with a policy

`extract(src, dest)` is genuinely one line with safe defaults; the enum cluster maps
cleanly (CLI: `ExtractionPolicy(policy)` straight from argparse strings — the
str-valued enums pay off). `ExtractionReport` iterating as its results tuple keeps
the common loop clean. The five-name cluster is coherent (see SUMMARY "actually
fine"). Frictions: E3 above, and P1 — under defaults, a duplicate-name tar/zip
*fails*, which is exactly the sort of surprise the safe-by-default posture is
supposed to avoid (safety against attack, not against `tar -rf`).

### 3. Open one member and stream it

`open(name_or_member)` → `ArchiveStream`: `KeyError` for unknown names (stdlib-ish),
`ArchiveyUsageError` for foreign members, usage-error for non-file types,
`stream.size` and lazy `seekable()` — coherent, spec-backed, and the identity rules
prevent real bugs. The `try_get_size` story surfaces publicly as `ArchiveStream.size`
(`archive_stream.py:168`) — fine. The one thing a caller must learn as lore:
check `member.type`/`is_file` *before* `open()` (non-file raises); the docs say it,
and `stream_members`' `None`-for-non-file convention covers the loop case.

### Mutability of `ArchiveMember`

"Mutable; callers must treat as read-only" + unhashable-by-design + late-stamped
fields (streaming link resolution) + `replace()` for filter rewrites. The contract is
spelled out in `archive-data-model` and the docstrings; `ExtractionResult` even
documents that `member` stays live-mutable inside a frozen result. This is about as
honest as a mutable model gets — fine for freeze. One residual: "using a reader after
`close()` is undefined" (`reader.py:171`) — members outlive the reader as plain data;
one sentence in `api.md` saying *that* ("fields already stamped remain valid; lazy
resolution stops") would close the loop.

## Config & error ergonomics (brief §D)

- Defaults are safe (`STRICT`, `ERROR`, limits on, `AUTO` accelerators with silent
  fallback) and each knob is orthogonal; `ExtractionLimits.UNLIMITED` /
  `ListingLimits.UNLIMITED` make opt-out explicit and greppable. Good freeze shape.
- `extract_all(config=..., limits=...)` — `limits` overrides `config.extraction_limits`
  for one call; slight overlap but documented in the docstring and genuinely useful.
  Keep.
- The error tree lets callers catch at the granularity they need; the CLI's whole
  error policy is two `except` clauses (`ArchiveyError` → 1, `OSError` → 1) plus
  usage errors propagating as bugs — which is the design working as intended.
  `ExtractionResult.error: ArchiveyError | OSError | None` honestly reflects that
  filesystem write errors are not translated (per CONTRIBUTING); documented inline.

## Minor CLI-revealed nits (rounded up)

- `cli/progress.py:9` and `cli/test_cmd.py:14` import `ExtractionProgress` from
  `archivey.internal.extraction_types` although it's re-exported at top level — the
  internal path is what IDE auto-import suggests because that's where the class
  *lives*. Moving public extraction types out of `internal/` (e.g. an
  `archivey/extraction.py` that internal code imports too) would stop tools from
  learning the wrong path. Cosmetic but cheap now, annoying later.
- `ExtractionProgress` reused as the progress payload for `test` (a non-extraction
  op) — field names like `bytes_written` read oddly for a verify. Not worth a rename
  alone; if E2's `verify` lands, give it the same dataclass under a neutral name
  (`MemberProgress`) and alias the old one. Decide with E2, not separately.
- `_TYPE_MARK` (`cli/format.py:9-15`) has no entry for `MemberType.ANTI` → anti
  members print `"?"`, indistinguishable from `OTHER`; no listing marker for
  `is_current=False` either (see `members-scope.md` — the CLI currently renders two
  identical `a.txt` lines for a versioned 7z). CLI-product territory, but the fix
  follows Q2's decision.
