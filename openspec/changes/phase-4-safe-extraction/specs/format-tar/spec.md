# TAR Format Behavior — delta (phase-4-safe-extraction)

## MODIFIED Requirements

### Requirement: Handle TAR hardlinks via two-pass extraction with cross-device fallback

The system SHALL support hardlink extraction from TAR archives; the `linkname` field holds
the source path. Resolution is performed by the `safe-extraction` `ExtractionCoordinator` and
MUST NOT require an upfront pre-pass over the archive: a TAR has no central directory, so
building a full member/closure map would mean scanning every 512-byte header (plain `.tar`)
or decompressing the entire stream (compressed `.tar.gz` etc.) *before* extracting — a cost
the common case (extract-all, or any run without a `filter`) does not need.

The default path is a **single sequential forward pass**. TAR guarantees the real file (the
source) precedes any hardlink that references it, so:

1. As FILE members are written, the coordinator records each one's on-disk path under the
   member's key, tracked per filesystem device (see cross-device handling below).
2. When a selected `HARDLINK` is reached and its source was already written in this pass,
   the link is created with `os.link()` — the common case, with no seek and no extra pass.
3. A selected `HARDLINK` whose source was **excluded** by the `members` selector or `filter`
   is an "orphaned" link (only possible when a filter is in use). Its source content is
   recovered and written to this — the first selected — link's path; any further selected
   links to the same source then `os.link()` to it. The recovery strategy is chosen from the
   source's `CostReceipt`, so no pre-pass is spent when it is not needed:
   - **Forward-only source** (`stream_capability == FORWARD_ONLY`, streaming/pipe): the bytes
     are unrecoverable in a single pass. This is a per-member extraction failure handled by
     the configured `OnError` policy — under `STOP` it raises, under `CONTINUE` it records a
     `FAILED` `ExtractionResult` (with the error) and proceeds. No recovery is attempted.
   - **Seekable + `AccessCost.DIRECT`** (plain `.tar`): seek to the source and materialize it
     at the link path immediately — cheap, no re-decompression, no second pass.
   - **Seekable + `AccessCost.SOLID`** (compressed tar): reaching an earlier member requires
     re-decompressing from the start, so orphaned links are collected during the main pass
     and resolved in a **single second pass** afterwards — and only if at least one orphaned
     link exists (otherwise there is no second pass). This bounds recovery to one extra
     decompression regardless of how many links are orphaned.

**Cross-device handling.** The coordinator tracks, per source, the on-disk paths it has
already created and their filesystem device. To create a link it prefers `os.link()` to an
existing copy **on the target device**; only when no same-device copy exists does it fall
back to `shutil.copy2`, recording the new copy's device so later links to the same source on
that device link to it instead of copying again. (For example, `B → A` landing cross-device
copies `A`'s content to `B`; a later `C → A` on `B`'s device then `os.link`s `C` to `B`
rather than copying a second time.) This is strictly better than `tarfile`, which re-extracts
the source's data from the archive for every cross-device link and never links sibling copies
to each other.

The excluded source's own name is never created on disk. There is **no `pending_*` deferred
link-creation state machine**; the only auxiliary structures are the running per-source
`{device → path}` map and — for the `SOLID` orphan case only — a bounded list of orphaned
links awaiting the single second pass.

#### Scenario: Hardlink source already extracted, same device

- **WHEN** a `HARDLINK` member is reached and its source was already written to disk in this pass on the same filesystem device
- **THEN** the system creates a filesystem hardlink from the source path to the new path via `os.link`

#### Scenario: Chained cross-device links reuse a sibling copy

- **WHEN** `B → A` is written to a different device than `A` (forcing a `shutil.copy2` of `A`'s content to `B`)
- **AND** a later `C → A` is written to the same device as `B`
- **THEN** `C` is created with `os.link(B, C)` rather than copying `A`'s content a second time

#### Scenario: Orphaned link on a forward-only source follows OnError

- **WHEN** a selected `HARDLINK` whose source was excluded by the `members` selector or `filter` is reached in streaming (forward-only) mode
- **THEN** it is treated as a per-member failure: `OnError.STOP` raises `ExtractionError`, and `OnError.CONTINUE` records a `FAILED` `ExtractionResult` (with the error) and continues

#### Scenario: Orphaned link on a plain seekable tar recovers without a second pass

- **WHEN** a selected `HARDLINK`'s source was excluded and the source is a seekable plain `.tar` (`AccessCost.DIRECT`)
- **THEN** the coordinator seeks to the source, writes its content to the first selected link's path (further selected links `os.link` to it), the excluded source is never created at its own path, and no second pass is used

#### Scenario: Orphaned link on a solid compressed tar recovers in one second pass

- **WHEN** one or more selected `HARDLINK`s have sources excluded and the source is a seekable compressed tar (`AccessCost.SOLID`)
- **THEN** the orphaned links are resolved in a single second pass after the main pass, so the stream is decompressed at most twice in total regardless of the number of orphaned links

#### Scenario: No filter means no second pass

- **WHEN** a compressed tar is extracted without a `members` selector or `filter` that excludes a hardlink source
- **THEN** every hardlink resolves during the single sequential pass and no second pass is performed

#### Scenario: Cross-device hardlink falls back to copy

- **WHEN** `os.link` fails with a cross-device error and no same-device copy of the source exists
- **THEN** the system copies the source content to the link destination instead and records it for reuse by later same-device links
