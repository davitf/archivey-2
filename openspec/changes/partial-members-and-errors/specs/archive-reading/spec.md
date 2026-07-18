## ADDED Requirements

### Requirement: MemberListReport surfaces partial listings with terminal errors

The system SHALL expose an immutable listing report and a materializing accessor
that always returns both recovered members and any terminal archive-level error:

```python
@dataclass(frozen=True)
class MemberListReport:
    members: tuple[ArchiveMember, ...]
    error: ArchiveyError | None
    diagnostics: DiagnosticSummary

def list_members(self) -> MemberListReport: ...
```

`list_members()` SHALL recover every member the backend can list before a
terminal archive-level failure, put them in `members` (archive order), set
`error` to that failure or `None` when the listing is complete, and attach a
point-in-time `diagnostics` snapshot for the operation. It MUST NOT raise for
terminal archive-level listing errors covered by this requirement (those belong
on `error`). Open-time failures and `ResourceLimitError` from `ListingLimits`
SHALL still raise (limits are not the damage story).

`error is None` SHALL mean the listing is complete. Callers MUST treat a
non-`None` `error` as an incomplete listing even when `members` is non-empty.
The report SHALL iterate, index, and size as its `members` sequence (same
ergonomics as `ExtractionReport` vs its results).

Members in the report SHALL be identity-stamped for this reader (`member in
reader`) so `open(member)` works for recovered `FILE` members. The system SHALL
NOT publish them as a successful complete `_members_cache`: subsequent
`members()` / `scan_members()` / `get(name)` MUST still raise the terminal error
(or re-drive and raise) rather than return a silent partial list.
`get_members_if_available()` SHALL return `None` until a complete successful
materialization exists.

On `streaming=True`, `list_members()` MAY start or finish the single forward
pass (like `scan_members`) and thereby consume it; it still returns a report
instead of raising on terminal archive-level listing errors.

#### Scenario: list_members / MemberListReport matrix

| Case | Expected |
| --- | --- |
| Clean archive | `error is None`; `members` is the full fully-resolved list |
| TAR rejected mid/final header after prefix (Option F) | `members` = recoverable prefix; `error` is `CorruptionError`; no complete cache published |
| Strict absent/short trailer after prefix | `members` = prefix; `error` is `TruncatedError`; no complete cache |
| `list_members()` then `members()` on same RA reader after incomplete | `members()` raises the terminal error (not a partial list) |
| `open(report.members[i])` for a recovered FILE after incomplete | Succeeds by identity |
| `get(name)` after incomplete | Raises terminal error / does not pretend completeness |
| `get_members_if_available()` after incomplete only | `None` |
| `ListingLimits.max_members` exceeded during `list_members` | `ResourceLimitError` raised (not soft-returned on `error`) |
| Streaming `list_members` after recoverable prefix + terminal error | Report with prefix + error; pass consumed |

## MODIFIED Requirements

### Requirement: Sequential in-order iteration

```python
def __iter__(self) -> Iterator[ArchiveMember]: ...     # sequential, in-order
def members(self) -> list[ArchiveMember]: ...          # materialize (RA only)
def scan_members(self) -> list[ArchiveMember]: ...      # fully-resolved, either mode
def list_members(self) -> MemberListReport: ...         # prefix + error report
def get_members_if_available(self) -> list[ArchiveMember] | None: ...  # index peek
```

`__iter__` MUST yield in archive order without loading all members into a
*caller-visible* complete cache before the first yield when a terminal
archive-level error will follow a recoverable prefix. In **random-access** and
**streaming**, after yielding every recovered member, a terminal archive-level
listing error SHALL propagate (yield-then-raise). In **random-access**,
`members()` MAY scan formats without a central directory; after **successful
complete** materialization, later `__iter__` calls MUST use the cache. In
**streaming**, no cache-replay: `__iter__` is part of the single forward pass
(see `access-mode-and-cost`).

`members()` and `scan_members()` SHALL remain **complete-or-raise**: they return
a fully-resolved `list[ArchiveMember]` only when the listing completes; on a
terminal archive-level listing error they SHALL raise that error and MUST NOT
return a partial list. Prefer `list_members()` when both the prefix and the
error are required.

`scan_members()` SHALL return the fully-resolved list (`link_target_member`
filled where the target exists, incl. forward-pointing and last-wins symlinks)
when complete. In RA it equals `members()`. On `streaming=True` it returns the
cache if the pass completed successfully, else **finishes that pass** (from
start or draining an interrupted one), resolves links, and returns the list —
or raises on terminal archive-level listing error. It is the only
complete-or-raise method permitted after an iteration method has started;
running it consumes/finishes the pass.

A live forward pass leaves forward-pointing symlinks unresolved at yield time.
Completing a pass **successfully** via `__iter__`, `stream_members`,
`extract_all`, or `scan_members` SHALL finalize the cache in place on
already-yielded objects and make `get_members_if_available()` return it. An
abandoned pass (early `break`, no `scan_members()`) SHALL NOT finalize. A pass
that ends in a terminal archive-level listing error after a prefix SHALL NOT
publish a successful complete cache (see `MemberListReport` requirement).

No `__len__` / `__getitem__` (not a collection; protocols are probed implicitly —
`list(reader)` probes `__len__` for preallocation). `len(ar)` → Python `TypeError`
in every mode; use `len(ar.members())`, `ar.info.member_count`, or count while
iterating. `list(ar)` just iterates (and may raise after yielding a prefix).

`get_members_if_available()` is index-only: returns the list only when available
without scanning or reading member data, else `None`. Never scans or starts the
forward pass. Returned members may have unresolved links when targets live in
member data (see `access-mode-and-cost`).

With `streaming=True`, `members()` / `get()` / `open()` / `read()` SHALL raise
`UnsupportedOperationError` uniformly. Only one forward pass
(`__iter__`/`stream_members` or one `extract_all`) is allowed, with
`scan_members()` / `list_members()` to finish/return it and
`get_members_if_available()` anytime. Canonical access-mode × method table:
`access-mode-and-cost`.

#### Scenario: iteration / access-mode matrix

| Method / action | `streaming=False` | `streaming=True` |
| --- | --- | --- |
| `__iter__` | Yields in order; after successful complete materialization, from cache; terminal archive error → yield prefix then raise | Single-use forward pass; terminal archive error → yield prefix then raise; second `__iter__`/`stream_members`/`extract_all` → `UnsupportedOperationError` |
| `members()` | Full scan if needed; complete list or raise (no partial return) | `UnsupportedOperationError` |
| `scan_members()` | Same fully-resolved list as `members()` when complete; raise on terminal archive error | Finishes/drains pass; complete list or raise; pass consumed |
| `list_members()` | Always returns `MemberListReport` (prefix + `error`) | Always returns report; may consume the pass |
| `scan_members()` after early `break` | n/a | Drains remainder; complete list or raise on terminal error |
| `get_members_if_available()` after completed **successful** pass | List if indexed/cached | Fully-resolved list (not `None`); forward-link finalization visible on yielded objects |
| `get_members_if_available()` after abandoned or incomplete (error) pass | — / `None` | `None` |
| `len(ar)` | `TypeError` | `TypeError` |
| `list(ar)` | Iterates (may raise after prefix) | Iterates (consumes the single pass; may raise after prefix) |
