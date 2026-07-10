## Why

The random-access TAR reader (TAR-RA) was carved out as a "single shared decoder," and ISO
was left as library-owned outside SharedSource. Both are the same shape: **seek-before-read
on a shared handle, no lock** (`tarfile._FileInFile`, `pycdlib.PyCdlibIO`). Bypassing
`extractfile` with `SharedSource.view(offset_data, …)` would force archivey to reimplement
GNU sparse and other tarfile edge cases. A thin **locked member-stream wrapper** (one
per-archive lock, held for the duration of each library `read()`) keeps tarfile/pycdlib
logic and makes opted-in interleaved opens correct for TAR-RA and ISO.

> **Depends on `concurrent-open-opt-in`.** That change owns the `archive-reading` rewrite:
> multiple simultaneously-open member streams are an opt-in, format-uniform capability
> (`allow_multiple_open_streams`), and it drops the blanket TAR-RA concurrent-open exemption.
> This change is the **TAR + ISO mechanism** that runs *under* that opt-in; it does not edit
> `archive-reading` itself.

## What Changes

- **Add a streamtools wrapper** that delegates to an inner member stream and holds a
  caller-supplied lock across each data-path `read` / `readinto` (so the library's
  seek+read stays atomic).
- **TAR-RA:** keep `extractfile`; wrap the returned stream with that helper and a
  per-`TarReader` lock before `_wrap_member_stream` (when the caller has opted in, or
  always — uncontended lock is cheap; gate enforcement still lives in the opt-in change).
- **ISO:** wrap pycdlib member streams the same way with a per-`IsoReader` lock.
- **Document** the mechanism in `format-tar` and `format-iso`; concurrent-open contract /
  gating stays in `concurrent-open-opt-in`.
- **Streaming TAR** stays single-pass / out of scope.
- Native TAR reader / SharedSource-at-`offset_data` remains a lower-priority future option.
- No public API break. **Not BREAKING.**

## Capabilities

### New Capabilities

_(none)_

### Modified Capabilities

- `format-tar`: Random-access concurrent member open via locked `extractfile` streams
  (under `allow_multiple_open_streams`).
- `format-iso`: Concurrent member open via the same locked-wrapper pattern around pycdlib
  streams (under the same opt-in).

> The `archive-reading` concurrent-open + reentrancy rewrites (dropping the TAR-RA
> exemption, adding the opt-in gate, clarifying library-owned lock-wrap compliance) are
> owned by `concurrent-open-opt-in`, not this change.

## Impact

- Code: new streamtools helper; `tar_reader.py` and `iso_reader.py` `_open_member` paths.
- Specs: `format-tar`, `format-iso` deltas. `archive-reading` / ABC docstring owned by
  `concurrent-open-opt-in`.
- Depends on: `concurrent-open-opt-in`.
- Docs: `docs/parallel-reader.md` TAR-RA and ISO audit rows.
- Tests: interleaved opens (opted-in) for plain/compressed TAR-RA and ISO; sparse TAR
  still correct; sequential extract regression.
- Out of scope: reimplementing TAR sparse; native TAR reader; parallel extraction;
  changing single-file/ZIP SharedSource dispositions.
