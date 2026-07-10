## Why

`shared-source-streams` (#51) landed a guarantee that any number of member streams may
be held open from one reader and read interleaved, correctly. `tar-concurrent-open`
extends the byte-range machinery to random-access TAR. But holding multiple member
streams open on a **solid / expensive-seek** archive (compressed TAR, one solid 7z
folder, a RAR solid block) can silently cost O(n) re-decompression per rewind — a
"re-reads a solid block" pattern that `VISION.md` explicitly calls a failure "even if a
small test corpus hides it."

The trap is a **format-dependent surprise deferred to production**: a developer who tests
only with ZIP / plain TAR (where concurrent open is free) and interleaves member streams
without a second thought ships code that explodes — slowly — the first time it meets a
`.tar.gz` or a solid 7z. A per-rewind warning does not save them ("a logging warning most
applications never see is a surprise deferred, not avoided" — `VISION.md`).

The fix that matches the library's uniformity principle (access-mode enforcement is
"deterministic across formats") is to make holding **multiple member streams open at once**
an **explicit, format-uniform opt-in**. Concurrency then fails fast *in development on every
format* — including the cheap ones — so the developer investigates, reads the documented
danger, and opts in knowingly. The cost receipt (`AccessCost.SOLID` / `solid_block_count`)
becomes the tool that tells an opted-in caller whether their interleaving is cheap or
expensive; it does not silently change what is *allowed*.

## What Changes

- **Default, every format:** at most one member data stream may be live per reader. Opening
  a second stream whose lifetime **overlaps** a still-open one SHALL raise a typed error
  (`ConcurrentAccessError`) — uniformly for ZIP, plain TAR, 7z, RAR, ISO, single-file, and
  random-access TAR alike, so the constraint is discovered regardless of the test corpus.
- **Gate on overlapping lifetimes, not on `open()` count or on reads.** The ordinary
  `open → read → close → open next` loop stays allowed on every format. "Live" spans from
  `open()` to the stream's `close()` / context-manager exit — **not** to EOF (member streams
  may be seekable and re-read) and **not** to garbage collection (non-deterministic).
- **Raise, do not auto-close.** The second overlapping `open()` raises and leaves the first
  stream **untouched and still readable**; the library never silently closes/invalidates a
  stream the caller still holds (that would defer the error to a later read, far from its
  cause — the harder-to-debug failure).
- **Opt-in flag** on `open_archive()` (`allow_multiple_open_streams: bool = False`) lifts the
  gate. When enabled, any number of member streams may be held open and interleaved,
  correctly, via the existing byte-range / `SharedSource` machinery. Its docstring documents
  the solid-archive re-decompression danger and points at `cost`.
- **Cost is informational, not gating.** `AccessCost` / `solid_block_count` describe whether
  opted-in interleaving is cheap (DIRECT) or expensive (SOLID); they never determine legality.
- **Drop the blanket TAR-RA concurrent-open exemption** (folded here so there is one
  authoritative rewrite): random-access TAR joins the byte-range backends when its
  uncompressed stream is seekable; only **streaming** and **non-seekable** stay out of scope.
- No public API break beyond the new default. **Not BREAKING** in the sense of data/behaviour
  for the common `open → read → close` path; it *does* tighten the concurrent-open guarantee
  landed in #51 from always-on to opt-in (see Impact).

## Capabilities

### New Capabilities

_(none)_

### Modified Capabilities

- `archive-reading`: rewrite *Multiple concurrently-open member streams* so multiple
  simultaneously-open streams are an opt-in, format-uniform capability (default raises on the
  second overlapping open; TAR-RA in scope when seekable). Rewrite *Random-access member-open
  is reentrant and reader-state-free* to drop the TAR blanket exemption (streaming /
  non-seekable only). Add the `allow_multiple_open_streams` keyword to *Opening an archive for
  reading*.
- `access-mode-and-cost`: note that `allow_multiple_open_streams` composes with `streaming`
  (only meaningful in random-access mode), and that `AccessCost` is **informational** about
  concurrent-open expense, never a gate on legality.

## Impact

- Code: `src/archivey/internal/base_reader.py` (track live member streams handed out by
  `open()`; hook stream `close()` to decrement; raise on the second overlap unless opted in),
  `src/archivey/core.py` / `open_archive` signature (new keyword), `src/archivey/exceptions.py`
  (`ConcurrentAccessError`).
- Specs: `openspec/specs/archive-reading/spec.md`, `openspec/specs/access-mode-and-cost/spec.md`.
- Docs: `docs/parallel-reader.md` (the opt-in is the gate for any future fan-out), ABC
  docstring on `_open_member` / `open()`.
- Tests: second-overlapping-open raises uniformly (ZIP + plain TAR + a solid format);
  sequential loop still allowed; opt-in enables interleave; first stream survives the raise.
- **Relationship to #51 / `tar-concurrent-open`:** this narrows the just-landed concurrent-open
  guarantee from "always available" to "opt-in, correct-and-cost-flagged when enabled." It is
  the authoritative owner of the `archive-reading` concurrent-open rewrite; `tar-concurrent-open`
  is rescoped to its `format-tar` mechanism (SharedSource + forward-cursor), which now runs
  **under** this opt-in.
- Out of scope: the forward-cursor / pooled-view *optimization* for solid streams (that lives in
  `tar-concurrent-open` and future 7z/RAR work); parallel/multi-thread extraction; changing the
  one-reader-per-thread rule.
