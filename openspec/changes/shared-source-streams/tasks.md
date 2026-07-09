# Tasks — Shared-source streams (Phase 6 entry gate)

> Run tools through uv: `uv run pytest`, `uv run pyrefly check`, `uv run ty check`,
> `uv run ruff`. Internal plumbing + reader contract; no new public exports.
> Prerequisite for native 7z/RAR (Phase 6) multi-open member streams.

## 1. SharedSource primitive (`streamtools`)

- [ ] 1.1 Add `SharedSource` + `SharedSourceView` under
      `src/archivey/internal/streams/streamtools/` (new module, e.g. `shared.py`).
- [ ] 1.2 Implement lock + per-view cursor (`RLock`); `view(start=0, length=None)`;
      `read`/`seek`/`tell`/`close` on the view; owner close invalidates views.
- [ ] 1.3 Reject `view()` when the underlying stream is not seekable (`ValueError`
      or existing streamtools precedent — no archivey exception imports).
- [ ] 1.4 Re-export from `streamtools/__init__.py`; keep the package free of
      archivey exception / types imports.
- [ ] 1.5 Unit tests: interleaved reads; bounded-length views; owner-close
      invalidation; non-seekable rejection; view-close does not close owner.

## 2. Reader concurrency contract

- [ ] 2.1 Add an owning-thread identity check helper on `BaseArchiveReader`
      (record `threading.get_ident()` on first public I/O or at open).
- [ ] 2.2 Call the check from public I/O entry points (`open`, `read`,
      `stream_members`, `__iter__`, `extract_all`, `members` / `scan_members` as
      applicable) and raise `UnsupportedOperationError` on mismatch.
- [ ] 2.3 Test: same-thread interleaved `open()` on a seekable ZIP (or other
      DIRECT format) returns correct bytes for both members.
- [ ] 2.4 Test: second-thread use raises `UnsupportedOperationError` with a
      clear message (use a thread + queue/event; keep the test deterministic).

## 3. Forward-only / non-seekable interaction

- [ ] 3.1 When `stream_capability` is `FORWARD_ONLY` (or the source cannot seek),
      a second concurrent `open()` without closing the first SHALL raise
      `UnsupportedOperationError` (implement at base or backend — pick the layer
      that already knows seekability).
- [ ] 3.2 Test covering the failure on a non-seekable / streaming TAR reader
      (or a synthetic reader double if TAR cannot open two members that way).

## 4. Adoption note for Phase 6

- [ ] 4.1 Document in the module docstring / a short `ARCHITECTURE.md` note that
      native 7z/RAR member streams MUST use `SharedSource` (or equivalent) when
      owning the archive handle.
- [ ] 4.2 Optional stretch: adopt `SharedSource` at one in-tree call site if a
      clean non-zipfile path exists; otherwise leave adoption to Phase 6 and keep
      the unit tests as the worked example.

## 5. Spec sync

- [ ] 5.1 Sync deltas into `openspec/specs/compressed-streams/spec.md`,
      `archive-reading/spec.md`, and `access-mode-and-cost/spec.md`.
- [ ] 5.2 `openspec validate --strict shared-source-streams` clean.

## 6. Gates

- [ ] 6.1 New unit + contract tests green.
- [ ] 6.2 Full `uv run --no-sync pytest` green.
- [ ] 6.3 `uv run --no-sync pyrefly check`, `ty check`, `ruff check` clean.
