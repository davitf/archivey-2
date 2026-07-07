## MODIFIED Requirements

### Requirement: Access-mode enforcement — streaming is forward-only

A reader opened with `streaming=True` is forward-only. The system SHALL raise `UnsupportedOperationError` from every random-access or full-materialization method — `members()`, `get()`, `open()`, and `read()`. (`ArchiveReader` defines no `__len__`/`__getitem__` at all — see `archive-reading` — and `member in reader` is scan-free identity membership, allowed in both modes.) This holds **uniformly**, regardless of whether the backend happens to have an index loaded, so streaming behaviour does not vary by format.

The source is traversed **at most once**, forward. The system SHALL treat `__iter__`, `stream_members`, and `extract_all` as the forward-pass entry points: the first of them to run consumes the single pass, and any subsequent call to **any** of them SHALL raise `UnsupportedOperationError` — uniformly for every format, and even after the first pass ran to completion (there is **no** cache-replay of `__iter__` in streaming mode; a caller that wants the list again uses `scan_members()` or `get_members_if_available()`). A pass abandoned before EOF (an early `break`) still counts as consumed. (There is no single-member `extract()`; selecting members for extraction is `extract_all(members=...)` — see `safe-extraction`.)

`scan_members()` is the sole exception: it MAY run before the pass (initiating and finishing it), after an *interrupted* pass (finishing the remainder internally), or after a completed pass (returning the cache). It returns the fully-resolved member list. When it initiates the pass it also consumes it, so a later `__iter__`/`stream_members`/`extract_all` SHALL raise.

`get_members_if_available()` neither begins nor advances the forward pass and never marks it consumed (see the next requirement), so it remains callable on any reader at any time.

#### Scenario: random access raises on a streaming reader

- **WHEN** any of `ar.get("f")`, `ar.members()`, `ar.open(m)`, or `ar.read(m)` is called on a reader opened with `streaming=True`
- **THEN** `UnsupportedOperationError` is raised

#### Scenario: a single forward pass is allowed on a streaming reader

- **WHEN** a `streaming=True` reader is iterated once via `__iter__` or `stream_members()`
- **THEN** members are yielded in archive order without error

#### Scenario: a second forward pass raises uniformly, even after completion

- **WHEN** a `streaming=True` reader has begun or completed one forward pass (via `__iter__`, `stream_members`, or `extract_all`) and any of those forward-pass methods is called again
- **THEN** `UnsupportedOperationError` is raised, regardless of format and regardless of whether the first pass ran to completion

#### Scenario: scan_members() finishes an interrupted pass

- **WHEN** a `streaming=True` reader's `__iter__` (or `stream_members`) is interrupted with an early `break`, then `ar.scan_members()` is called
- **THEN** `scan_members()` drains the remainder of the single pass internally and returns the complete, fully-resolved member list
- **AND** a subsequent `stream_members()` / `__iter__` / `extract_all` raises `UnsupportedOperationError`

#### Scenario: scan_members() before any pass consumes it

- **WHEN** `ar.scan_members()` is called on a not-yet-iterated `streaming=True` reader and afterwards `ar.stream_members()` is called
- **THEN** `scan_members()` returns the full member list, and the subsequent `stream_members()` raises `UnsupportedOperationError`, for every index topology (leading, trailing, no-index)

### Requirement: get_members_if_available() — an index-only member list

The system SHALL provide `get_members_if_available() -> list[ArchiveMember] | None`. It is **index-only**: it performs **no forward scan and no member-data reads**, and never begins or consumes the forward pass, so it is safe to call on any reader (including `streaming=True`) at any time without affecting a later pass. It returns the full member list when that list is available from a true upfront index or an already-materialized cache (a completed iteration / `scan_members` pass, or `members()` in random mode), and `None` otherwise. A caller that wants a guaranteed-materialized, fully-resolved list uses `members()` (random-access mode) or `scan_members()` (either mode).

Availability depends on the format's **index topology** (the `_MEMBER_LIST_UPFRONT` predicate):

- **Leading-index** (directory listing, ISO): reachable from the front — available in both modes.
- **Trailing-index** (ZIP central directory, native 7z header at EOF): reachable **only by seeking to the end**, so availability presupposes a **seekable source**. Those backends require a seekable source (`REQUIRES_SEEK`, and they do not permit non-seekable streaming), so their list is available in both modes. A hypothetical future format with a trailing index that also permitted non-seekable streaming SHALL report unavailable (`None`) on a non-seekable source.
- **No-index** (TAR): not reachable index-only — `None` until a forward pass has completed (or `scan_members()`/`members()` materialized the cache), after which the materialized, fully-resolved list is returned.

Because it is index-only, the members it returns are **not guaranteed to have resolved links**: for a format whose link *targets* are stored in member data (e.g. a ZIP symlink's target is its file content), `get_members_if_available()` SHALL return those members with `link_target` and `link_target_member` **unset**, since resolving them would require reading member data. `members()` and `scan_members()` perform the reads/scan needed to resolve links; `get_members_if_available()` does not.

#### Scenario: indexed backend returns the list even on a streaming reader

- **WHEN** `ar.get_members_if_available()` is called on a `streaming=True` reader of a format with an upfront index (e.g. ZIP)
- **THEN** the full member list is returned, with no scan and no member-data read, and the single forward pass remains available

#### Scenario: streaming backend returns None before iteration

- **WHEN** `ar.get_members_if_available()` is called on a not-yet-iterated reader of a no-index format (e.g. a streaming tar)
- **THEN** `None` is returned

#### Scenario: no-index backend returns the resolved list after a completed pass

- **WHEN** a `streaming=True` reader of a no-index format is iterated to completion (or `scan_members()` is called), then `ar.get_members_if_available()` is called
- **THEN** the fully-resolved materialized list is returned rather than `None`

#### Scenario: index-only listing leaves data-stored link targets unresolved

- **WHEN** `ar.get_members_if_available()` is called on a ZIP archive containing a symlink (whose target is stored in the member's data)
- **THEN** the returned symlink member has `link_target` and `link_target_member` unset (no member-data read occurs)
- **AND** `ar.members()` / `ar.scan_members()` on the same archive return that symlink with its `link_target` populated and `link_target_member` resolved

### Requirement: Access mode × method behaviour summary

The per-method behaviour is the composition of the rules above. There are exactly two modes: **random access** (`streaming=False`, the default) and **streaming** (`streaming=True`). The system SHALL behave per this table (`✅` = allowed, `⛔` = `UnsupportedOperationError`):

| Method | random access (`streaming=False`) | streaming (`streaming=True`) |
|--------|-----------------------------------|------------------------------|
| `__iter__` | ✅ (repeatable; from cache after first) | ✅ **once** (no replay; second call ⛔) |
| `stream_members` | ✅ | ✅ once (the one pass; second call ⛔) |
| `extract_all` | ✅ | ✅ once (the one pass) |
| `scan_members` | ✅ (= `members`) | ✅ (finishes the pass; may follow an interrupted/completed one) |
| `get_members_if_available` | ✅ (index-only; may be `None`) | ✅ (index-only, no-consume; may be `None`) |
| `members` | ✅ (may scan) | ⛔ |
| `get` | ✅ (may scan) | ⛔ |
| `open`, `read` | ✅ | ⛔ |
| `in` (`__contains__`, identity — see `archive-reading`) | ✅ (no scan) | ✅ (no scan) |
| `cost`, `info`, `format`, `close`, context manager | ✅ | ✅ |
| at `open_archive()` | fail fast if the source can't be random-accessed | works on any source |

In streaming mode, `__iter__`, `stream_members`, and `extract_all` all draw on the **same single forward pass**: whichever runs first consumes it, and a later one raises; `scan_members()` may still finish or return that pass's result. The independent backend-capability flag `_SUPPORTS_RANDOM_ACCESS` can also force `open`/`read` to raise (a backend that cannot seek the source at all); it composes with — does not replace — the access-mode rules above.

#### Scenario: scan_members is allowed in both modes

- **WHEN** `ar.scan_members()` is called on either a `streaming=False` or a `streaming=True` reader
- **THEN** it returns the fully-resolved member list (in random-access mode it is equivalent to `members()`; in streaming mode it finishes/consumes the single forward pass)

#### Scenario: streaming __iter__ does not replay after completion

- **WHEN** a `streaming=True` reader is fully iterated once via `__iter__`, then iterated again
- **THEN** the second iteration raises `UnsupportedOperationError` (streaming `__iter__` is single-use; use `scan_members()` / `get_members_if_available()` for the list)
