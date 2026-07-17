# Brief — Stream wrapper layering: correctness + collapse-for-performance

Read `review/README.md` (conventions, VISION tie-breakers, deliverable shape). This
review has **two coupled questions**, and the second is the point:

1. **Correctness** — are the per-member stream wrappers and the checks they perform
   (slicing bounds, digest/length verification, seek-disables-verify, EOF handling,
   concurrency re-seek, close/teardown) actually right?
2. **Necessity** — are the several stacked layers each *required*, or can most of the
   extra wrapping (slicing, verification) collapse into **one** stream that does
   everything, keeping the outer `ArchiveStream` identity? This is a performance
   question with a concrete design hypothesis (below).

## Why now

The performance review (#134) found the ≤1.3× budget missed on the common paths —
ZIP read-all **2.2–2.3×**, extract-all **2.4–3.7×**, open+list **5–8×** stdlib — and
attributed part of it to the stream layering. Each member handle the library returns
is a stack of `ReadOnlyIOStream`/`DelegatingStream` wrappers, and **every `read()`
chunk pays a Python-level method dispatch (and often a bytes copy) at each layer**.
Collapsing layers is one of the few structural levers left before `0.2.0`.

Note the division of labour with #134: that review owns *measuring* the budget and
the open/list/extract hotspots; **this** review owns the *wrapper architecture* —
whether the layers are correct and whether they can be fused. The open+list 5–8× is
likely dominated by materialization/`zipfile`, not the wrapper stack (streams open
lazily) — confirm, but the wrapper target is the **read/extract per-chunk and
per-open** cost.

**Attribution discipline (sharpening from #134):** #134's ZIP read-all profile splits
the non-zlib time across *several* buckets — `DecompressorStream`'s chunk loop +
bytearray copy, `VerifyingStream`, nested `ArchiveStream` shims, and CRC itself
(parity with `zipfile`). This review can only claim wins on the **wrapper** buckets.
Do **not** project that fusion alone closes the full 2.2× gap. Isolate with a
**STORED ZIP** microbench (no decode layer) so wrapper overhead is visible without
zlib/`DecompressorStream` noise; leave decode-engine cost to backlog Topic 6.

## The current stack (trace and confirm — this is the object of review)

For a ZIP member the handed-out handle is, bottom to top (hot path, measurement off):

1. **Source view** — `SlicingStream` (`streamtools/slice.py`) presents the member's
   byte range; in `SharedSource` mode it re-seeks-under-lock per read for CONCURRENT
   fan-out. `fix_stream_start_position` may add a *second* `SlicingStream` to give a
   codec a clean `tell()==0`. TAR/ISO add `LockedStream` (`streamtools/locked.py`)
   over the shared handle.
2. **Decode** — the codec stream: `DecompressorStream` + `Decoder` (post-#96),
   `_AcceleratorStream`/`_GzipTruncationCheckStream` (`codecs.py`), or
   `AesDecryptStream` (`crypto.py`). STORED members have **no** decode layer.
3. **Verify** — `VerifyingStream` (`verify.py`): hashes forward reads, checks
   `member.hashes` + `expected_size` at clean EOF, bounds over-long decodes, drains
   post-payload authenticators (WinZip AES HMAC), disables on seek off the frontier.
4. **Outer** — `ArchiveStream` (`archive_stream.py`): lazy open under a lock, exception
   translate+stamp, `size`, `RewindWarning`, `on_close` lease release, `weakref.finalize`,
   diagnostics watermark.

So a **STORED ZIP member** = `SlicingStream → VerifyingStream → ArchiveStream` (three
Python `read()` hops, no decompression), and a **deflate member** adds the decoder.
Composition site: `base_reader._wrap_member_stream` (`base_reader.py:572`) +
`zip_reader.py:816` (VerifyingStream) — map the equivalent stack for **every** backend
(TAR, 7z solid, RAR-via-unrar, ISO, single-file, directory).

**Concrete double-wrap (fixed on the perf follow-up branch):** the default
`_lazy_member_stream` used to build an outer `ArchiveStream(lazy=True)` whose
`open_fn` called `_open_member` → `_wrap_member_stream` (a *second*
`ArchiveStream`). Nesting is now collapsed inside `ArchiveStream._ensure_open`
via `_collapse_nested` (steals a still-lazy opener, or an already-opened inner, and
**adopts nested translate/stamp/rewind_warning** so codec errors stay typed) —
confirm the public handle's `_inner` after first read is **not** an `ArchiveStream`,
and treat any remaining nesting as a regression. Solid sequential paths (7z/RAR)
build a single wrapper directly.

## Part 1 — Correctness audit (do this first; it defines the invariants a collapse must keep)

Each wrapper is subtle; a fusion that silently drops one of these is worse than the
overhead. Verify each, with `file:line` and the concrete input/state:

- **`readinto` side-effect safety.** `DelegatingStream.readinto` zero-copies straight
  to `inner.readinto`, *bypassing* an overridden `read` unless `readinto_passthrough=
  False` (`base.py:99-125`). Any layer with a read side effect (hashing, counting)
  must not be bypassed. Confirm `VerifyingStream` (a `ReadOnlyIOStream`, so `readinto`
  routes through `read` — good) and `CountingReader`/`OutputCountingStream` (override
  `readinto` explicitly) are all correct, and that a fused stream preserves this — a
  `readinto` that skips the hasher is a silent verification hole.
- **`VerifyingStream` EOF/close logic** (`verify.py:195-331`) is the riskiest: verify
  only on a clean *sequential* read to EOF; the deferred-short verdict; `reached_declared`
  vs the `read(1)` probe; the accel-raises-instead-of-EOF path; typed-error precedence;
  the `expected_size` bound *draining the WinZip AES HMAC*. Enumerate the states and
  confirm each holds — this is the archived stream-decoder F6/F4 territory, so cross-check
  those fixes didn't leave a seam.
- **`SlicingStream` dual mode** (`slice.py`): single-consumer (eager seek) vs
  re-seek-under-lock (`_seek_before_read`); the "never call unlocked `tell`/`seek` on a
  shared handle" rule (`BufferedReader.tell` is not thread-safe); `SEEK_END` probing;
  `own_source` close vs non-owning default; the BytesIO-matching negative-seek clamp.
- **`ArchiveStream`** (`archive_stream.py`): the lazy-open claim/lock and the "open_fn
  claimed then failed" path; `_fail` translation ordering (closed-file before the
  per-library translator); the finalizer/lease release and its interpreter-exit caveat;
  `size` derivation; `_collapse_nested` (flattens a deferred `_open_member` wrap inside
  `_ensure_open`, stealing a still-lazy opener or an already-opened inner) — confirm
  close/lease ownership stays on the public handle only.
- **`SharedSource`/CONCURRENT**: how views are minted per handle and whether the
  single-live-stream / re-seek invariants survive. **This is the main constraint on
  fusion** (see Part 2).

## Part 2 — Necessity & the collapse hypothesis (the deliverable)

The maintainer's hypothesis to evaluate, accept/refute, or refine:

> Keep an **outer `ArchiveStream`** that carries the extra properties above a plain
> `BinaryIO` (`size`, cost/rewind, translation, lease). Move **most of the extra
> wrapping — slicing and verification — into it**, so a member is served by a **single
> stream** that reads its byte range, decodes, hashes/length-checks, and translates in
> one `read()`, instead of a 3–4 deep stack.

Evaluate concretely:

- **Per-layer verdict table.** For each layer (member-boundary slice, `fix_stream_start_position`
  slice, verify, outer) classify: *fuse into the outer stream* / *keep separate,
  structural* / *already conditional (skip)*. The redundant nested outer from
  `_lazy_member_stream` is **already collapsed** (confirm; do not re-propose). Justify
  each against the Part-1 invariants. Decode engine stays out of scope (Topic 6) —
  list it as *keep separate* with a one-line pointer, not a redesign.
- **What fuses cleanly.** `VerifyingStream` is the strongest remaining candidate: the
  outer `ArchiveStream` already reads through an inner and owns `size`; folding the
  hasher + `expected_size` bound into its `read`/`close` removes a whole layer on
  *every* member. But it's conditional (nested `open_stream`/inner-archive handles and
  non-FILE members don't verify) — show the fused stream stays correct when there's
  nothing to verify.
- **What resists fusion, and why.** Member-boundary slicing entangles with
  `SharedSource` (multiple concurrent views over one handle, re-seek under lock) and
  with internal uses (`fix_stream_start_position`, codec body slices) that aren't at
  the member boundary. Distinguish *member-boundary slicing* (foldable into the
  per-member outer stream, since that handle is 1:1 with a member) from *shared/internal
  slicing* (structural — leave it). If CONCURRENT fan-out makes even member-boundary
  slicing need a separate view, say so and quantify what's left to fuse.
- **The `readinto` fast path.** A fused single stream can implement one real `readinto`
  (zero-copy into the caller's buffer) that also hashes/bounds — versus today's
  `ReadOnlyIOStream.readinto`→`read`→bytes-copy at the verify layer. Estimate that win;
  it may matter more than the dispatch count.
- **Decode stays separate.** The `DecompressorStream`/`Decoder` engine and the
  accelerator/crypto stages are *not* in scope to fold in — they're the settled #96
  decode layer (and Topic 6 for their *speed*). The target is the wrapper stack
  *around* the decoder, not the decoder. Note #134 already landed a `readall()`
  join-of-chunks fast path on `DecompressorStream`; don't re-litigate it here.
- **Irreducible floor.** Before recommending fusion, state what cost *must* remain:
  at least one Python `read` that updates a hasher + bounds length, plus translate/stamp
  on errors, plus lease/finalizer on the public handle. If the measured stack is already
  close to that floor on STORED ZIP, fusion is the wrong lever.
- **Cost, measured.** Back the recommendation with numbers: per-`read(64K)` dispatch
  cost across the current stack vs a fused stream, and per-`open()` construction cost
  (the `ArchiveStream` lock+finalizer+watermark, the `VerifyingStream` hasher setup, the
  `SlicingStream`) — reuse #134's harness / `--track-io` and add a microbench. Tie the
  projected win back to the **wrapper share** of the 2.2× / 2.4–3.7× gaps, not the
  whole gap. Prefer: STORED vs deflate as the isolation axis (nested `ArchiveStream`
  is already gone).

## Non-goals
- Not re-opening the #96 decoder composition or the accelerator internals — those are
  settled and separately reviewed.
- Measurement wrappers (`counting.py`) are already identity when measurement is off
  (`base_reader.py:533`) — not a hot-path cost; don't propose removing them.
- Not a correctness *bug hunt* beyond the wrappers in scope — but a real bug found in
  the verify/slice/outer logic is a first-class finding (it also de-risks the fusion).
- Don't fuse away an invariant to win a benchmark: a smaller stack that verifies less,
  or breaks CONCURRENT, fails VISION #2/#3 and is not an acceptable trade.
- Not open+list / detection / member-model construction (H3) — out of scope; those are
  non-stream costs from #134.

## Deliverable
Per README. Suggested theme files: `correctness.md` (the per-wrapper audit + the
invariant list), `layer-map.md` (the full per-backend stack, traced), and
`collapse-design.md` (the verdict table + a concrete fused-stream design with the
invariants it preserves, what stays separate and why, and the measured/estimated win).
The headline the maintainer wants: **can a single stream under the `ArchiveStream`
identity replace slicing+verification without weakening any Part-1 invariant, and how
much does it buy?** Give a clear yes/no/partial with the design and the numbers, not a
menu. If the answer is "partial," prefer a ranked sequence (e.g. fuse verify next;
leave SharedSource slicing) over an options list.
