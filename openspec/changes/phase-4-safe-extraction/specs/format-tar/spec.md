# TAR Format Behavior — delta (phase-4-safe-extraction)

## MODIFIED Requirements

### Requirement: Handle TAR hardlinks via two-pass extraction with cross-device fallback

The system SHALL support hardlink extraction from TAR archives; the `linkname` field holds
the source path, and TAR ordering guarantees the real file (the source) precedes any hardlink
that references it. Resolution is performed by the `safe-extraction` `ExtractionCoordinator`,
which acts as a **pull-based sink**: it drives the `ArchiveReader` — inspecting `reader.cost`,
calling `reader.get_members_if_available()`, and iterating the forward pass — and selects
among the algorithms below. It MUST NOT hold a push-model deferred-state machine, and MUST NOT
force an upfront pass the run does not need.

Only a `members` selector or a `filter` can **orphan** a hardlink — select a link while
excluding its source; an unfiltered extract-all never orphans one, because the source is
always selected and precedes the link. The coordinator uses **one core algorithm**, with an
**optional optimization** when a free member list is available:

**Core — sequential pass with a conditional second pass.** One forward pass: write each
selected member; record written FILEs in a per-source `{device → on-disk path}` map; a
selected hardlink to an already-written source is created with `os.link()`. This alone handles
the common case — with no filter no link is ever orphaned, so the pass completes in one go
with no second pass. If a `filter`/selector *does* orphan a selected link (its source was
excluded), the coordinator:

- on a **seekable** source, collects the orphans and resolves them in a **single second pass**
  afterwards — only when at least one orphan exists. For a plain `.tar` the second pass
  re-scans headers; for a compressed tar it re-decompresses (so the stream is decompressed at
  most twice, and exactly once when there are no orphans).
- on a **forward-only** source (`stream_capability == FORWARD_ONLY`), the source's bytes are
  unrecoverable — a per-member failure handled by the configured `OnError` policy (STOP raises
  `ExtractionError`; CONTINUE records a `FAILED` `ExtractionResult` and proceeds).

The coordinator SHALL NOT speculatively call `members()` to look ahead: a header scan of a
plain `.tar` is not reliably cheap (seek-heavy on spinning or network media), and listing a
compressed tar would decompress the whole stream. It pays the second pass only when an orphan
actually forces it.

**Optional optimization — planned single pass.** When a selector/`filter` is in use **and** a
member list is available *for free* — `get_members_if_available()` returns it (a true central
directory, or an already-materialized list) — the coordinator MAY plan up front (apply the
selector, policy transform and `filter` to the list, computing the write plan and a
`source → selected-link-paths` map) and, in the single forward pass, write each **needed**
source to the first selected link's path even if the source itself was excluded (its bytes are
streaming past regardless), `os.link()`ing the remaining selected links. This avoids the second
pass for indexed sources. It is an optimization layered on the core algorithm, not a separate
correctness path.

**Cross-device handling.** The coordinator tracks, per source, the on-disk paths it has
created and their filesystem device. To create a link it prefers `os.link()` to an existing
copy **on the target device**; only when no same-device copy exists does it fall back to
`shutil.copy2`, recording the new copy's device so later links to the same source on that
device link to it instead of copying again. (E.g. `B → A` landing cross-device copies `A`'s
content to `B`; a later `C → A` on `B`'s device then `os.link`s `C` to `B` rather than copying
a second time.) This is strictly better than `tarfile`, which re-extracts the source's data
from the archive for every cross-device link and never links sibling copies to each other.

The excluded source's own name is never created on disk. The only auxiliary structures are
the per-source `{device → path}` map, a bounded list of orphaned links awaiting the second
pass (core algorithm), and the write plan (optional optimization); none of these is a
push-model deferred-creation machine.

#### Scenario: Unfiltered extract resolves hardlinks in one sequential pass

- **WHEN** an archive is extracted with no `members` selector or `filter` that excludes a hardlink source
- **THEN** every hardlink resolves during a single sequential pass with `os.link` (source precedes link) and no member list is fetched up front

#### Scenario: Filtered extract with a free member list stages orphaned sources in one pass

- **WHEN** a `filter` excludes a hardlink's source but selects the link, and `get_members_if_available()` returns the list (a true index or an already-materialized list)
- **THEN** the coordinator plans up front, and during the single forward pass writes the excluded source's content to the first selected link's path (further selected links `os.link` to it); no second pass is used and the excluded source is never created at its own path

#### Scenario: Filtered tar without a free list recovers an orphan in one second pass

- **WHEN** a plain `.tar` or a `.tar.gz` (no free list) is extracted with a filter that orphans one or more hardlinks on a seekable source
- **THEN** the coordinator does not speculatively scan/list up front, and resolves all orphans in a single second pass after the main pass (a compressed tar is thus decompressed at most twice)

#### Scenario: Filtered tar with no orphan does not take a second pass

- **WHEN** a plain `.tar` or `.tar.gz` is extracted with a filter that does not orphan any hardlink
- **THEN** extraction completes in a single pass with no second pass and no up-front list fetch

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
