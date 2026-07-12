## Why

The random-access TAR reader (TAR-RA) was carved out as a "single shared decoder," and ISO
was left as library-owned outside SharedSource. Both are the same shape: **seek-before-read
on a shared handle, no lock** (`tarfile._FileInFile`, `pycdlib.PyCdlibIO`). Bypassing
`extractfile` with `SharedSource.view(offset_data, …)` would force archivey to reimplement
GNU sparse and other tarfile edge cases.

A raw-read-only wrapper is too narrow: TAR `getmembers()`/strict-EOF reads, member-open
initialization, ISO context entry, the complete member stream boundary (including supported
seek/tell and close), and archive close must be coordinated consistently. Correctness requires
one **per-reader lock around every shared-handle operation**, with no
provider/callback/diagnostic work under that lock. Required library-internal decode may remain
inside the atomic handle call.

> **Depends on `concurrent-member-streams`.** That change owns the cross-format
> `archive-reading` contract: safe simultaneous random-access member streams are guaranteed
> by construction, and concurrent worker `open()` is supported after materialization. This
> change is the TAR + ISO mechanism satisfying that contract.

## What Changes

- **Add a streamtools wrapper** that delegates to a library member stream and holds a
  caller-supplied lock across `read`, `readinto`, supported `seek`/`tell`, and close/context
  exit, preserving normal `io.UnsupportedOperation` for unsupported positioning.
- **TAR-RA:** one `TarReader` lock covers TarFile initialization, `extractfile` member
  creation, `getmembers()` (which reads via `_load()` / `next()`), Archivey's strict-EOF
  `fileobj.read()`, all wrapped member operations, and archive close.
- **ISO:** one `IsoReader` lock covers `PyCdlib.open` / `open_fp` initialization,
  `open_file_from_iso`, `PyCdlibIO.__enter__`, all wrapped member operations, other audited
  `PyCdlib._cdfp` / `PyCdlibIO._fp` operations, and archive close. Pinned pycdlib `walk()` /
  `get_record()` are verified in-memory catalog paths and get a version-regression audit rather
  than a misleading handle lock requirement.
- **No callback under the handle lock:** release it before exception translation/stamping,
  logging, lifecycle lease release, or any user callback/finalizer hook.
- **Document** the mechanism in `format-tar` and `format-iso`; the public worker/lifecycle
  contract remains owned by `concurrent-member-streams`.
- **Streaming TAR** stays single-pass and outside concurrent-open support; its shared-handle
  calls still use the same normally uncontended lock.
- Native TAR reader / SharedSource-at-`offset_data` remains a lower-priority future option.
- The comprehensive lock prioritizes correctness, not parallel throughput. Record a
  proportionate baseline (wall/lock timing and practical seek/byte counters) without a
  pass/fail threshold; later performance claims use targeted before/after measurements.
- No public API parameter is added *here*: the `member_streams` declaration is owned by
  `concurrent-member-streams`; this change is the TAR/ISO mechanism behind its
  `CONCURRENT` capability. This replaces the earlier TAR carve-out before 1.0.

## Capabilities

### New Capabilities

_(none)_

### Modified Capabilities

- `format-tar`: Random-access simultaneous/concurrent member streams via one comprehensive
  lock around stdlib `tarfile` shared-handle operations.
- `format-iso`: The equivalent comprehensive lock around pycdlib shared-handle operations.

> The `archive-reading` concurrent-open + reentrancy rewrites (dropping the TAR-RA
> exemption and defining the materialized worker seam/lifecycle) are owned by
> `concurrent-member-streams`, not this change.

## Impact

- Code: new streamtools helper; `tar_reader.py` and `iso_reader.py` member-open, member-I/O,
  and archive-close paths.
- Specs: `format-tar`, `format-iso` deltas. `archive-reading` / ABC docstring owned by
  `concurrent-member-streams`.
- Depends on: `concurrent-member-streams`.
- Docs: `docs/grab-bag/parallel-reader.md` TAR-RA and ISO audit rows.
- Tests: interleaved and threaded opens for plain/compressed TAR-RA and ISO; capability-
  conditional seek/tell/close races; `getmembers()`/EOF and pycdlib catalog audits; sparse
  TAR regression; sequential extract regression; callback and lazy-stream lock probes.
- Measurement: proportionate correctness-lock serialization baseline for TAR/ISO.
- Out of scope: reimplementing TAR sparse; native TAR reader; parallel extraction;
  changing single-file/ZIP SharedSource dispositions.
