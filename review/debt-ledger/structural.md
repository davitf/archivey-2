# Structural debt — S1–S4 revisited, module seams, markers

Original line references: `main` @ `7bb862b`. **Status refresh 2026-07-23**
against `main` @ `8cc3ea5` (post-#184).

## S3 — one pass-stream driver — **DONE (#184)**

The 2026-07-12 `deep-simplification.md` S3 predicted the native RAR reader
would add a fourth copy of the "close previous / open current / yield /
cleanup tail" loop skeleton. It did. Debt-ledger **Q3 = (b)** paid that debt
before 0.2.0 via OpenSpec `unify-pass-driver` (#184):

- Shared driver: `BaseArchiveReader._drive_pass_streams` (`close_previous`,
  open hook, resource-cleanup `finally`; always closes the last stream in its
  own `finally`).
- Call sites: base default, TAR streaming (`close_previous=False` — tarfile
  owns invalidation), 7z solid, RAR solid — all `yield from` the driver.
- No fifth copy for the next backend: new readers supply hooks, not a new loop.

OpenSpec change is **✓ Complete** but still live under `openspec/changes/` —
archive under ledger **D7**.

## S2 — one finalize path — **DONE (#184)** with S3

Paid in the same change:

- One `_finalize_links` helper with a single double-fault policy
  (`error is not None` → swallow secondary Corruption/Truncated; clean EOF →
  re-raise). Eager materialization and progressive pass both call it (thin
  `_finalize_pass_links` wrapper preserved for call-site clarity).
- Mirrored guard-comment prose deleted.
- Drive loops remain two *shapes* (eager scan vs progressive iterator) by
  design — the risky duplicated *invariant* was the finalize guard, not the
  enumeration shape. `_get_members_index_only` stays a third pure-enumeration
  path.

Earlier half-paid work (shared stamper, publication, name-index) still holds.

## S1 — one error boundary: **paid, and it held** (fine; one small residue)

`_translated_errors` exists on the base and the newer paths kept it honest:

- TAR / ISO / ZIP use it at their open/member boundaries.
- RAR and 7z define only the `_translate_exception` hook and route raw-library
  errors through the shared `ArchiveStream` boundary — no hand-rolled
  translate/stamp/raise loops.
- Remaining direct `_stamp_error_context` calls are **origination** sites
  (typed password outcomes, `stamp=` lambdas into stream machinery). That is
  not the S1 disease; no action.

Residue: TAR `_translate_open_error` is a small hand-rolled translate-and-stamp
variant (returns instead of raising, adds a CorruptionError fallback). One
site, deliberate shape. **KEEP** — folding it into the boundary would need a
new keyword for the fallback; not worth it.

## S4 — ReaderState: reworked, not accreted (fine; verify note)

`reader_state.py` uses a consolidated `OperationToken` carrying `kind`
("root"/"child"/"worker") **and the acquiring thread** — the "owner fields,
not new bookkeeping" shape S4 asked for. No further action; if the file is
touched again, re-read S4 first.

## Module-split coherence — the splits are earning their seams (fine)

Checked each split the backlog named; every one carries a documented,
load-bearing rationale in its module docstring (`config` vs `internal.config`,
`measurement`, `extraction_types`, 7z quartet, `timestamps`, etc.). No
consolidation recommended. **KEEP all.**

## Markers, dead code, leftovers

- **In-code deferred markers: effectively one.** The O7 policy-gated rename
  follow-up in `extraction.py` — already a recorded threat-model residual. No
  stray `TODO`/`FIXME`/`XXX`/`HACK` markers. (fine)
- **`VerifyingStream` leftover:** post-#137 fusion, the standalone wrapper
  survives as the rapidgzip length backstop and unit-test subject — parked
  under `backlog.md` Topic 6. Still true after #183 (stdlib gzip moved off
  GzipFile onto `DecompressorStream`; accelerator backstop unchanged). **KEEP**
  until DD4 / Topic 6 retires the codecs backstop need.
- **Public surface:** deliberate two-tier export scheme in `__init__.py`. (fine)
