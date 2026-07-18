## MODIFIED Requirements

### Requirement: Access-mode enforcement — streaming is forward-only

On `streaming=True`, `members()` / `get()` / `open()` / `read()` SHALL raise
`UnsupportedOperationError` uniformly. No `__len__`/`__getitem__`
(`archive-reading`); `member in reader` is scan-free identity membership (both
modes).

Forward-pass entry points: `__iter__`, `stream_members`, `extract_all`. The first
consumes the single pass; any later call to any of them SHALL raise — even after
completion (no streaming `__iter__` cache-replay). Early `break` still consumes.
Member selection for extraction is `extract_all(members=...)` (`safe-extraction`).

`scan_members()` MAY run before the pass (starts+finishes it), after an interrupted
pass (drains remainder), or after completion (returns cache). Starting the pass
consumes it. `list_members()` MAY likewise start or finish the pass and consumes
it; it returns `MemberListReport` instead of raising on terminal archive-level
listing errors (`archive-reading`). `get_members_if_available()` never
begins/advances/consumes the pass.

On both access modes, `__iter__` and `stream_members` SHALL yield every
recovered member before propagating a terminal archive-level listing error
(yield-then-raise). `members()` / `scan_members()` remain complete-or-raise.

#### Scenario: streaming enforcement matrix

| Case | Expected |
| --- | --- |
| `get` / `members` / `open` / `read` on `streaming=True` | `UnsupportedOperationError` |
| First `__iter__` or `stream_members` | Yields in archive order |
| Terminal archive error after prefix (either mode) | Prefix yielded; then raise |
| Second forward-pass method after begin/complete | `UnsupportedOperationError` (all formats) |
| Early `break` then `scan_members()` | Drains remainder; fully-resolved list or raise; later pass methods raise |
| `scan_members()` then `stream_members()` on fresh streaming reader | List returned when complete; subsequent pass raises (any index topology) |
| `list_members()` on streaming with terminal archive error after prefix | Report with prefix + `error`; pass consumed; no raise from `list_members` |

### Requirement: Access mode × method behaviour summary

The system SHALL behave per this canonical table (`✅` allowed,
`⛔` → `UnsupportedOperationError`):

| Method | `streaming=False` | `streaming=True` |
| --- | --- | --- |
| `__iter__` | ✅ repeatable after **successful** complete cache; yield-then-raise on terminal archive error | ✅ **once** (no replay); yield-then-raise on terminal archive error |
| `stream_members` | ✅; yield-then-raise on terminal archive error | ✅ once; yield-then-raise |
| `extract_all` | ✅; RA extract-prep fail-closed on terminal listing error | ✅ once; streaming write-then-raise |
| `scan_members` | ✅ (= `members`); complete-or-raise | ✅ finishes/returns pass; complete-or-raise |
| `list_members` | ✅ always returns `MemberListReport` | ✅ may consume pass; always returns report |
| `get_members_if_available` | ✅ index-only (may be `None`); `None` if only incomplete | ✅ index-only, no-consume |
| `members` / `get` / `open` / `read` | ✅; `members`/`get` complete-or-raise | ⛔ |
| `in` (identity) | ✅ no scan (incl. recovered report members) | ✅ no scan |
| `cost` / `info` / `format` / `close` / CM | ✅ | ✅ |
| at `open_archive()` | fail fast if source not RA-capable | any source |

In streaming mode, `__iter__` / `stream_members` / `extract_all` share one pass.
Backend `_SUPPORTS_RANDOM_ACCESS` may also force `open`/`read` to raise; it
composes with — does not replace — these rules.

#### Scenario: summary checks

| Case | Expected |
| --- | --- |
| `scan_members()` either mode on clean archive | Fully-resolved list (RA ≡ `members()`; streaming finishes pass) |
| Full streaming `__iter__`, then iterate again | Second → `UnsupportedOperationError` |
| RA `__iter__` on TAR rejected-header after prefix | Yields prefix members, then `CorruptionError` |
| `list_members()` row present either mode | ✅ returns report |
