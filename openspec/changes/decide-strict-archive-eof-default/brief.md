# decide-strict-archive-eof-default — pick the TAR end-of-archive strictness stance

**Status:** Decided + **implemented** — **Option F (signal-aware default)**. Depends on
nothing. Minor breaking: `nonzero` tars change from warn to raise. `config.py` default
stays `False`.

**Why it matters:** The founding inventory use case needs honest listings, but stdlib
tarfile can treat a corrupt mid-archive header as a clean end. Archivey’s trailer check is
the only backstop; a monolithic `strict_archive_eof` flip could not serve both inventory
honesty and the common trailer-less corpus.

**What it does:** Locks **Option F**: split the EOF diagnostic on the stop-block signal.
Rejected header (`nonzero`) → `CorruptionError` by default (RA via `_EofProbeStream`,
including final-block and GNU sparse last members; streaming via trailing-block when data
follows). Missing/short trailer (`absent`/`short`) → warn by default,
`TruncatedError` under `strict_archive_eof=True`. RA extract fails closed; streaming writes
salvageable members then raises.

**Decided:** Option F over A/D (leave `nonzero` silent), B/C (break trailer-less corpus),
and E (soft-extract report). Native TAR (P3) still owns the streaming final-header gap and
salvage precision; a salvage mode (`IDEAS.md`) is the future escape for reading a `nonzero`
tar without an exception.

**Cross-notes for later:** CLI (`cli-v1`) — `archivey test` should default to strict EOF
(validator = maximally paranoid). Open-issues P1 reworded to decided + implemented.

**Bottom line:** Applied in `tar_reader._verify_tar_eof` / `_EofProbeStream`; docs and
regression fixtures (incl. sparse final-header) land with the change.
