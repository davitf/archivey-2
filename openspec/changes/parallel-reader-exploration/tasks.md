# Tasks — Exploration: parallel-safe reader

> This is an **exploration** with one committed spec outcome (the `_open_member` reentrancy
> invariant) plus a written analysis feeding a future parallel-extraction change. Run tools
> through uv. Reference: `src/archivey/internal/base_reader.py` (reader state),
> `openspec/changes/shared-source-streams` (the shared-source primitive this builds on),
> `docs/threat-model.md` C4 (free-threading), `VISION.md` (benchmark-before-claims),
> `openspec/project.md` (parallel extraction is a v1-deferred feature).

## 0. Framing / decisions

- [ ] 0.1 **Commit the interface invariant now; defer the feature** — lock `_open_member`
      reentrancy before Phase 6 writes new backends; parallel extraction stays a future change.
- [ ] 0.2 **Target regime is random-access, member-list-materialized** — streaming/forward-pass
      is inherently sequential and out of scope.
- [ ] 0.3 **Decide the feature with numbers** — no parallel-extraction implementation until the
      benchmark shows a real win under both GIL and free-threaded builds.

## 1. Committed: the `_open_member` reentrancy invariant (scoped to random-access)

- [ ] 1.1 Add the `archive-reading` delta (this change's spec) — random-access backend
      member-open is reentrant and reader-state-free, byte access via a shared-source view;
      streaming and single-decoder (TAR-RA) backends are out of scope. **Land after / synced
      with `shared-source-streams`** (the delta references its concurrent-open requirement).
- [ ] 1.2 **Audit existing backends** (`directory`, `zip`, `tar`, `iso`, `single_file`)
      `_open_member` against the invariant; record compliance. Known: directory/ZIP largely
      comply; `single_file._first_stream` scratch and ISO shared-handle seeks are the gaps;
      TAR-RA is exempt (single shared decoder).
- [ ] 1.3 **Fix ownership is explicit, no overlap:** the `single_file` scratch fix and the ISO
      disposition are owned by **`shared-source-streams`** (single-file retrofitted there; ISO
      carved out there). This change only *records* them — it does not re-fix them, so the two
      changes don't fight.
- [ ] 1.4 Make the invariant discoverable by a Phase 6 implementer: docstring on the ABC
      `_open_member` **including the materialize-before-fan-out precondition**, plus a pointer
      from `ARCHITECTURE.md`. (This is the committed doc note; it is small and lands here.)

## 2. Explored (written analysis; NO feature code)

> Home for the write-up: this change's `design.md` (decisions) + a seed `docs/parallel-reader.md`
> (the durable analysis the future `parallel-extraction` change starts from). No runtime code in
> this section.

- [ ] 2.1 **Member-cache one-time-build safety** — sketch the safe options (init-under-lock vs.
      "materialize-before-fan-out precondition") and recommend one; note it is small and
      independent of the feature. (The precondition itself is already documented via task 1.4.)
- [ ] 2.2 **Benchmark design** — define the workloads and metrics for deciding the feature:
      wall time, bytes-decompressed, seek counts; DIRECT (non-solid) archives vs. multi-folder
      solid 7z; C-codec vs. pure-Python decode; GIL build vs. 3.13t. Home: `benchmarks/`.
- [ ] 2.3 **Free-threading position** — draft the stance for `docs/threat-model.md` C4: what
      parallel decode/extract looks like under 3.13t, and the C++-accelerator-thread caveat.
- [ ] 2.4 **Work-partitioning per format** — document the parallelizable unit (7z: folder;
      RAR: solid block; ZIP/DIRECT: per-member) so a future coordinator knows the granularity.
- [ ] 2.5 **"N readers over one path" vs. "one reader + shared source"** — weigh the two
      parallel models (independent handles + independent reader state vs. shared source + lock)
      and recommend, including how each interacts with the `ExtractionCoordinator`.
- [ ] 2.6 **Recommendation** — if the analysis finds the ABC needs more than the §1 invariant
      to avoid a retrofit, state exactly what, so it can be decided before Phase 6 locks the ABC.

## 3. Outcome

- [ ] 3.1 `openspec validate --strict parallel-reader-exploration` passes.
- [ ] 3.2 The written analysis (§2) is captured (proposal + a docs note) as the seed for a
      future `parallel-extraction` change; that feature is explicitly **not** implemented here.
- [ ] 3.3 Pyrefly + ty + ruff clean (only if any audit-driven tightening lands).
