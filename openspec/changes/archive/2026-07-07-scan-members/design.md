## Context

The reader exposes ways to obtain the member list:

- `members() -> list[ArchiveMember]` — materialize the full, fully-resolved list;
  disabled in streaming mode.
- `scan_members() -> list[ArchiveMember]` — **(new)** the fully-resolved list in either
  mode; on a streaming reader it finishes the single forward pass (running it, or
  completing an interrupted one) and returns the resolved list.
- `get_members_if_available() -> list[ArchiveMember] | None` — a no-scan, no-consume,
  **index-only** peek; returns the list only when it is already reachable, else `None`.
- `__iter__` / `stream_members()` / `extract_all()` — the single forward pass (metadata,
  metadata+data, or extraction).

The gap (see proposal): there is no working way to get the **fully-resolved** listing of a
`streaming=True` reader without also consuming member data, because (a) `members()` is
disabled, (b) the streaming pass never populates `_members_cache`, and (c) a single pass
leaves forward-pointing symlinks unresolved. This change adds `scan_members()` and fixes
the streaming-pass materialization.

Two structural facts drive the design:

- **Members are mutable and filled in place** (ARCHITECTURE §2.1). A member yielded early
  in a streaming pass can have `link_target_member` (and `size`/CRC) filled *later* on the
  same object the caller holds. So end-of-pass forward-link resolution updates objects the
  caller already received — no re-fetch.
- **Streaming already retains all member *metadata*.** `_register_progressively` builds a
  `by_name_lists` map referencing every member for backward-link resolution, so the O(1)
  streaming-memory guarantee is about member *data*, not the metadata objects. Collecting
  the resolved list at end-of-pass therefore costs nothing beyond what is already held.

### Index topology (one of the three axes)

Where a format's member list lives determines what is reachable *without a forward data
scan and without reading member data*, which is exactly the `_MEMBER_LIST_UPFRONT`
predicate behind `get_members_if_available()`:

| Topology | Formats (v2) | List reachable index-only? | `_MEMBER_LIST_UPFRONT` |
|---|---|---|---|
| **Leading-index** | directory, ISO | Yes — the listing is at/near the front (or is the filesystem itself). | `True` |
| **Trailing-index** | ZIP, native 7z | Yes, **but only by seeking to the end** (central directory / 7z header at EOF). | `True` (backend guarantees a seekable source via `REQUIRES_SEEK`) |
| **No-index** | TAR (plain & compressed) | No — the list exists only after walking every 512-byte header. | `False` |

The trailing-index row carries a subtlety: a non-`None` `get_members_if_available()` for
ZIP/7z **presupposes a seekable source**. Those backends set `REQUIRES_SEEK = True` and do
**not** set `SUPPORTS_STREAMING_NON_SEEKABLE`, so even a `streaming=True` ZIP is opened
over a seekable source and its trailing index is reachable without touching the forward
member-data stream. A hypothetical future format with a trailing index that *also* allowed
non-seekable streaming would have to report `_MEMBER_LIST_UPFRONT = False` on such a
source, because on a pipe its index is unreachable without consuming everything. TAR is the
only backend today that streams over a non-seekable source, and it is no-index, so the
static class-level flag is correct for all current backends; the requirement is stated at
the source-capability level so it stays correct as backends are added.

### Where link targets live (the other resolution axis)

Link resolution needs the target *string* (`link_target`) before it can find the target
*member* (`link_target_member`). Formats store that string in different places:

- **In the header/index** (TAR `linkname`): available during metadata enumeration, no
  member-data read.
- **In the member's data** (a ZIP symlink's *content* is its target path; the central
  directory only flags "this is a symlink" via the Unix mode bits): obtaining it requires
  **opening and reading the member**.

This is orthogonal to index topology: ZIP has a trailing *index* (names/metadata are
cheap) yet its symlink *targets* are in data. So an index-only listing can enumerate ZIP
members but cannot know their link targets without reading each symlink's bytes.

## Goals / Non-Goals

**Goals:**

- One explicit, mode-agnostic method to obtain the fully-resolved member list
  (`scan_members()`), usable before, during-then-interrupted, or after the forward pass.
- Make a completed (or `scan_members()`-completed) streaming pass materialize the resolved
  cache, so `get_members_if_available()` returns it afterward.
- A crisp, index-only contract for `get_members_if_available()`, with the
  resolved-vs-unresolved-links distinction stated explicitly.
- Preserve the no-surprise rule: no listing operation may have *format-dependent*
  consequences that bite a caller who tested on one format and ran on another.

**Non-Goals:**

- Changing `members()`'s streaming prohibition (it stays disabled; not overloaded).
- Reordering/​buffering iteration to yield resolved members mid-pass (rejected — see
  Decision 6).
- Implicit buffering / spooling of a non-seekable source to enable random access
  (explicitly refused by `access-mode-and-cost`).
- Bounding *metadata* memory for huge member counts (a no-member-cache mode is a separate,
  deferred effort — see phase-5 design Non-Goals).

## Decisions

### 1. `scan_members()` — a dedicated, mode-agnostic finish-and-resolve accessor

Signature: `def scan_members(self) -> list[ArchiveMember]`. Returns the **fully-resolved**
list (same objects/resolution as random-access `members()`, incl. true last-wins symlinks
and forward-pointing symlinks), callable in either mode.

- **Random-access mode**: equivalent to `members()` — materialize (scan if needed),
  resolve all links, cache, return. Non-consuming.
- **Streaming mode**: return the cache if the pass already completed; otherwise **finish
  the single forward pass** — run it from the start, or continue an *interrupted* one from
  where it stopped, draining the underlying metadata scan to EOF — then resolve all links,
  cache, and return. `scan_members()` is the **only** method allowed to run after an
  iteration method has started (see Decision 3).

**Why a new method, not `members()`-consumes or `members(consume=True)`:** the no-surprise
rule targets *format-dependent consequences of the same call*. If `members()` silently
consumed, it would burn the pass on TAR but not on ZIP, so `list-then-stream` would work on
the format you tested and break on the one you didn't. A boolean flag has the same mental
collision (`members()` means "safe, re-iterable" in random mode) plus the boolean-trap
ergonomics. A distinct name is greppable, self-documenting, and carries the cost warning at
the call site.

**Why mode-agnostic (works in random mode too):** a consumer writing generic code ("give
me all members regardless of how this was opened") gets one call with no `if streaming:`
branch. The only behavioural difference (finishes the pass) is tied to a mode the caller
*explicitly chose*, not to a hidden format property — acceptable under the no-surprise
rule. (Alternative — streaming-only `scan_members()`, forcing random callers to use
`members()` — rejected: needless branching for the common "just list it" case.)

**Naming:** `scan_members()`. "Scan" is already the codebase's word for the full header
walk (`ListingCost.REQUIRES_SCANNING`, "may trigger a full scan"), so it is honest and
consistent. Deliberately *not* an alarming name: for a non-seekable stream this is the
*correct and only* tool for a listing, and a scary name would punish the legitimate use.
The docstring carries the "finishes / consumes the one forward pass" note. (Alternatives
considered: `read_all_members`, `list_members` — less consistent with existing vocabulary.)

### 2. Post-pass materialization lives in `_register_progressively`

All streaming forward passes funnel through `_register_progressively` (base `__iter__`;
TAR's `_iter_with_data`; and, by contract, future streaming backends' `_iter_with_data`).
That is the single natural finalization point.

`_register_progressively` already stamps ids, resolves backward links incrementally, and
retains every member in a `by_name_lists`. Change it to accumulate into **instance** state
and, **on normal exhaustion of the generator** (the consumer drained it, or `scan_members`
drove it to EOF), run the full `_resolve_link` over every link member — resolving
forward-pointing symlinks, true last-wins symlinks, and full chains, filling
`link_target_member` **in place** on the objects already yielded — then set
`self._members_cache` and `self._members_by_name_lists`.

Generators run their post-loop tail only when fully consumed, so an *early break* (an
interrupted pass) does **not** finalize — correct: a partial pass must not present itself
as a complete listing. `scan_members()` finishes an interrupted pass by draining the *same*
instance-held generator to exhaustion, which reaches this tail. Because finalization writes
in place, a caller who collected members during the pass sees their forward links become
resolved once it completes.

```python
# base_reader.py — illustrative
def _register_progressively(self, members):
    self._by_name_lists = {}
    self._scanned = []
    for idx, member in enumerate(members):
        ...  # stamp id, resolve backward links (unchanged), index name, append
        yield member
    # Reached only when the pass is drained to EOF (by the consumer or scan_members).
    for m in self._scanned:
        if m.is_link and m.link_target:
            self._resolve_link(m, self._by_name_lists)   # forward/last-wins/chains, in place
    self._members_cache = self._scanned
    self._members_by_name_lists = self._by_name_lists
```

**Mechanics note (single underlying scan).** So that `scan_members()` can *continue* an
interrupted `__iter__`/`stream_members`, the streaming pass must be a single
**instance-held** iterator over the backend's forward metadata scan; `__iter__` yields from
it, `stream_members`/`extract_all` pull members from it and open member data alongside, and
`scan_members()` drains whatever remains of it. TAR's `_iter_with_data` today builds its own
`_register_progressively(...)` locally; it must be adjusted to pull from the shared
instance-held pass so all four entry points share one cursor and one finalization. This is
an explicit contract for future streaming backends (native 7z/RAR).

### 3. One forward pass only; `scan_members()` is the finish exception

A streaming reader's source is traversed **at most once**, forward. Add
`self._forward_pass_started: bool = False`.

- `__iter__`, `stream_members`, and `extract_all` each check the flag **before** touching
  the source; if a pass has already started, they raise `UnsupportedOperationError`. The
  first of them to run sets the flag and owns the pass. This holds even after a *completed*
  pass — there is **no cache-replay of `__iter__`** in streaming mode (see below).
- `scan_members()` is exempt: it may run before (initiating + finishing the pass),
  after-an-interruption (finishing it), or after-completion (returning the cache). When it
  initiates the pass it sets the flag too.
- **`get_members_if_available()` never begins or advances the pass** and never sets the
  flag. It reads the index (a seek, for a leading/trailing-index format) or returns the
  cache/`None`.

**Why one pass only — no `__iter__` replay after completion.** During a live pass, a
forward-pointing symlink is *unresolved at the moment it is yielded* (the pass hasn't seen
its target yet); finalization fills it at EOF. A replayed second `__iter__` would present
that same member as *already resolved at yield time*, so "iterate the reader" would
observably behave differently depending on how many times you had done it — precisely the
format-/state-dependent divergence the library avoids. Making iteration strictly single-use
removes the divergence: there is exactly one yield-time observation per member, and the
resolved snapshot is obtained through the *different, clearly-named* `scan_members()` /
`get_members_if_available()` methods. (This qualifies `archive-reading`'s "subsequent
`__iter__` returns from the cache" as **random-access mode only**.)

This guard is new behaviour: today a second streaming pass is not explicitly rejected (it
would silently re-read and fail opaquely on a consumed pipe). Making it a uniform, explicit
error is the precondition that lets `scan_members()` be the single well-defined
post-iteration accessor.

### 4. `get_members_if_available()` is strictly index-only

`get_members_if_available()` performs **no member-data reads and no forward scan**. It
returns the list only from a true upfront index (leading/trailing topology) or an
already-materialized cache; otherwise `None`. Consequences:

- For a no-index format (TAR) it returns `None` until the pass has completed (or
  `scan_members()`/`members()` materialized the cache), then the resolved cache.
- For a format whose **link targets live in member data** (ZIP symlinks), an index-only
  listing returns members with `link_target` / `link_target_member` **unset** — it will not
  read symlink bytes to resolve them. `members()` and `scan_members()` *do* perform those
  reads and return resolved links.

**ZIP backend change.** Today `ZipReader._to_member` eagerly opens and reads every
symlink's data during enumeration to populate `link_target`, so a ZIP listing is not
actually index-only. Defer that read out of enumeration: the index-only listing leaves ZIP
symlink `link_target` unset, and the target is read lazily when needed — during
`members()`/`scan_members()` link resolution, and when following the link in
`open()`/`read()`/extraction. This makes `get_members_if_available()` honestly cheap and
turns "resolved vs. unresolved links" into a real, testable property rather than an
accident of which backend eagerly read data. (Alternative — keep eager reads and relax the
contract to "may read bounded inline metadata" — rejected: a fuzzy contract, and it makes a
"no-scan peek" silently open N members.)

### 5. The resolution asymmetry is documented, not hidden

`members()` and `scan_members()` return **fully-resolved** members (they read/scan whatever
is needed). `get_members_if_available()` returns whatever the index provides **as-is**:
members may have `link_target`/`link_target_member` unset for formats that store targets in
data, and it returns `None` entirely for a not-yet-materialized no-index format. Callers who
need resolved links call `members()`/`scan_members()`; callers who want a cheap peek accept
possibly-unresolved links. This is stated in both capability specs and `ARCHITECTURE.md`.

### 6. Rejected alternative — buffer/reorder iteration to resolve mid-pass

Considered: hold unresolved-link members in a buffer during the pass and yield them last,
after resolving, so `__iter__` itself yields fully-resolved members (and multiple passes
would agree). **Rejected:**

- It is not just forward symlinks — symlinks are *last-wins overall*, so **every** symlink
  must be held until EOF; the order degrades to "everything in archive order, then all
  symlinks in a clump."
- It can change **extraction results**: when a name exists as both a file and a symlink, the
  on-disk winner depends on write order; moving symlinks to the end silently alters
  last-wins-on-disk.
- It sacrifices the load-bearing **archive-order** invariant for a benefit already delivered
  by `scan_members()` (fully-resolved, true last-wins) — without reordering — and by the
  mutable-fill-in-place contract (held objects become resolved after the loop).
- A `yield_in_order` vs `hold_until_resolved` toggle doubles the behavioural surface and
  reintroduces "same call, different behaviour" variance. Overkill.

### 7. The behaviour matrix (the core question)

**`members()`** — fully-resolved list; materializes/scans as needed.

| | Leading-index | No-index (TAR) | Trailing-index (ZIP/7z) |
|---|---|---|---|
| Random, before | list (index; ZIP reads symlink targets to resolve) | list (**full scan**) | list (seek to EOF; reads symlink targets to resolve) |
| Random, after | list (cache) | list (cache) | list (cache) |
| Streaming (any) | `UnsupportedOperationError` | `UnsupportedOperationError` | `UnsupportedOperationError` |

**`scan_members()`** — fully-resolved list; mode-agnostic; finishes the pass.

| | Leading-index | No-index (TAR) | Trailing-index (ZIP/7z) |
|---|---|---|---|
| Random | = `members()` | = `members()` (full scan) | = `members()` |
| Streaming, before pass | list (index) **+ pass consumed** | list (**runs+finishes the pass**) | list (seek) **+ pass consumed** |
| Streaming, after **interrupted** pass | list (finishes remainder) | list (**finishes remainder**) | list (from cache/index) |
| Streaming, after **completed** pass | list (cache) | list (cache) | list (cache) |

**`get_members_if_available()`** — index-only; never scans, never reads member data, never consumes.

| | Leading-index | No-index (TAR) | Trailing-index (ZIP/7z) |
|---|---|---|---|
| Random, before | list (index) | **`None`** | list (seek); **symlink links unresolved** |
| Random, after materialization | list (cache, resolved) | list (cache, resolved) | list (cache, resolved) |
| Streaming, before pass | list (index) | **`None`** | list (seek); **symlink links unresolved** |
| Streaming, mid-interrupted pass | list (index) | **`None`** (not finalized) | list (seek); links unresolved |
| Streaming, after **completed**/`scan_members` pass | list (cache) | **list (cache, resolved)** ← the fix | list (cache, resolved) |

Behavioural changes vs. today: the no-index `get_members_if_available()` returns the
resolved list after a completed streaming pass (previously `None`); a second
`__iter__`/`stream_members`/`extract_all` raises; ZIP's index-only listing no longer
eagerly reads symlink targets (links unresolved until a resolving method runs).

## Risks / Trade-offs

- **[One-pass-only rejects a second `__iter__` even after completion]** → a caller who
  iterated once and wants to iterate again must use `scan_members()` (then iterate the
  returned list) or `get_members_if_available()`. → Intentional: it removes the pass-to-pass
  link-resolution divergence; the resolved list has dedicated accessors.
- **[Deferring ZIP symlink-target reads changes `get_members_if_available()` output]** →
  ZIP symlinks now list with unresolved links under the index-only peek. → This is the point
  (honest index-only contract); resolving methods and link-following still work, and it is
  covered by tests.
- **[`scan_members()` holds the whole resolved list in memory]** → O(members) metadata, not
  O(archive). → Already true of the streaming pass's `by_name_lists`; unbounded member
  *counts* are the separate, deferred no-cache concern.
- **[Single instance-held pass shared across `__iter__`/`stream_members`/`scan_members`]** →
  a refactor of TAR's `_iter_with_data` (and a contract for native 7z/RAR) so all entry
  points share one cursor and one finalization. → Contained to backends that stream; TAR is
  the only current one and its change is local.
- **[New second-pass rejection could break callers relying on today's silent re-read]** →
  Pre-1.0, no deprecation cycle; the previous behaviour was an opaque failure on
  non-seekable sources anyway.

## Migration Plan

Additive and pre-1.0: ship `scan_members()`, the finalization, the one-pass guard, the
index-only `get_members_if_available()` contract, and the ZIP symlink-target deferral
together. `ArchiveReader` protocol gains `scan_members`; `ARCHITECTURE.md` §2.4 and the two
capability specs update in the same change. No deprecation cycle; removed behaviours (a
second streaming pass; ZIP eager symlink-target reads during listing) were unspecified.

## Open Questions

_None outstanding._ Resolved during design review:

1. `__iter__` after a completed streaming pass **raises** (one-pass-only); it does not
   replay from cache — the pass-to-pass link-resolution divergence makes replay unsafe.
   `scan_members()`/`get_members_if_available()` are the post-pass accessors.
2. `scan_members()` is **mode-agnostic** (random mode ≡ `members()`).
3. `get_members_if_available()` is **strictly index-only**; ZIP defers symlink-target reads
   so its index-only listing leaves links unresolved.
