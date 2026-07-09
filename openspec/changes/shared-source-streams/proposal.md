## Why

Phase 6's native 7z/RAR readers (and any future native ZIP path) need multiple
concurrently-open member streams over one seekable archive handle — the shape
stdlib `zipfile._SharedFile` already provides (one underlying file + a lock + a
per-view position). Today `streamtools` has adapt/slice/delegate primitives but
no shared-source view, and the concurrency contract is only sketched in
`IDEAS.md` / `PLAN.md` entry criteria: readers are not thread-safe (one per
thread), interleaved single-threaded opens must work by construction, and
unsupported misuse must **fail loudly** rather than silently jumble bytes. Landing
this plumbing **before** the native parsers means those readers inherit safe
multi-open by construction instead of reinventing it per format.

## What Changes

- Add a `SharedSource` / per-view stream primitive under
  `archivey.internal.streams.streamtools` (zipfile `_SharedFile` shape: one
  handle, one lock, per-view cursor; each `read`/`seek` repositions under the lock).
- Decide and document the **concurrency contract** for a single reader:
  - Supported: multiple member streams open on one seekable reader in one thread
    (interleaved reads).
  - Unsupported / fail loudly: concurrent use of one reader from multiple threads;
    opening a second view over a non-seekable / forward-only source.
- Wire the primitive where today's backends already need independent member
  slices over one handle (at minimum: make it available for ZIP's member opens
  and for the upcoming native readers; do not rewrite every backend unless a
  clear win).
- Spec the contract under `compressed-streams` (streamtools home) and a thin
  cross-reference from `archive-reading` / `access-mode-and-cost` where the
  reader-level rules live.
- Keep `streamtools` free of archivey error types where possible; translate at
  the archivey boundary if a public-facing error is required.

## Capabilities

### New Capabilities

_(none)_

### Modified Capabilities

- `compressed-streams`: add requirements for the shared-source view (lock +
  per-view position; close semantics; seekable-only).
- `archive-reading`: add the reader-level concurrency contract (what multiple
  open member streams guarantee; what raises).
- `access-mode-and-cost`: clarify that `FORWARD_ONLY` / non-seekable sources
  cannot host concurrent member views (fail at second open, not silent
  interleave).

## Impact

- **Code:** new module(s) under `internal/streams/streamtools/`; possible light
  adoption in `zip_reader` / `base_reader` member-open paths; tests for
  interleaved reads and loud failure modes.
- **API:** internal plumbing only for v1 — not exported from `archivey`. The
  public guarantee is behavioral (interleaved `open()` works; cross-thread use
  fails loudly).
- **Deps:** none (stdlib `threading.Lock` / `RLock`).
- **Does not:** implement parallel multi-threaded extraction (`IDEAS.md` — still
  speculative); does not implement native 7z/RAR (consumers of this plumbing);
  does not claim free-threading safety under 3.13t.
