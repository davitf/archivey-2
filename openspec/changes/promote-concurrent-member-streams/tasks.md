## 1. Marker and CI

- [x] 1.1 Register the `concurrent_reader` pytest marker in `pyproject.toml`
- [x] 1.2 Mark directory, ZIP, single-file stdlib, SharedSource, lifecycle/operation-state,
      and TAR concurrent/cooperative tests with `@pytest.mark.concurrent_reader`
- [x] 1.3 Add required Linux `free-threaded-concurrency` job to `.github/workflows/ci.yml`
      (`uv python install 3.13t`, core-only sync, `pytest -m concurrent_reader`); job fails
      if free-threaded Python cannot be installed

## 2. Multi-thread stress

- [x] 2.1 Multi-thread concurrent open/read/close after `members()` for directory and ZIP
      (exact bytes)
- [x] 2.2 Multi-thread concurrent open/read for stdlib single-file / SharedSource paths
      where applicable
- [x] 2.3 Multi-thread concurrent open/read for plain TAR-RA and at least `.tar.gz` under
      `CONCURRENT`
- [x] 2.4 Multi-thread concurrent open/read for ISO under `CONCURRENT` (skip cleanly without
      `pycdlib`; not claimed in core-only `3.13t` job)
- [x] 2.5 Forced race probes: archive/member close cannot interrupt an active shared-handle
      operation under the supported lifecycle sequence; callback/logging probes stay outside
      the backend lock

## 3. Baseline measurement

- [x] 3.1 Add a non-gating TAR/ISO lock baseline recipe/script (wall + wait/hold where
      practical; seek/byte counters when cheap) under `benchmarks/` or documented in
      `docs/grab-bag/parallel-reader.md`
- [x] 3.2 Record a dated sample run in the docs or script output comments (no pass/fail
      threshold)

## 4. Docs: drop provisional status

- [x] 4.1 Update `MemberStreams` / reader docstrings to describe the supported seam
- [x] 4.2 Update `openspec/project.md`, `SPEC.md`, `ARCHITECTURE.md`, `IDEAS.md`,
      `docs/grab-bag/parallel-reader.md`, and threat-model C4 accordingly
- [x] 4.3 Sync delta specs into main `openspec/specs/` for the four modified capabilities

## 5. Verification

- [x] 5.1 `openspec validate --strict promote-concurrent-member-streams`
- [x] 5.2 `uv run --no-sync ruff check` / `ruff format --check` on touched paths
- [x] 5.3 `uv run --no-sync pyrefly check` and `uv run --no-sync ty check`
- [x] 5.4 Focused + full pytest; attempt local `3.13t` marked run when the toolchain is
      available; three-config push gate per `CONTRIBUTING.md`
