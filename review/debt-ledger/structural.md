# Structural debt — S1–S4 revisited, module seams, markers

Original line references: `main` @ `7bb862b`. **Status refresh 2026-07-24**
against `main` @ `bdf5ffd` (post-#184 / #191).

## S3 — one pass-stream driver — **DONE (#184)**

Paid via OpenSpec `unify-pass-driver` (Q3 = b):

- Shared driver: `BaseArchiveReader._drive_pass_streams` (`close_previous`,
  open hook, resource-cleanup `finally`; always closes the last stream in its
  own `finally`).
- Call sites: base default, TAR streaming (`close_previous=False`), 7z solid,
  RAR solid — all `yield from` the driver.

OpenSpec change is **✓ Complete** but still live under `openspec/changes/` —
archive under ledger **D7**.

## S2 — one finalize path — **DONE (#184)** with S3

- One `_finalize_links` helper with a single double-fault policy.
- Eager materialization and progressive pass both call it.
- Drive-loop *shapes* remain two by design; the risky duplicated *invariant*
  was the finalize guard.

## S1 — one error boundary: **paid, and it held** (fine; one small residue)

RAR/7z route through the shared boundary. Remaining direct
`_stamp_error_context` calls are origination sites. TAR
`_translate_open_error` residue: **KEEP**.

## S4 — ReaderState: reworked, not accreted (fine)

Owner-carrying `OperationToken`; no further action.

## Module-split coherence (fine)

`config` vs `internal.config`, `measurement`, `extraction_types`, 7z quartet,
`timestamps` — each earns its seam. **KEEP all.**

## Markers, dead code, leftovers

- Essentially one in-code deferred marker (O7 rename follow-up) — registered.
- **`VerifyingStream` leftover:** rapidgzip length backstop + unit tests —
  parked under Topic 6 / DD4 adjacency. **KEEP**.
- Public two-tier `__init__.py` exports deliberate. (fine)
