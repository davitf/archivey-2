# Should `members()` / iterators include non-current members by default?

The maintainer's added question for this review: today `members()`, `__iter__`,
`scan_members`, and `stream_members` return **every** entry — superseded same-name
members (7z), RAR version-history rows (`path;n`), and 7z anti tombstones. Should
there be an include/exclude argument, and what would each choice cost?

## Recommendation up front

**Keep "everything" as the only behavior of the listing surfaces; do not add an
include/exclude argument.** Instead: (a) make `is_current` mean the same thing on
every backend (P1 — that's the actual coherence bug), (b) document the two-level
model in one place, and (c) let the existing selector/predicate machinery do the
filtering. The one-line filter is already expressible everywhere it's needed:

```python
[m for m in reader.members() if m.is_current]          # listing
reader.stream_members(lambda m: m.is_current)           # streaming
reader.extract_all(dest)                                # already skips non-current
```

## Why "everything" is the right default

1. **The reader models the archive, not the extracted tree.** An archive *is* an
   entry sequence; the final-state view is a derived artifact that `extract_all`
   already produces (hardwired `is_current` skip, `safe-extraction` spec). Listing
   faithfully and extracting effectively is a clean two-level model — and it's
   already the documented, spec'd design (`safe-extraction`'s visibility table:
   "members() / __iter__ / get → Visible (metadata + is_current=False)").
2. **Reference tools agree.** `7z l` lists superseded entries and anti items;
   `unrar l` lists `path;n` rows; `unzip -l`/`tar -t` list duplicates. A
   current-only default would make Archivey's listing disagree with every tool a
   user would cross-check against — and with its own `info.member_count`.
3. **The integrity job needs all entries.** `archivey test` and any digest audit
   must verify history payloads too (their bytes exist and can be corrupt).
   Defaulting them out of the iterators would silently weaken verification.
4. **`get()` already does the "current" thing** for the one case where names
   collide (returns the last = current for 7z; RAR history rows have distinct
   names, so `get("path")` is the live one by construction). Point lookup and
   listing already split along the right line.

## Why an `include_noncurrent=` argument specifically is a bad trade

- **It can't be honored uniformly — which is this review's whole theme.** In
  `streaming=True` TAR, last-entry-wins is unknowable until the pass ends: a
  forward-only iterator literally cannot exclude (or even mark) an entry that a
  later entry will supersede. The flag would be "supported on 7z/RAR/(indexed
  ZIP/TAR), approximate or erroring on streaming TAR" — a new per-format divergence
  shipped inside the API surface itself.
- **A boolean doesn't carve the space users actually mean.** Anti tombstones are
  `is_current=True` (they *are* the final state of the path — "absent"). So
  `members(current_only=True)` still yields ANTI entries a naive caller will trip
  on (`open()` raises), while hiding readable old payloads. The intuitive "just
  the files I'd get on disk" is really `m.is_current and not m.is_anti` — at which
  point it's a predicate, and the API already accepts predicates. A flag that
  needs three values (all / current / extractable-payload) is a smell that it
  shouldn't be a flag.
- **Bookkeeping alignment breaks.** `info.member_count`, `ListingLimits.max_members`,
  `member_id` density, `ExtractionReport` rows (which include `SKIPPED` rows for
  non-current members), and the CLI's counts all speak "entry space". A default
  exclusion creates two silently different cardinalities across surfaces that are
  documented to correspond.
- **Freeze asymmetry.** Shipping 0.2.0 *without* the flag costs nothing — callers
  filter with one expression, and a well-named keyword can be added compatibly
  later if demand shows up. Shipping it and regretting the default (or the shape)
  is a breaking change. Under freeze pressure, the reversible choice wins.

## What actually needs fixing for this model to hold (ties to P1/Q1)

The two-level model is only defensible if `is_current` is trustworthy — today it
isn't outside 7z/RAR:

1. **Compute last-entry-wins `is_current` in every random-access materialization**
   (ZIP central directory, TAR scan, and any future backend). Both see the full
   entry list before publishing, so it's a dict pass like
   `compute_is_current` (`sevenzip_parser.py:331`) — hang it on the shared
   materialization path in `base_reader.py` rather than per-backend.
2. **Streaming mode gets an honest, documented caveat**: in a forward-only pass
   `is_current` may be `True` at yield time for an entry later superseded (fields
   are already documented as late-stamped; the completed-pass cache in
   `scan_members()` can be back-filled). Extraction in streaming mode then handles
   same-name entries by overwrite-in-order (bsdtar semantics) — which is what the
   hardwired skip degrades to when knowledge arrives too late.
3. **Same-exact-name supersession stops being an O2 collision** (that's what makes
   ZIP/TAR duplicate extraction error today). O2 keeps its job for *distinct*
   stored names that collide after normalization (casefold/NFC) — a genuinely
   different, adversarial situation. Same-byte-name duplicates are the format's
   own versioning idiom and follow the `is_current` skip like 7z.
4. **Documentation**: one section ("Duplicates, versions, and tombstones") in
   `usage.md`/`formats.md` stating the model: *listing is the entry sequence;
   `is_current` marks the final state; extraction materializes final state;
   filter with a predicate when you want the current view.* Plus the one-line
   `is_current` filter added to the dedupe recipe.
5. **CLI follow-through** (with cli-product review): a listing marker for
   non-current entries (e.g. lowercase mark or a `;v` column) and a distinct mark
   for `ANTI`, so the model is visible in the first consumer.

## If the maintainer prefers current-only-by-default anyway

Then the least-bad shape is **not** an argument on `members()` but a distinct
accessor pair — e.g. `members()` (current view) vs `raw_members()`/`entries()`
(everything) — so each name has one meaning and `len()` comparisons across calls
can't silently diverge. Costs to accept, with eyes open: streaming TAR cannot
implement the current view (raise `UnsupportedOperationError` there?); `info.member_count`
needs a story (entry count vs current count); `archivey test` must switch to the
raw surface; anti entries need a decision (they are current); and `7z l` parity is
lost. This is strictly more surface and more per-format caveats than the
recommendation — which is why it isn't the recommendation.

→ Decision recorded as **Q2** in `QUESTIONS.md`.
