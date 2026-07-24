# Design — gzip truncation backstop for any seekable source

## Scope

Generalize `_GzipTruncationCheckStream` from **path-only** to **any declared-seekable source**.
No new public API; no format/detection change. The end state is that a bare seekable
`BinaryIO` of a truncated single-member gzip, read through rapidgzip with no container-declared
size, raises `TruncatedError` the same way a path source does today.

## Why the path restriction is incidental (code facts)

| Path use today | Generalization |
| --- | --- |
| `_verify_not_truncated` re-opens the path at EOF to read ISIZE | Capture ISIZE up front (already read by `_gzip_isize_from_source`; value currently discarded — keep it) |
| `_begin_stdlib_fallback` builds `GzipDecompressorStream(self._source_path)` from offset 0 | Rewind the seekable source (`seek(0)`) and hand it to the same stdlib engine — fallback only runs **after** `old.close()`, so no cursor conflict |
| multi-member magic scan opens a fresh handle | Seek the same source (save/restore position) — or, preferably, the sibling change `gzip-multimember-detect-via-index` removes this scan entirely |

`_config_with_gzip_isize` already calls `_gzip_isize_from_source` for any seekable source but
only stores `gzip_isize_backstop=True`. Plumbing the **int** (onto `StreamConfig` or the check
stream) is the core of the mechanism change.

## Obstacle 1 — accelerator must not close the caller's source

`_begin_stdlib_fallback` does `old.close()`, and `_AcceleratorStream.close()` closes the raw
rapidgzip object, which may close the file object rapidgzip was opened over. For a
**caller-owned** source archivey must never close it (same contract `BinaryIOWrapper` /
`_NonClosingBufferedReader` already enforce).

**Reuse the existing non-owning wrapper.** Feed rapidgzip a `_NonClosingBufferedReader`
(`ensure_bufferedio`) or `BinaryIOWrapper` view of the caller's source, so:

- `old.close()` stops rapidgzip's C++ worker (still required — see the lifecycle requirement)
  but leaves the underlying caller source open;
- the fallback can `seek(0)` that same source and re-decode.

For a **path** source nothing changes — archivey owns the fd and closes it.

## Obstacle 2 — rapidgzip Bug 3 (`terminate()` on a raising Python source)

`docs/internal/known-issues.md` Bug 3: rapidgzip can `terminate()` the **process** when a
*Python* source object raises during decode (undefined finalizer ordering across the C++/Python
boundary). This is why the truncation sweep deliberately used path sources only.

**It is not introduced by this change** — production already opens rapidgzip over non-path
seekable sources (`return stream` in `GzipCodec.open`), so the exposure exists today with *less*
safety, not more. But generalizing the backstop means we deliberately drive more file-object
traffic through the accelerator, so we should harden the boundary first.

**Proposed mitigation (to validate): an exception-trapping source shim.** Wrap the caller source
in an inner adapter whose `read`/`seek`/`readinto` **never raise into rapidgzip**: on an
underlying error it stores the exception, returns a benign EOF-shaped result (`b""` / short) to
the C++ layer, and the `_AcceleratorStream` outer wrapper checks for a stored exception after
each accelerator call and re-raises it (translated). This converts a process-abort into a normal
Python exception on the archivey side.

Open questions this raises (measure before committing):

- Does returning `b""` to rapidgzip on a trapped inner error reliably produce a clean stop
  (soft-EOF), or can it still wedge a worker thread? Needs the same wall-clock-timeout sweep the
  investigation used.
- Is the trap needed at all for *seekable file* sources, or is Bug 3 specific to sources that
  themselves raise (vs. plain truncated bytes, which do not raise — they just end)? If a
  `BytesIO`/file of truncated bytes never raises, Bug 3 may not fire for the exact case this
  change targets, and the trap becomes belt-and-suspenders rather than a prerequisite.

## Open decision (for the maintainer)

**Do we gate rapidgzip-over-file-objects, and how?** Options:

- **(a)** Land the trap shim, then enable the backstop on any seekable source unconditionally.
- **(b)** Keep using rapidgzip on file objects as today but add the backstop, treating the trap
  as a separate hardening change (accept the pre-existing Bug-3 exposure unchanged in the
  interim).
- **(c)** Add a config axis "prefer speed vs. absolute robustness" that decides whether
  file-object sources use rapidgzip at all (the maintainer's framing), with this backstop active
  whenever rapidgzip is chosen.

Leaning **(b)** for the backstop itself (it strictly improves safety and does not widen Bug-3
exposure beyond today), with the trap shim and/or (c) as a **follow-up** — but this is the
maintainer's call and the reason this change ships as investigation + specs first.

## Testing

- Truncated single-member gzip from a `BytesIO` / caller file object, `use_rapidgzip=ON`, no
  declared size → `TruncatedError` (parity with the existing path-source tests in
  `tests/test_accelerator_corruption.py`).
- Caller-owned source is **still open and readable** after the archivey stream closes
  (non-owning wrapper) — mirror `test_ensure_bufferedio_does_not_close_raw_source`.
- Empty→stdlib fallback over a rewound `BytesIO` recovers the same prefix a path source does.
- Bug-3 sweep with a raising file object behind rapidgzip under a wall-clock timeout (no process
  abort) — reuse `scripts/rapidgzip_truncation_sweep.py` shape.
- Three dependency configs (`[all]`, `[all-lowest]`, `[core-only]`); rapidgzip-gated tests skip
  cleanly on core-only.
