## Why

The random-access TAR reader (TAR-RA) was carved out of the concurrent-open /
`_open_member` reentrancy contracts as a "single shared decoder." That carve-out is
too blunt: compressed TAR already decompresses to an uncompressed stream that
`tarfile` opens in `r:` mode, and member payloads live at fixed `TarInfo.offset_data`
offsets in that stream. Plain TAR already re-seeks via stdlib `_FileInFile`. With
`SharedSource` wrapping the uncompressed stream tarfile sees, TAR-RA can honor
interleaved concurrent member opens like other byte-range backends — without opening
a fresh view on every member (which would hurt sequential reads).

## What Changes

- **Bring TAR-RA under the concurrent-open guarantee** for random-access
  (`streaming=False`) opens: interleaved reads of multiple open member streams MUST
  stay correct.
- **Wrap the uncompressed stream** that `tarfile` consumes in `SharedSource` (plain
  TAR path/stream and post-codec compressed TAR). Member data is served via
  byte-range views at `offset_data`, not by relying on two concurrent
  `extractfile`s on one shared handle/view.
- **Forward-cursor view policy:** reuse one view for forward seeks; mint another
  only when an earlier offset is needed while a view is still busy — do not open a
  new view per member unconditionally.
- **Narrow / remove the TAR carve-outs** in `archive-reading` (concurrent-open and
  reentrancy invariant) and document TAR-RA behavior in `format-tar`.
- **Streaming TAR** (`streaming=True` / `r|`) stays single-pass and out of scope.
- No public API break. **Not BREAKING.**

## Capabilities

### New Capabilities

_(none)_

### Modified Capabilities

- `archive-reading`: Remove (or narrowly replace) the "single shared decoder /
  random-access TAR" exemption from *Multiple concurrently-open member streams* and
  from *Random-access member-open is reentrant and reader-state-free*, so TAR-RA is
  in scope when served via SharedSource views over the uncompressed tar stream.
- `format-tar`: Add requirements for random-access concurrent member open (SharedSource
  on the uncompressed stream, forward-cursor view reuse, streaming still exempt).

## Impact

- Code: `src/archivey/internal/backends/tar_reader.py` (open path + `_open_member`);
  possibly small helpers around SharedSource view pooling / forward-cursor policy.
- Specs: `openspec/specs/archive-reading/spec.md`, `openspec/specs/format-tar/spec.md`
  (via this change's deltas); ABC docstring in `base_reader.py` if it still names
  TAR-RA as exempt.
- Docs: `docs/parallel-reader.md` audit row for TAR-RA; design notes in
  `shared-source-streams` / exploration that called TAR exempt.
- Tests: interleaved concurrent opens on plain and compressed TAR-RA fixtures;
  sequential-read regression (forward-cursor must not regress single-pass extract).
- Dependencies: none new (`SharedSource` already landed).
- Out of scope: multi-decoder / indexed codecs for compressed TAR (→ `IDEAS.md`);
  parallel extraction feature; streaming TAR.
