## Context

#59 landed declared `MemberStreams.CONCURRENT` / `SEEKABLE` with load-bearing
correctness machinery (tokens, leases, single-live-stream gate, TAR/ISO handle lock)
under a **provisional** cooperative guarantee (D15). Spec deltas were synced to main,
so `packaging-and-extras` and `testing-contract` already require a Linux `3.13t`
`free-threaded-concurrency` job and free-threaded stress — but CI has no such job, and
the heavier multi-thread TAR/ISO / lock-order tests remain unchecked deferred reminders
in the archived changes.

## Goals / Non-Goals

**Goals:**

- Promote `CONCURRENT` from provisional to supported by landing the deferred free-threaded
  CI job and multi-thread stress coverage.
- Align prose/docstrings with the supported claim (drop "provisional").
- Record a proportionate TAR/ISO lock baseline without inventing a speed gate.
- Keep optional backends honest: skip ≠ free-threaded support claim.

**Non-Goals:**

- Native 7z/RAR concurrent-open implementation (still blocked on those readers).
- Parallel extraction / scheduling / throughput guarantees.
- Making the `ArchiveReader` object fully thread-safe (iteration, materialization,
  extraction, close remain single-owner).
- Changing the public `member_streams` API shape.
- Independent-handle / raw-extent TAR/ISO optimizations (measurement-only before any
  such claim).

## Decisions

### D1. New change, not reopen archived tasks

`concurrent-member-streams` / `tar-concurrent-open` are archived. This change owns the
promotion work and cites the deferred task IDs as provenance. Archival deferred
checkboxes stay historical reminders.

### D2. Required Linux `3.13t` job, core-only + marker

Match the archived 7.8 recipe:

```text
uv python install 3.13t
uv sync --python 3.13t --no-dev
uv run --python 3.13t --no-sync --with pytest --with pytest-timeout pytest -m concurrent_reader
```

Zero-dep core keeps the free-threaded claim scoped to always-available backends
(directory, ZIP, stdlib single-file, SharedSource, TAR plain). Optional wheels that fail
to install on `3.13t` are excluded from the claim.

### D3. `concurrent_reader` marker registration

Register in `pyproject.toml` / `pytest` config and apply to tests that exercise the
promoted seam (gate + concurrent open/read + lifecycle tokens + TAR lock). Ordinary
`[all]` CI continues to run the full suite including these tests on GIL builds.

### D4. Multi-thread stress is correctness, not microbench

Thread pools / barriers that interleave `open`/`read`/`close` (and supported
seek/tell under `SEEKABLE`) on distinct members after `members()`. Assert exact bytes
and that documented misuse still raises `ArchiveyUsageError` /
`ConcurrentAccessError`. No wall-time assertions in pytest.

### D5. Baseline measurements are informational

A small script (or documented recipe) records TAR/ISO lock wall and wait/hold samples
under representative loads. Checked into `benchmarks/` or referenced from
`docs/parallel-reader.md`. No CI fail threshold; later perf claims need before/after of
the same recipe.

### D6. Provisional language removal is documentation-only for the API

`MemberStreams.CONCURRENT` semantics are unchanged; only the support status and CI
backing change. Docstrings and prose that said "provisional in v1" become the supported
cooperative + free-threaded-tested description.

## Risks / Trade-offs

- **`3.13t` toolchain flakiness** → pin the job to Ubuntu; fail the job on install failure
  rather than silently skip; document known uv/python version.
- **False confidence from skipped optional tests** → marker docs and job summary must
  state which backends are claimed; skipped optional ≠ covered.
- **Stress flakiness on shared runners** → keep stress bounded (small member counts,
  short timeouts); prefer deterministic barriers over long soak loops.
- **Baseline bitrot** → store methodology + sample numbers with date; re-run before any
  performance claim rather than treating samples as eternal truth.

## Migration Plan

1. Land marker + stress tests (green on ordinary CI).
2. Add the `3.13t` job; fix any free-threaded races it exposes.
3. Drop provisional wording; update threat-model C4 if needed for clarity.
4. Record TAR/ISO baseline notes.
5. Sync deltas → archive this change.
