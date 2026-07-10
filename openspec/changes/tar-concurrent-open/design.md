## Context

> **Depends on `concurrent-open-opt-in`.** Interleaving multiple open member streams is an
> opt-in, format-uniform capability (`allow_multiple_open_streams`); the default still permits
> one live stream at a time and raises on a second overlapping open. That change owns the
> `archive-reading` rewrite (opt-in gate + dropping the TAR-RA exemption). Everything below is
> the **TAR mechanism** that runs when the caller has opted in.

`shared-source-streams` landed `SharedSource` and a concurrent-open contract that **exempts**
random-access TAR (TAR-RA) as a "single shared decoder." `parallel-reader-exploration`
repeated that carve-out in the `_open_member` reentrancy invariant.

That model is wrong for how `TarReader` already works:

1. **Compressed TAR** opens as `codec → uncompressed stream → tarfile.open(..., mode="r:")`.
   `TarInfo.offset_data` is an offset into the **uncompressed** stream, not the gzip/xz bytes.
2. **Plain TAR** uses stdlib `tarfile` in `r:` mode; member payloads are independent byte
   ranges. Stdlib `_FileInFile` already re-seeks the underlying fileobj for sequential
   `extractfile` use — but two interleaved `extractfile` streams on one `TarFile` still
   fight over one file position / one decoder cursor.
3. **`SharedSource`** already solves "many views, one seekable source" for single-file and
   is the right primitive for the uncompressed stream tarfile sees.

So TAR-RA can join the concurrent-open club by wrapping that uncompressed stream and serving
member data from views at `offset_data`, with a **forward-cursor** policy so sequential
extract does not pay a new view per member.

## Goals / Non-Goals

**Goals:**

- Random-access TAR (`streaming=False`) supports interleaved concurrent member streams
  under the same public contract as other byte-range backends.
- Spec carve-outs that name TAR as exempt are removed or narrowed to **streaming TAR only**.
- Sequential / forward-only member reads stay efficient (reuse one view when seeking forward).
- Preserve existing cost model: compressed TAR remains `AccessCost.SOLID` /
  `is_solid=True` (one compression stream); concurrent open does not claim free random
  access into the *compressed* bytes.

**Non-Goals:**

- Streaming TAR (`streaming=True` / `r|`) concurrent open.
- Multi-decoder or indexed codecs for compressed TAR (seekable gzip/zstd indexes) — backlog
  in `IDEAS.md`.
- Parallel extraction / multi-threaded `ArchiveReader` use.
- Changing ZIP/ISO SharedSource dispositions.
- Making `tarfile.TarFile.extractfile` itself concurrency-safe (we bypass it for data opens).

## Decisions

### D1. SharedSource wraps the uncompressed stream tarfile sees

**Choice:** After codec open (compressed) or after opening the plain TAR fileobj/path handle,
wrap that seekable uncompressed byte source in `SharedSource`. `tarfile.open(fileobj=…,
mode="r:")` reads headers (and today, data via `extractfile`) from a **view** or from a
dedicated "tarfile cursor" view — not from the raw SharedSource object (which is not a
`BinaryIO`).

**Alternatives:**

- Wrap the *compressed* bytes in SharedSource — rejected: `offset_data` is uncompressed;
  seeking compressed bytes does not land on member payloads.
- Open N independent codec+tarfile stacks per concurrent member — rejected for default path:
  N× decompress cost; keep as future escape hatch only if benchmarks demand it.

### D2. Member data via `SharedSource.view(offset_data, size)`, not dual `extractfile`

**Choice:** `_open_member` returns a SharedSource view (then existing `_wrap_member_stream`)
for FILE members, using `TarInfo.offset_data` and `TarInfo.size` (sparse: follow existing
sparse handling — if today `extractfile` expands sparse, preserve that behavior; do not
regress).

**Why:** Two `extractfile` results share one `TarFile.fileobj` position; even if that
fileobj were a SharedSource *view*, both would mutate the same view `_pos`. Independent
views are required.

**Header scan / `getmembers`:** may keep using one long-lived view (or the tarfile's
fileobj) for the catalog pass; concurrent *data* opens must not depend on that cursor.

### D3. Forward-cursor view policy (do not mint a view per member unconditionally)

**Choice:** Maintain a small pool / single "forward cursor" view used when the next open's
`offset_data` is at or after the cursor's current position and no other consumer holds it.
Mint a **new** view only when:

- an open needs an earlier offset while the forward cursor is busy, or
- the forward cursor cannot be reused safely (e.g. another stream still reading from it).

**Why:** Unconditional `view()` per `open()` is correct but hurts sequential extract
(lock + seek setup per member). Forward reuse matches typical TAR extract order.

**Alternatives:** Always new view — simplest, correct, slower sequential. Always one view —
incorrect under concurrency.

### D4. Plain TAR path sources

**Choice:** Open the path once into a seekable file object, wrap with SharedSource, feed
tarfile a catalog/cursor view. Do not leave tarfile owning an unwrapped path handle if
member data will come from SharedSource views over a different FD (two FDs ⇒ two catalogs
risk). One SharedSource over one FD is the source of truth.

### D5. Spec updates

**Choice:** The `archive-reading` concurrent-open + reentrancy rewrites (drop the TAR-RA
exemption, add the opt-in gate; streaming/non-seekable remain out of scope) are owned by
`concurrent-open-opt-in`. This change only adds a `format-tar` requirement describing the
uncompressed-stream SharedSource + forward-cursor policy, applicable when the caller has opted
in via `allow_multiple_open_streams`.

### D6. Lifecycle

**Choice:** Reader close closes the SharedSource (and owned codec stream) after tarfile
close, same ownership story as today's `_owned_stream`. Member views are non-owning; reads
after reader close fail loudly per existing SharedSource / reader-boundary contract.
Revisit rapidgzip-style "don't close under live accelerator" only if a TAR codec path hits
the same abort class (`docs/known-issues.md`); default gzip/bz2/xz via stdlib should be fine.

## Risks / Trade-offs

- **[Risk] Sparse / GNU sparse members** — `offset_data`/`size` may not match what
  `extractfile` returns today → **Mitigation:** keep `extractfile` for sparse (or map
  sparse map explicitly); add a sparse fixture test before switching the common path.
- **[Risk] Forward-cursor bugs under interleaving** — wrong reuse corrupts bytes →
  **Mitigation:** property/interleave tests; default to minting a new view when unsure.
- **[Risk] tarfile header reads vs data views** — concurrent header access during open →
  **Mitigation:** materialize members before fan-out (existing precondition); catalog pass
  completes before concurrent data opens in the supported regime.
- **[Risk] Sequential perf regression** — if policy always mints views → **Mitigation:**
  benchmark sequential extract plain + `.tar.gz` before/after; keep forward-cursor as
  MUST for the happy path.
- **[Trade-off]** Seeking a compressed-TAR uncompressed stream may re-decode from the
  start (SOLID cost already advertised). Concurrent open does not make compressed TAR
  `DIRECT`; it only makes interleaved opens correct.
- **[Note]** Stdlib `tarfile` `_FileInFile` already re-seeks a *seekable* fileobj on each
  `read()`, so single-threaded interleaved `extractfile` can appear to work today. This
  change still moves data opens to SharedSource views so TAR-RA honors the archivey
  invariant (per-view position + locked seek+read), matches other backends, and enables a
  deliberate forward-cursor policy — rather than relying on tarfile's shared handle.

### Seekability (spike result)

Archivey's codec streams for gzip/bz2/xz used by `TarReader` report **seekable** under
`streaming=False`. `SharedSource` can wrap that uncompressed stream for both plain and
compressed TAR-RA. If a future codec path is non-seekable, that variant stays exempt until
a seekable layer exists (spool or indexed codec — `IDEAS.md`); tasks include a guard test.

## Migration Plan

1. Confirm seekability per codec in tests (plain + gz/bz2/xz at minimum).
2. Wrap uncompressed stream in SharedSource; catalog via one cursor view; data via
   `view(offset_data, size)` + forward-cursor policy.
3. Interleave + sequential regression tests; sparse handling check.
4. Update specs/docs/ABC docstring; fix `docs/parallel-reader.md` TAR-RA row.
5. No API migration for callers.

## Open Questions

- Sparse member strategy (`extractfile` vs. manual map) — resolve in implementation.
- Whether tarfile should hold a SharedSource view for headers only, while all data opens
  go through the pool (likely yes).
- Non-seekable compressed codecs: keep a narrow exemption vs. spool — prefer exemption +
  `IDEAS.md` unless spool is already cheap and local.
