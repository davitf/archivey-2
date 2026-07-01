# TAR Format Behavior — delta (phase-4-safe-extraction)

## MODIFIED Requirements

### Requirement: Handle TAR hardlinks via two-pass extraction with cross-device fallback

The system SHALL support hardlink extraction from TAR archives; the `linkname` field holds
the source path, and TAR ordering guarantees the real file (the source) precedes any hardlink
that references it. Resolution is performed by the `safe-extraction` `ExtractionCoordinator`,
which acts as a **pull-based sink**: it drives the `ArchiveReader` — inspecting `reader.cost`,
calling `reader.get_members_if_available()` (and, when cheap, `reader.members()`), and
iterating the forward pass — and selects among the algorithms below. It MUST NOT hold a
push-model deferred-state machine, and MUST NOT force an upfront pass the run does not need.

**Obtaining the member list.** Only a `members` selector or a `filter` can orphan a hardlink
(select a link while excluding its source); an unfiltered extract-all never orphans one. So
the coordinator obtains the full member list up front **only when a selector/filter is in
use** and the list is available without a wasted pass — either `get_members_if_available()`
returns it (a central directory, or an already-materialized list), or the source is seekable
with `listing_cost == REQUIRES_SCANNING` (a plain `.tar`, whose 512-byte header walk is cheap
and decompresses nothing) so it MAY call `members()`. It SHALL NOT call `members()` on a
compressed tar (that would decompress the whole stream) or on a streaming reader (which
raises `UnsupportedOperationError`).

The coordinator then uses one of three algorithms:

1. **No selector/filter — single sequential pass.** Write each member; record written FILEs
   in a per-source `{device → on-disk path}` map; a hardlink to an already-written source is
   created with `os.link()`. No planning and no second pass. (Orphans are impossible.)
2. **Filtered, member list in hand — planned single pass.** Apply the selector, policy
   transform and `filter` to the member list up front to compute the write plan and a
   `source → selected-link-paths` map (including sources that are themselves excluded but
   referenced by a selected link). Then one forward pass: write selected members; when the
   pass reaches a **needed** source, write its content to the first selected link's path even
   if the source itself was excluded (its bytes are streaming past regardless), and `os.link()`
   the remaining selected links. No second pass; works for `AccessCost.DIRECT` and `SOLID`
   alike, since the list was obtained without a wasted pass.
3. **Filtered, no cheaply-available list (compressed tar) — sequential pass + conditional
   second pass.** `get_members_if_available()` is `None` and listing would require
   decompressing the whole stream, so the coordinator runs the sequential pass and, if a
   selected link's source turns out to have been excluded (an orphan), collects it and
   resolves all orphans in a **single second pass** afterwards — only when at least one orphan
   exists. The stream is thus decompressed at most twice, and exactly once in the common
   no-orphan case.

**Forward-only sources** (`stream_capability == FORWARD_ONLY`, streaming/pipe): the list is
unavailable and there is no second pass, so a selected link whose source was excluded is
unrecoverable — a per-member failure handled by the configured `OnError` policy (STOP raises
`ExtractionError`; CONTINUE records a `FAILED` `ExtractionResult` with the error and proceeds).

**Cross-device handling.** The coordinator tracks, per source, the on-disk paths it has
created and their filesystem device. To create a link it prefers `os.link()` to an existing
copy **on the target device**; only when no same-device copy exists does it fall back to
`shutil.copy2`, recording the new copy's device so later links to the same source on that
device link to it instead of copying again. (E.g. `B → A` landing cross-device copies `A`'s
content to `B`; a later `C → A` on `B`'s device then `os.link`s `C` to `B` rather than copying
a second time.) This is strictly better than `tarfile`, which re-extracts the source's data
from the archive for every cross-device link and never links sibling copies to each other.

The excluded source's own name is never created on disk. The only auxiliary structures are
the write plan (algorithm 2), the per-source `{device → path}` map, and — for algorithm 3
only — a bounded list of orphaned links awaiting the single second pass; none of these is a
push-model deferred-creation machine.

#### Scenario: Unfiltered extract resolves hardlinks in one sequential pass

- **WHEN** an archive is extracted with no `members` selector or `filter` that excludes a hardlink source
- **THEN** every hardlink resolves during a single sequential pass with `os.link` (source precedes link) and no member list is fetched up front

#### Scenario: Filtered extract with a cheap member list stages orphaned sources in one pass

- **WHEN** a `filter` excludes a hardlink's source but selects the link, and the member list is available via `get_members_if_available()` or a plain-tar header scan
- **THEN** the coordinator plans up front, and during the single forward pass writes the excluded source's content to the first selected link's path (further selected links `os.link` to it); no second pass is used and the excluded source is never created at its own path

#### Scenario: Filtered compressed tar recovers an orphan in one second pass

- **WHEN** a `.tar.gz` (`REQUIRES_DECOMPRESSION`, no index) is extracted with a filter that orphans one or more hardlinks
- **THEN** the orphans are resolved in a single second pass after the main pass, so the stream is decompressed at most twice regardless of the number of orphans

#### Scenario: Filtered compressed tar with no orphan does not take a second pass

- **WHEN** a `.tar.gz` is extracted with a filter that does not orphan any hardlink
- **THEN** extraction completes in a single decompression pass with no second pass

#### Scenario: Orphaned link on a forward-only source follows OnError

- **WHEN** a selected hardlink whose source was excluded is reached on a forward-only (streaming) source
- **THEN** it is a per-member failure: `OnError.STOP` raises `ExtractionError`, and `OnError.CONTINUE` records a `FAILED` `ExtractionResult` and continues

#### Scenario: Chained cross-device links reuse a sibling copy

- **WHEN** `B → A` is written to a different device than `A` (forcing a `shutil.copy2` of `A` to `B`)
- **AND** a later `C → A` is written to the same device as `B`
- **THEN** `C` is created with `os.link(B, C)` rather than copying `A`'s content a second time

#### Scenario: Cross-device hardlink falls back to copy

- **WHEN** `os.link` fails with a cross-device error and no same-device copy of the source exists
- **THEN** the system copies the source content to the link destination and records it for reuse by later same-device links
