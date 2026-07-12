> **Grab-bag / exploration.** v1 is sync-only (`docs/decisions/0005-sync-only-v1.md`). Index: [grab-bag/index.md](index.md).

# Async API — Exploration

> **Status: exploration, not a decision.** v1 currently lists "Async API" as a
> **decided deferral** (`openspec/project.md` "Deferred / out of scope (v1)",
> `SPEC.md` Appendix A, `ARCHITECTURE.md` §5.3). This document analyses how hard
> async would actually be, whether it must be "baked in from the start," and what
> the cheap-now / expensive-later seams are. It ends with a recommendation and a
> short list of decisions that are the maintainer's to make. Nothing here changes
> a shipped spec; promoting any of it means a real `openspec/changes/` proposal.

## 1. The question

> "It may be better to bake [async] in from the start instead of trying to add it
> later."

The instinct behind this is sound and worth taking seriously: async is the classic
**"function colour"** problem. `async def` can only be awaited from `async def`, so
if the core is written sync and async is bolted on later, you risk a parallel
universe of duplicated code (`read` / `aread`, `open` / `aopen`, `__iter__` /
`__aiter__`) threaded through every backend. If that were the real cost of "later,"
baking in now would clearly win.

The conclusion of this analysis is that **it is not the real cost** — because a hard
constraint (below) caps how async an archive library can ever be in Python, and that
cap happens to fall exactly where the current architecture already has a clean seam.
So the recommendation is **not** "rewrite the core async," nor "stay sync and shrug,"
but a narrow middle: **keep the core sync, and spend ~1–2 days now on a handful of
seams that make a leaf-level async facade cheap to add later.**

## 2. What "async" means for an archive library

There are three independent surfaces a caller might want to be async. They are *not*
the same feature and have very different value:

| Surface | What the caller writes | Who benefits |
|---|---|---|
| **(S) Async source** | open an archive whose *compressed bytes* arrive over the network without blocking the event loop (`s3://…/a.zip`, an `http` body, an fsspec async file) | servers reading archives off object storage / remote FS |
| **(M) Async member streams** | `await stream.read(n)` / `async for chunk in stream` on a member, with backpressure into an async consumer | streaming a member out to an async HTTP response |
| **(I) Async iteration / extraction** | `async for member in reader`, `await reader.extract_all(dest)` so a long extraction doesn't block the loop | any asyncio app that doesn't want a multi-second blocking call |

Surface **(I)** is satisfiable *today* with zero core changes via
`asyncio.to_thread(archivey.extract_all, ...)` — the whole operation runs in a worker
thread and the event loop stays responsive. That is the current plan (§5.3) and it is
genuinely adequate for "don't block my loop while extracting." Surfaces **(S)** and
**(M)** are where a thread wrapper is unsatisfying, and they are what "bake it in"
is really about.

## 3. The hard constraint: the decoders *pull* bytes synchronously

Every decode path the library relies on is **synchronous C code that pulls its input
through a blocking `read()` callback we do not control**:

- `zipfile` calls `self.fp.read(n)` synchronously deep inside `ZipExtFile`.
- `tarfile` reads from `fileobj` synchronously while walking headers.
- stdlib `lzma` / `bz2` / `zlib` decompressors are pull-driven C extensions.
- the native 7z / RAR header parsers read from a sync stream; `unrar` is a subprocess
  whose pipe we read synchronously.

There is no hook to make `lzma.decompress` *await* its next input chunk. You cannot
suspend a C stack frame on an `await`. This has a decisive consequence:

> **There is no "async all the way down" available in Python without rewriting the
> decoders.** The most async an archive library can be is: an async *source* surface
> and an async *consumer* surface, with the sync decode core **running in a worker
> thread** and **bridged** to the event loop.

Two corollaries:

1. Async buys **no CPU concurrency** — decode is CPU-bound under the GIL. The only win
   is **I/O overlap**: not blocking the loop while waiting on a slow (network) source,
   and applying backpressure to an async consumer.
2. Because decode must run in a thread *anyway*, the bridge is the same regardless of
   whether the core is "written async." A native-async core would still call
   `lzma.decompress` on a worker thread. The async-ness only ever lives at the
   **edges** (source in, bytes out), never in the middle.

This is why "bake the whole core async" is the wrong target: the middle *cannot* be
async, and the edges are exactly where the current design already injects/returns
plain objects.

## 4. The realistic ceiling, drawn on our architecture

Given §3, the maximal async design is:

```
   async caller
        │   await reader.read(member)        ← async edge (M / I)
        ▼
 ┌──────────────────────────┐
 │  archivey.asyncio facade │  runs the sync reader on a worker thread,
 │  (anyio BlockingPortal)  │  bridges chunks back via from_thread
 └──────────────────────────┘
        │   sync BinaryIO.read(n)            ← the sync core, unchanged
        ▼
 ┌──────────────────────────┐
 │  sync ArchiveReader +     │  zipfile / tarfile / lzma / 7z / unrar
 │  DecompressorStream stack │  — pulls bytes synchronously
 └──────────────────────────┘
        │   source.read(n)                   ← async edge (S)
        ▼
 ┌──────────────────────────┐
 │  sync-facade over an      │  a thin shim that turns the core's blocking
 │  async source             │  read(n) into `portal.call(async_source.read, n)`
 └──────────────────────────┘
```

Both async edges are bridged with the **same** primitive: a worker thread running the
sync core, plus `anyio` / `asyncio` `run_in_executor` + a portal to call back into the
loop for the async source. The sync core in the middle **does not change shape**.

## 5. Options

### Option A — Sync-only + `asyncio.to_thread` (status quo)

`asyncio.to_thread(archivey.extract_all, src, dest)`. Whole op in one thread.

- **Cost now:** zero (already the plan). **Cost later:** zero.
- **Gets you:** surface (I) — loop stays responsive during a whole-archive operation.
- **Doesn't get you:** (S) genuine async source I/O; (M) per-chunk backpressure into an
  async consumer. Concurrency is bounded by the thread pool; a streaming read from S3
  blocks a whole thread for the archive's lifetime.

### Option B — Sync core + thin async facade leaf (`archivey.asyncio`), added later

A separate module that *wraps* the sync core. `async for member in areader`,
`await astream.read(n)` where each call hops to the worker thread; an async source is
adapted by a sync-facade shim (§4). The core never imports it; the facade is a **leaf,
not a root**, so it adds no colour to the core.

- **Cost now:** zero (it's deferred). **Cost later:** moderate, **isolated** — one new
  module + tests, *if* the core honours the §6 seams. Without the seams it's still
  doable but uglier (e.g. an async source can only be supported by buffering it whole
  first).
- **Gets you:** (I) fully; (M) with real backpressure; (S) for any async source that
  can be driven from a worker thread via a portal.
- This is exactly the "future `archivey.asyncio` module … is a clean add-on" already
  promised in ARCHITECTURE.md §5.3 — this document's contribution is to confirm it
  *stays* a clean add-on, and to pin down the seams that keep it clean.

### Option C — Native async core ("bake it in")

Re-express the source protocol, the `DecompressorStream` stack, the backends, and the
reader surface in `async def`. Decode still runs sync on a thread (§3), so the async
keywords stop at the decoder boundary regardless.

- **Cost now:** very high — weeks. It colours every layer, doubles the stream stack,
  and forces an `anyio`/event-loop dependency into the core (against the zero-dep-core
  rule). It must *still* bridge to sync decoders, so it carries Option B's complexity
  **plus** the colour tax.
- **Gets you:** nothing Option B doesn't, because the win is capped at the edges (§3).
- **Verdict:** net negative. It pays the full colour cost to reach a ceiling that
  Option B reaches from a leaf.

## 6. What "baking in now" *should* mean — the cheap seams

The valuable kernel of "bake it in from the start" is **not** writing async code now;
it is making sure the sync core never makes an assumption that *blocks* Option B later.
These are small, sync-only, and worth doing during the stream/reader work regardless:

1. **Inject the source as a narrow Protocol, not concrete `BinaryIO`.** Define an
   internal `ReadableSource` (`read`, `readable`, `close`) / `SeekableSource`
   (`+ seek`, `tell`, `seekable`) Protocol and type backend/stream inputs against it.
   The runtime objects stay ordinary sync file objects; the point is that the core
   already accepts *"anything that reads like a file,"* so a later sync-facade-over-
   async-source drops in without touching call sites. (The binaryio layer in the
   stream PR already normalises arbitrary streams — this is formalising that boundary
   as the single injection point.)

2. **Keep every reader instance thread-confinable.** No module-global mutable I/O
   state; all per-open state lives on the reader/stream instance; nothing assumes it
   runs on the thread that constructed it. Then the facade can own a reader on a
   dedicated worker thread with no surprises. (Largely true today — worth stating as a
   contract so it isn't broken accidentally.)

3. **Keep the byte interface pull-based and chunked end-to-end.** The whole stack is
   already `read(n) -> bytes` with bounded buffers (the `stream_members` backpressure
   model). A portal bridges a pull-based sync stream to an async consumer cleanly; a
   push/callback design would not. Don't introduce eager whole-member materialisation
   on any hot path.

4. **Don't leak the event loop into error/context plumbing.** The per-library
   translators + central context stamping (error-handling spec) are already
   loop-agnostic; keep it that way so the facade needs no special error handling.

5. **Write the plan down (this doc) and link it** from the deferral note, so "later"
   starts from a design rather than a blank page.

None of these is async code. All are sync hygiene that a careful library wants anyway.
Their combined cost is ~1–2 days folded into the existing stream/reader phases, versus
the weeks Option C would cost — and they make Option B a localised, low-risk add.

## 7. Recommendation

1. **Do not bake a native async core in (reject Option C).** The Python decoder
   constraint (§3) caps the payoff at the edges, where the architecture already has a
   seam; paying the colour tax buys nothing extra.
2. **Keep v1 sync, document `asyncio.to_thread` for surface (I)** (Option A) as the
   supported answer today.
3. **Adopt the §6 seams now** as explicit, sync-only contracts in the relevant specs
   (`archive-reading`, `compressed-streams`/stream layer, `backend-registry`) so the
   future facade is guaranteed cheap. This is the honest, low-cost way to honour "bake
   it in from the start."
4. **Spec the `archivey.asyncio` facade (Option B) as a named, scheduled follow-on**
   (not just "speculative"), built on `anyio` so it serves both asyncio and trio, and
   landing after the sync core is stable.

In one line: **bake in the *seams*, not the *colour*.**

## 8. Effort estimate

| Item | When | Effort | Risk |
|---|---|---|---|
| A: document `to_thread` recipe | now | ~0.5 day | none |
| §6 seams (source Protocol, thread-confinement contract, pull-model guarantee, doc) | now, folded into stream/reader phases | ~1–2 days | low |
| B: `archivey.asyncio` facade via `anyio` (loop bridge, async source shim, `async for`/`await read`, tests) | scheduled follow-on | ~3–5 days | low–moderate, **isolated** to one module |
| C: native async core | — | weeks | high; rejected |

## 9. Decisions for the maintainer

This document deliberately does **not** edit the shipped deferral. The following are
the maintainer's calls; once made, they become real `openspec/changes/` proposals:

1. **Flip the framing** of the deferral from "decided deferral" to "sync v1 + a
   *scheduled* `archivey.asyncio` follow-on built on the §6 seams"? (Touches
   `openspec/project.md` line ~112, `SPEC.md` Appendix A, `ARCHITECTURE.md` §5.3.)
2. **Adopt the §6 seams as v1 contracts** now (the only thing with a cost in the
   current phases), or leave them implicit?
3. **`anyio` vs `asyncio`-only** for the eventual facade (anyio ⇒ trio support, one
   extra dep behind an `[async]` extra; asyncio-only ⇒ zero deps, asyncio-bound)?
4. **Is async source I/O (surface S) in scope at all**, or is "async consumer of a
   local-file archive" (surfaces I + M) enough? S only earns its keep alongside the
   deferred fsspec/remote-source work in `IDEAS.md`.
