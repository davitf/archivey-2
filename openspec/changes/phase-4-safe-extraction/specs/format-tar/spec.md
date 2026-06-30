# TAR Format Behavior — delta (phase-4-safe-extraction)

## MODIFIED Requirements

### Requirement: Handle TAR hardlinks via two-pass extraction with cross-device fallback

The system SHALL support hardlink extraction from TAR archives. The `linkname` field holds
the target path. Resolution is performed by the `safe-extraction` `ExtractionCoordinator` (see
the *Hardlink Two-Pass Extraction* requirement there), not by a separate post-pass in the TAR
backend, and MUST NOT introduce `pending_*` link-resolution state.

TAR ordering guarantees the real file (the source) precedes any hardlink that references it.
The coordinator therefore resolves hardlinks within its single forward pass, **without
seeking back or re-decompressing** (important for solid `.tar.gz`, where reaching an earlier
member would require re-inflating the stream from the start):

1. If the hardlink's source has already been extracted (or staged) earlier in this pass,
   create a filesystem hardlink via `os.link()`.
2. **Streaming mode** (forward-only, no upfront member list): if the source was filtered out
   by the `members` selector or `filter`, the bytes are unrecoverable in a single pass —
   raise an explicit `ExtractionError` with a clear message.
3. **Random-access mode**: the pre-pass builds a hardlink closure map from member metadata
   (for TAR the `linkname` lives in the header, so this reads no member payload). When the
   forward pass reaches an **excluded-but-needed** source, the coordinator writes its content
   to the first selected link's destination path **at that point** (the source precedes its
   links, so its bytes are already streaming past), and `os.link()`s any further selected
   links to it when they are reached. The excluded source is never created at its own
   destination path. An implementation MAY instead stage the content in a hidden temp inside
   `dest`. No seek-back, no re-decompression, and no deferred post-pass; the only state is a
   bounded `{source → link path}` map.
4. If `os.link()` fails with a cross-device link error, fall back to copying via
   `shutil.copy2`.

#### Scenario: Hardlink source already extracted

- **WHEN** a `HARDLINK` member is encountered during extraction
- **AND** its source has already been written to disk in this pass
- **THEN** the system creates a filesystem hardlink from the source path to the new path via `os.link` (or `shutil.copy2` on cross-device failure)

#### Scenario: Hardlink source filtered out in streaming mode

- **WHEN** a selected `HARDLINK` member is encountered in streaming mode
- **AND** its source was excluded by the `members` selector or `filter`
- **THEN** an explicit `ExtractionError` is raised (a single forward pass cannot recover the source bytes)

#### Scenario: Hardlink source filtered out in random-access mode

- **WHEN** a selected `HARDLINK` member's source was excluded by the `members` selector or `filter`
- **THEN** the source content is written to the first selected link's path as the forward pass reaches the source (further selected links are `os.link`'d to it), the excluded source is never created at its own path, and no seek-back, re-decompression, or deferred post-pass is used

#### Scenario: Solid compressed tar resolves hardlinks without re-decompression

- **WHEN** a `.tar.gz` (solid, no seek accelerator) is extracted and a selected hardlink's source was excluded
- **THEN** the source is staged during the single forward decompression pass and the stream is never re-decompressed from the start to recover it

#### Scenario: Cross-device hardlink falls back to copy

- **WHEN** `os.link` fails with a cross-device error
- **THEN** the system copies the source file to the link destination instead
