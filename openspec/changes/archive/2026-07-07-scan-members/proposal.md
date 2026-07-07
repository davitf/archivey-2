## Why

A caller who opens a `streaming=True` reader (the only option for a non-seekable
source, e.g. a TAR arriving over a pipe) sometimes wants the **full, fully-resolved
member list** and *not* the member data — a plain "list everything" operation. Today
`members()` is disabled in streaming mode (correctly — it would consume the single
forward pass on scan-only formats but not on indexed ones, a format-dependent surprise),
and the documented fallback ("iterate discarding the members, then call
`get_members_if_available()`") does not actually work:

- The streaming `__iter__` / `stream_members()` paths go through
  `_register_progressively` and **never populate `_members_cache`**, so
  `get_members_if_available()` still returns `None` for a scan-only format after a full
  pass — contradicting its own spec ("available … after an iteration pass").
- Even conceptually, a single forward pass resolves only **backward-pointing** links;
  **forward-pointing symlinks stay unresolved**, so a bare iteration can never yield the
  same resolved objects that random-access `members()` produces.

So the one legitimate use case — "give me the resolved listing of a streaming archive" —
has no working, non-awkward path today.

## What Changes

- **Add `scan_members() -> list[ArchiveMember]`** to the reader surface: returns the
  **fully-resolved** member list (forward-pointing and true last-wins symlinks included)
  in *either* access mode. In random-access mode it is equivalent to `members()`. On a
  streaming reader it is the finish-and-resolve accessor — it returns the cache if the
  single forward pass already completed, otherwise **finishes that pass** (running it, or
  draining the remainder of an *interrupted* one) and returns the complete list. It is the
  only method allowed to run after an iteration method has started.
- **Fix streaming-pass materialization**: completing a streaming forward pass
  (`__iter__` / `stream_members` / `extract_all`, or via `scan_members`) now finalizes the
  fully-resolved member cache — running full link resolution (forward/last-wins symlinks,
  filled **in place** on the already-yielded objects per the mutable-member contract) and
  populating `_members_cache` — so `get_members_if_available()` returns that list
  afterward, as its spec already promises.
- **One forward pass only**: a streaming reader's source is traversed at most once.
  `__iter__` / `stream_members` / `extract_all` are the pass entry points; a second call to
  any of them raises `UnsupportedOperationError` — **including a second `__iter__` after the
  first completed** (there is no streaming cache-replay of `__iter__`). Rationale: a
  replayed pass would present forward-pointing symlinks as already-resolved at yield time,
  unlike the first pass — a state-dependent divergence. `scan_members()` is the exempt
  finish accessor; `get_members_if_available()` never begins or consumes the pass.
- **`get_members_if_available()` becomes strictly index-only** — no forward scan and **no
  member-data reads**. For a format whose link *targets* live in member data (a ZIP
  symlink's target is its file content), it returns members with `link_target` /
  `link_target_member` **unset**, whereas `members()` / `scan_members()` read what's needed
  to resolve them. This requires the ZIP backend to **stop eagerly reading symlink data
  during enumeration** (currently it does), reading the target lazily during resolution /
  link-following instead.
- **Clarify the listing-method semantics** for `members()`, `scan_members()`, and
  `get_members_if_available()` across the two access modes, before/during/after the pass,
  the three index topologies — **leading-index** (directory/ISO), **no-index** (TAR),
  **trailing-index** (ZIP, native 7z; index at EOF, reachable only on a seekable source) —
  and the resolved-vs-unresolved-links asymmetry.
- `members()` remains disabled in streaming mode (unchanged); it is **not** overloaded to
  consume — `scan_members()` is the explicit, greppable, cost-signalling entry point.

## Capabilities

### New Capabilities

<!-- none: this refines the existing reader surface -->

### Modified Capabilities

- `archive-reading`: add the `scan_members()` method to the member-listing surface;
  specify its cross-mode + finish-an-interrupted-pass behaviour and the post-pass
  materialization of the resolved member cache; make streaming `__iter__` single-use
  (qualify "subsequent `__iter__` returns from cache" as random-access only); state the
  index-only nature of `get_members_if_available()` and its possibly-unresolved links.
- `access-mode-and-cost`: add `scan_members()` to the access-mode × method table; specify
  the one-forward-pass rule (second `__iter__`/`stream_members`/`extract_all` raises, no
  replay) with `scan_members()` as the finish exception; make
  `get_members_if_available()` strictly index-only (no member-data reads), across
  leading-/no-/trailing-index formats, including the unresolved-links caveat for
  data-stored link targets.

## Impact

- **Code**: `src/archivey/internal/base_reader.py` (new `scan_members()`,
  instance-held single forward pass + `_register_progressively` finalization, one-pass
  guard on `__iter__`/`stream_members`/`extract_all`); `src/archivey/reader.py` (add
  `scan_members` to the `ArchiveReader` protocol); `src/archivey/internal/backends/tar_reader.py`
  (`_iter_with_data` pulls from the shared instance-held pass so `scan_members` can finish
  an interrupted one); `src/archivey/internal/backends/zip_reader.py` (defer symlink-target
  reads out of enumeration; read lazily on resolution / link-following). Future native
  7z/RAR streaming readers must route their forward pass the same way.
- **Public API**: additive (`scan_members()`); no breaking change. Behavioural changes:
  a *second* streaming forward pass now raises instead of silently re-reading (or failing
  opaquely), including a second `__iter__`; `get_members_if_available()` becomes strictly
  index-only (ZIP symlinks now list with unresolved links) and returns the materialized
  list after a completed streaming pass.
- **Docs/specs**: `ARCHITECTURE.md` §2.4 (member materialization) and the two modified
  capability specs; the access-mode × method table gains a `scan_members` row.
- **Tests**: `tests/test_tar.py` (streaming scan_members + post-pass
  get_members_if_available + second-pass rejection); ZIP/directory for the indexed and
  random-mode paths.
