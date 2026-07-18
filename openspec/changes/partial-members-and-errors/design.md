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
- Exception-payload-only design as the primary surface (optional later
  convenience, not required here).
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
always return a report; exception `.recovered_members` as the *only* surface;
`list_members()` (reads as a synonym of `members()`).

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

### 3. Incomplete materialization state (N1)

When a terminal archive error occurs after a prefix:

- Do **not** set `_members_cache` / complete name-index as a successful
  materialization.
- `get_members_if_available()` SHALL return `None` (or the prior *complete*
  cache if one existed from an earlier successful pass — should not apply on
  first failed TAR scan).
- Stamped `ArchiveMember` objects returned from `members_report()` or yielded
  before raise SHALL satisfy `member in reader` (identity) so `open(member)` can
  work for recovered FILE members without pretending `members()` succeeded.
- `get(name)` / `members()` after a failed incomplete scan SHALL still raise the
  terminal error (or re-drive the scan and raise) — they MUST NOT return a
  silent partial list.

Implementation sketch: hold an optional `_incomplete_members` / generation
token for identity + open-by-member only; never promote it to `_members_cache`
until a clean complete pass.

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
   reader:** re-scan or replay the incomplete snapshot? Lean **re-scan**;
   document.
