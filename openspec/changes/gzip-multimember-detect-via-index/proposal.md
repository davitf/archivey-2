# Detect multi-member gzip via rapidgzip's index instead of a second full-file scan

## Why

When the ISIZE backstop sees a length mismatch, `_GzipTruncationCheckStream` disambiguates
"real truncation" from "valid concatenated multi-member gzip" with `_has_additional_gzip_member`
— a **second pass over the whole file**, scanning 1 MiB blocks for a further `1f 8b 08` header:

```python
def _has_additional_gzip_member(self) -> bool:
    ...
    return gzip_has_additional_member(f)   # O(file size) I/O
```

That scan runs on the two **hot** paths, not rare ones:

- **Every valid multi-member gzip read to EOF.** ISIZE records only the *last* member's size,
  so `total % 2**32 != isize` is guaranteed for a valid ≥2-member file → mismatch → full scan
  (short-circuits at the first magic, but that is ~16 MiB apart in compressed data on average).
- **Every truncated single-member raise.** There is no second magic to find, so the scan reads
  the **entire file** before concluding "no further member → raise `TruncatedError`."

But rapidgzip has **already scanned the whole stream** and built an index (block/member offsets)
to serve random access. Asking that index "are there ≥2 gzip members?" answers the exact
question the byte scan answers, with **zero extra I/O**.

**The soundness is clean because of *what* the check protects.** The scan exists only to avoid
false-flagging a **valid** multi-member file. For a valid, complete file rapidgzip's index is
**complete and trustworthy** — the "`block_offsets_complete` is not trustworthy on truncated
input" caveat applies only to the truncated case, where we intend to raise anyway and a genuinely
truncated single-member file has no real second member for a partial index to miss. So the
index is authoritative exactly where we rely on it (maintainer-confirmed reasoning).

## What Changes

- **`seekable-decompressor-streams`** — MODIFY "Accelerator errors translate uniformly": the
  multi-member disambiguation for the rapidgzip gzip backstop SHALL prefer rapidgzip's
  already-built index (gzip member / stream boundaries) over a second full-file magic scan,
  falling back to the byte scan only when the index is unavailable or does not expose member
  boundaries. No detection/format change; no new public surface.
- Same conservative direction preserved: never false-flag a valid file (the only permissible
  error stays a *missed* truncation, never a false positive).

This change is **investigation + specs + implementation**: it must first confirm rapidgzip's
index actually distinguishes gzip *member* boundaries from deflate *block* boundaries (see
`design.md`); the swap lands once that holds.

## Specs

Proposed delta in `specs/seekable-decompressor-streams/spec.md` (kept here until accepted).
Sibling change `gzip-truncation-backstop-any-seekable` modifies the same requirement — sequence /
rebase the deltas when both land. The per-member ISIZE **sum** deferred by
`rapidgzip-truncation-investigation` needs the same member-boundary data, so it should build on
this change's index accessor rather than re-deriving boundaries.
