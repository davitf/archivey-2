## Context

Provenance: `review/debt-ledger/structural.md` (S2/S3), `review/archive/2026-07-12-codebase-deep-review/deep-simplification.md` (original S2/S3), debt-ledger **Q3** maintainer decision **(b) pay pre-release** (2026-07-20). Fuzzing-before-Phase-6 is the analogous entry-gate precedent; here the gate is inverted — pay *now*, before any fifth backend is contemplated.

Today’s S3 copies (`_iter_with_data`):

| Backend | Close previous? | Last stream on exhaust | Extra resources |
| --- | --- | --- | --- |
| Base (ZIP/dir/ISO/nonsolid RAR) | yes | leave open (`stream_members` finally) | none |
| TAR streaming | **no** (tarfile invalidates) | n/a | none |
| 7z | yes | close in `finally` | `SolidBlockReader` / folder swap |
| RAR solid | yes | close in `finally` | `unrar p` pipe + `SolidBlockReader` + `pipe_offset` |

Outer `stream_members` always owns pass acquire/release and closes the *current* handle in its `finally` — so 7z/RAR may double-close the last stream (must stay idempotent).

S2 today: `_materialize_members` vs `_ProgressivePassIterator` + `_finalize_materialized_links` vs `_finalize_pass_links`, with near-identical double-fault comments. Shared already: `_register_member`, `_stamp_progressive_member` → register, `_publish_materialized`, `_index_member_name`.

## Goals / Non-Goals

**Goals:**

- Delete the *category* of hand-rolled close-previous loops and mirrored finalize guards.
- Encode ownership (close_previous / leave_last_open / pass resources) in one place.
- Land T1 solid-RAR mutation **before** touching demux loops.
- Preserve all must-not-break behaviors listed in the exploration map (solid RAR/7z, progressive TAR, double-fault, stream_members close/ownership).

**Non-Goals:**

- Lazy `ArchiveMember` derivation (L5) — separate, deferred.
- Changing public `stream_members` / `members()` / `scan_members()` contracts.
- Teaching the declarative RAR corpus builder `-s` (optional later; T1 uses static fixtures).
- Unifying `_get_members_index_only` away (genuinely different; keep).
- Making TAR start tracking `previous` (rejected — tarfile owns that).

## Investigations

Four-copy map and S2 overlap confirmed against `main` @ post-#171 tree (see agent exploration 2026-07-20). Materialization is **not** always a drained data pass:

1. RA indexed: materialize metadata first, then lazy opens.
2. Streaming TAR: progressive pass *is* the forward pass; data co-travels.
3. Solid 7z/RAR: index upfront; demux loop is separate from listing.

So “one drive loop” means one *stamp/finalize/publish* path and one *stream-pair* driver with hooks — not forcing every backend’s data demux to be the listing mechanism.

T1 gap: `test_mutation_fuzz.py` `_PARAMS` iterates declarative `CORPUS` only; `_rar_build` has no `-s`. Static `tests/fixtures/rar/basic_solid__.rar` (+ rar4) already exist for example tests.

## Decisions

### 1. Pay S2+S3 before 0.2.0 (Q3 = b)

Not an entry gate for native ZIP. Clean structure preferred; suite is the regression net.

**Rejected:** (a) entry gate until next backend — would leave four copies shipping.

### 2. T1 before structural edit

Add mutation params over static solid RAR4/RAR5 fixtures (reuse `_exercise` / kinds / timeout). Skip when `unrar` absent via existing runnable/skip patterns.

**Rejected:** only rely on existing example tests; **Rejected:** flip all CORPUS RAR to `-s` in this change (blast radius on oracles/sizes).

### 3. Shared stream driver API (S3)

Introduce a single helper on `BaseArchiveReader` (name TBD in implement, e.g. `_drive_pass_streams`) roughly:

- Iterate members.
- Optionally close previous on advance (`close_previous`).
- Call a per-member open hook → `ArchiveStream | None`.
- On exit: optional resource cleanup hook; optionally close last stream (`leave_last_open`).

Backend `_iter_with_data` becomes: obtain member source + resource state, then `yield from` the driver with a closure/hook for open (7z folder swap and RAR `pipe_offset` live in the hook).

| Hook / flag | Base | TAR stream | 7z | RAR solid |
| --- | --- | --- | --- | --- |
| member source | progressive or materialized | progressive | `_members` | `_members` |
| open hook | `_lazy_member_stream` | `extractfile` wrap | folder swap + lazy solid | `pipe_offset` + lazy solid |
| `close_previous` | True | False | True | True |
| `leave_last_open` | True | True | False | False |
| resource `finally` | none | none | close solid | close solid + clear `_live_unrar` |

**Rejected:** force TAR onto close-previous tracking. **Rejected:** five abstract classes / strategy objects — keep one helper + closures.

### 4. Shared link finalize (S2)

One `_finalize_links(members, by_name_lists, *, error, child_scope, enforce_limits)` (exact shape at implement time) used by both eager and progressive paths. Double-fault policy: swallow secondary `CorruptionError`/`TruncatedError` when `error is not None`; re-raise when finalizing a clean EOF (`error is None`) — matching today’s progressive semantics; eager incomplete path always has `error` set.

Eager success path: finalize with `error=None` and no swallow. Progressive clean EOF: same. Progressive/eager incomplete: swallow secondary damage.

**Rejected:** keep two finalizers with “see other comment” prose. **Rejected:** changing when `is_current` is applied relative to link resolve without a failing test forcing it — preserve order per path as today unless tests allow convergence.

### 5. No public spec delta in this change

Behavior-preserving internal refactor + test widening. If a public contract accidentally changes, that is a bug, not a spec edit.

**Rejected:** writing speculative `archive-reading` deltas “to document the driver” before behavior is proven identical.

### 6. Do not record PLAN entry-gate language

Q3=(b) supersedes any draft that added S2+S3 as a native-ZIP entry gate (`PLAN.md` / `IDEAS.md`). Those edits must not land (PR #173 closed).

## Risks / Trade-offs

| Risk | Mitigation |
| --- | --- |
| Silent solid demux / pipe_offset skew | T1 mutation first; solid RAR/7z + measurement decode-once tests |
| Double-close / leave-last-open mismatch | Explicit flags; cooperative close tests; idempotent `ArchiveStream.close` |
| Progressive vs eager finalize order drift | Port existing call sites carefully; double-fault contract tests |
| TAR streaming regresses without previous-close | `close_previous=False`; TAR materialize-from-pass tests |
| Scope creep into L5 / corpus `-s` | Non-goals; keep PR focused |

## Open Questions

1. Exact helper name / whether it is a nested function vs method — implementer’s call; no API surface.
2. Whether to add a third static fixture (`symlinks_solid__.rar` / `file_version_solid__.rar`) in T1 wave-1 or wave-2 — default wave-1 = two basic solid files only.
