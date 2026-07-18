## Context

VISION claim (3): damaged input yields recoverable members **plus** an honest
error. Option F (`decide-strict-archive-eof-default`) made detectable TAR EOF
corruption raise (`CorruptionError` on rejected header; strict `TruncatedError`
on absent/short trailer). That closed the “silent shorten” honesty gap for the
error side.

What remains (api-coherence **Q7**): after a recoverable *prefix*, random-access
materialization still **fail-closes and discards** the prefix. Streaming
`__iter__` / `stream_members` / extract already **yield/write then raise**. RA
`__iter__` is not progressive today — it calls `_get_members_registered()` then
yields the snapshot — so RA iteration shares the fail-closed path.

Provenance: Q7 write-up in `review/api-coherence/QUESTIONS.md` (parked for the
freeze); maintainer explore decision **5+7** (report accessor + RA
publish/yield-then-raise aligned with streaming, without false-complete cache).

Concurrency N1 (archived deep review): never republish a partial member list as
a successful complete `_members_cache`.

## Goals / Non-Goals

**Goals:**

- A first-class **report** API that returns prefix members + terminal error +
  diagnostics without silencing either side.
- Keep `members()` / `scan_members()` as **complete-or-raise** (easy happy path,
  no incomplete list disguised as success).
- Align RA `__iter__` / `stream_members` with streaming: **yield prefix, then
  raise** on terminal archive-level errors.
- Preserve N1: incomplete materialization MUST NOT publish the complete-cache
  sentinel.
- Give CLI `list` a path to print the prefix and exit nonzero.

**Non-Goals:**

- Salvage / best-effort resync (`IDEAS.md`, `--salvage`) — reading *past* damage
  without a terminal error.
- Soft extract / Option E report field for archive-level EOF after writes — RA
  extract-prep stays fail-closed.
- Q5 / E2 `verify` / per-member `OnError.CONTINUE` on `stream_members`.
- Carrying the recovered prefix on the terminal exception (e.g.
  `error.recovered_members`) — as the primary surface *or* as an add-on. The
  prefix is reachable exactly one way (the report, plus the members yielded
  before a raise); the exception carries only the error. Two ways to get the same
  data is a worse surface than one obvious one (Decision 1).
- Kwargs on `members()` / diagnostics-only honesty.

## Investigations

### Caller-visible paths today (TAR rejected-header / Option F)

| API | Mode | Prefix visible? | Error? | Cache published? |
| --- | --- | --- | --- | --- |
| `members()` / `scan_members()` | RA | no | raise | no (`fail_materialization`) |
| `__iter__` | RA | no (materialize-first) | raise | no |
| `__iter__` / `stream_members` | streaming | yes (yielded) | raise after | no complete cache if raise before finalize |
| `extract_all` | RA | n/a (no writes) | raise in extract-prep | no |
| `extract_all` | streaming | files written | raise at end | — |
| `reader.diagnostics` alone | either | no members | advisory only if no raise | — |

### Why diagnostics-only fails Q7

Option F explicitly rejected “hope callers read diagnostics” for high-stakes
inventory. A soft incomplete `members()` return with only a diagnostic flag would
reintroduce silent shorten for anyone who ignores diagnostics. Diagnostics remain
a companion on the report, not the primary signal.

### ExtractionReport as the precedent

`extract*` already returns `ExtractionReport(results, diagnostics)` — an
immutable operation result. Listing’s analogous “I want outcomes + honesty” call
should follow that shape rather than overloading `list[ArchiveMember]`.

### RA progressive vs collect-then-yield

TAR’s terminal EOF check runs **after** the last recoverable member. Two
implementation shapes both satisfy yield-then-raise:

1. **Progressive:** yield each member as `_iter_members` produces it; on terminal
   error, propagate (streaming-like). Link resolution / `is_current` for the
   prefix need a defined moment (end of successful prefix, before raise).
2. **Collect-then-yield:** drain into a local list under try/except; resolve
   links on the prefix; yield all; re-raise. Simpler for today’s RA
   materialization loop; slightly less streaming-like for huge archives.

Prefer (2) for the first cut on RA (reuses registration/link helpers on a local
list without publishing cache); allow backends to move to (1) later if needed.
Streaming already does (1).

## Decisions

### 1. Dual listing surface: complete-or-raise vs report (option 5)

- `members()` / `scan_members()` → `list[ArchiveMember]`; on terminal archive
  error, **raise** and do not return a partial list.
- New `members_report() -> MemberListReport` **always returns**; `error is None`
  means the listing is complete; non-`None` means prefix + honest error. The
  name is deliberate: `members()` returns a list (complete-or-raise);
  `members_report()` returns a report (prefix + error) — same family as
  `ExtractionReport`, not a synonym of `members()`.

```python
@dataclass(frozen=True)
class MemberListReport:
    members: tuple[ArchiveMember, ...]
    error: ArchiveyError | None
    diagnostics: DiagnosticSummary
```

Mirror `ExtractionReport`: report may iterate/len/index as `members` for
ergonomics (optional; specs can require or leave as convenience — prefer yes for
CLI/parity).

**Rejected:** diagnostics-only; `members(raise_on_error=…)`; making `members()`
always return a report; exception `.recovered_members` in **any** form (primary
or add-on) — the prefix has one home, the report, and the terminal exception
carries only the error (two paths to the same data is a worse surface, see
Non-Goals); `list_members()` (reads as a synonym of `members()`).

### 2. RA yield-then-raise for iteration (option 7)

On terminal archive-level errors after a recoverable prefix, RA `__iter__` and
RA `stream_members` SHALL yield every recovered member (in order), then raise the
same error `members_report()` would put on the report. This matches streaming’s
caller-visible contract.

`members()` / `scan_members()` remain fail-closed (raise with no return) so the
name “members” never means “maybe incomplete.”

**Rejected:** keep RA iter fail-closed forever (leaves founding inventory needing
`streaming=True` just to see a prefix); publish `_members_cache` then raise
(false-complete — N1).

### 3. Materialization state: one stored report (N1)

Materialization produces **one** stored, immutable `MemberListReport` rather than
a separate complete `_members_cache` plus an incomplete side-table. Completeness
is a field on that report, not a matter of which slot is populated:

- `_report is None` — not materialized (no scan has run, no upfront index built).
- `_report.error is None` — complete, fully-resolved listing.
- `_report.error is not None` — recovered prefix + terminal archive-level error.

Every accessor derives from this one field, so N1 ("never publish a partial list
as a successful complete cache") becomes a **type invariant** instead of a rule
enforced by hand:

- `members()` / `scan_members()` → materialize, then `raise _report.error` if set,
  else return `list(_report.members)`.
- `__iter__` / `stream_members` → yield `_report.members`, then raise
  `_report.error` if set (yield-then-raise falls out for free).
- `get(name)` → *found → return; not-found and `error` set → raise the terminal
  error; not-found and complete → default*. Absence is only a definite "no" once
  the listing is complete.
- `members_report_if_available()` → the stored report if one exists (complete or
  incomplete) without scanning, else the upfront index as a complete report for
  `_MEMBER_LIST_UPFRONT` backends, else `None` (Decision 3a).
- `members_report()` → return `_report`.

Two guard rails keep the "everyone reads the report" model honest:

- **`_report.error` holds only terminal archive-level damage** (Option F
  `CorruptionError` / strict `TruncatedError` and analogous format checks).
  `ResourceLimitError` and interrupt-class exceptions
  (`KeyboardInterrupt` / `MemoryError` / `SystemExit`) MUST NOT be captured onto
  the report: they propagate and leave the reader **unmaterialized** (`_report`
  stays `None`, so a later call re-drives and re-raises). This is the same split
  the current `except BaseException: fail_materialization(); raise` already draws
  — the damage branch is routed into a stored report instead of re-raised.
- **Keep the public type clean.** `MemberListReport` is the public frozen result;
  the private name index (`_members_by_name_lists`) rides alongside it in an
  internal holder (e.g. `_Materialized(report, by_name_lists)`) so publication
  stays a **single immutable-reference store**. That removes the current
  "ORDER IS LOAD-BEARING" two-write publish discipline (name map before the
  `_members_cache` sentinel): a lock-free reader now sees either `None` or a
  fully-built holder.

Recovered `ArchiveMember` objects on the report are identity-stamped
(`member in reader`) so `open(member)` works for recovered `FILE` members without
pretending `members()` succeeded. Because the report is stored, a repeated
`members_report()` on the same reader **replays** it (cheap, deterministic; no
re-scan of a damaged source) — see Open Question 2.

Completeness (`error`) is orthogonal to link resolution: an upfront index or an
incomplete prefix may carry unresolved links; `error is None` means "the listing
is complete," not "links are resolved."

### 3a. `members_report_if_available()` returns a report, not just a complete list

Signature changes from `-> list[ArchiveMember] | None` to
`-> MemberListReport | None`. Rationale: on a **known-incomplete** listing the old
`None` return conflates "no cheap index for this archive" with "I have a prefix and
I know it is damaged," so a caller sizing a member count / progress bar before
iteration loses information it could use. Returning the report exposes both states:

- complete listing cheaply available (`_members_cache`, or an `_MEMBER_LIST_UPFRONT`
  index build) → report with `error is None`;
- incomplete listing **already materialized** by a prior pass → report with `error`
  set (the count is a *floor*, not the true total — the true total is unknowable
  past the damage);
- nothing materialized and a scan would be required → `None`.

It stays a peek: the incomplete case only returns a report if a prior pass already
stored it. `members_report_if_available()` still never triggers the scan that would
discover the damage, never reads member data, and never consumes the forward pass.
Because `MemberListReport` mirrors `ExtractionReport` sequence ergonomics
(iterate / `len` / index), the common progress-sizing callers (`len(report)`,
iterating) are unaffected; only list-specific ops / annotations change. N1 is
preserved because the report self-labels via `error` — handing it to a caller does
not make `members()` / `get()` return a partial list; those still raise.

### 4. What counts as a “terminal archive-level” listing error

In scope for yield-then-raise / report `error`:

- Archive-level EOF / corruption detected **after** recovering one or more
  members (TAR Option F rejected header; strict absent/short trailer; analogous
  future format checks).
- Mid-pass failures that abort further listing but leave a usable prefix
  (format-dependent; TAR mid-member truncation already raises during iteration —
  if no member was fully recovered, report may have `members=()` + error).

Out of scope / unchanged:

- Open-time failures (no reader / no members).
- Per-member *data* read errors during `stream_members` body reads (Q5/salvage).
- `ListingLimits` / `ResourceLimitError` (caps are not “damage”; keep raising
  without advertising a complete list — report MAY include the prefix under the
  cap policy; prefer raise-only for limits to avoid conflating bomb guards with
  damage). **Decision:** `ResourceLimitError` stays raise-only on
  `members()`/`members_report()`/`scan_members()`; do not soft-return a report with
  a limit error for v1 (limits ≠ VISION damage story).

### 5. Extract stays fail-closed on RA

RA `extract_all` materializes via extract-prep before writes. A terminal listing
error during that materialization SHALL still abort before writing (Option F).
Streaming extract keeps write-then-raise. Soft-extract archive-level status
remains deferred with salvage / Option E.

### 6. Streaming `members_report` / `scan_members` interaction

- `members_report()` on streaming: may run/finish the forward pass like
  `scan_members()`, return the report, and consume the pass. If the pass ends
  with a terminal error after a prefix, return prefix + error (do not raise out
  of `members_report()`).
- `scan_members()` on the same situation: **raise** (complete-or-raise), after
  the same prefix work — callers that need both use `members_report()`.

### 7. CLI

`archivey list` SHALL use `members_report()` (or equivalent): print recovered
members to stdout; if `error` is set, print a short stderr message and exit `1`.
`-v` still surfaces diagnostics. `test` may keep its hand-rolled loop for now
(Q5); optional follow-up to consume the report for open/list-phase failures only.

### 8. Record Q7 decision

Update `review/api-coherence/QUESTIONS.md` Q7 and `review/backlog.md` /
`STATUS.md` cross-links to point at this change (decision: report + RA
yield-then-raise).

## Risks / Trade-offs

| Risk | Mitigation |
| --- | --- |
| Callers treat `members_report().members` as complete without checking `error` | Docs + docstring; CLI shows the failure; type/docs stress `error is None` ⇒ complete |
| RA `__iter__` behavior change surprises code that expected fail-closed before any yield | Release note; rare path (only damaged archives); matches streaming / VISION |
| Identity/`open(member)` without complete cache creates a half-state | Spec the incomplete identity set explicitly; `get`/`members` stay loud |
| Two listing APIs to teach | Recipe: `members()` for assert-complete; `members_report()` for inventory/damage |
| Conflating limits with damage | Keep `ResourceLimitError` raise-only on listing APIs |

## Open Questions

1. **Should `MemberListReport` iterate as its members tuple?** Lean yes
   (ExtractionReport precedent). Confirm in specs.
2. **After incomplete RA scan, re-call `members_report` on the same seekable
   reader:** re-scan or replay the incomplete snapshot? **Decided: replay.** The
   single stored report (Decision 3) *is* the memo — repeated calls return it
   without re-doing I/O on a source the reader already assumes is stable (the
   complete-cache path assumes the same). A fresh scan only happens on reopen.
