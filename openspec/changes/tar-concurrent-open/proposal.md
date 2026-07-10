## Why

The random-access TAR reader (TAR-RA) was carved out of the concurrent-open /
`_open_member` reentrancy contracts as a "single shared decoder." That carve-out is
too blunt: compressed TAR already decompresses to an uncompressed stream that
`tarfile` opens in `r:` mode, and member payloads live at fixed `TarInfo.offset_data`
offsets in that stream. Plain TAR already re-seeks via stdlib `_FileInFile`. With
`SharedSource` wrapping the uncompressed stream tarfile sees, TAR-RA can honor
interleaved concurrent member opens like other byte-range backends — without opening
a fresh view on every member (which would hurt sequential reads).

> **Depends on `concurrent-open-opt-in`.** That change owns the `archive-reading` rewrite:
> multiple simultaneously-open member streams are an opt-in, format-uniform capability
> (`allow_multiple_open_streams`), and it drops the blanket TAR-RA concurrent-open exemption.
> This change is the **TAR mechanism** that runs *under* that opt-in; it no longer edits
> `archive-reading` itself.

## What Changes

- **Bring TAR-RA under the concurrent-open capability** for random-access
  (`streaming=False`) opens: when the caller has opted in (`allow_multiple_open_streams`),
  interleaved reads of multiple open member streams MUST stay correct.
- **Wrap the uncompressed stream** that `tarfile` consumes in `SharedSource` (plain
  TAR path/stream and post-codec compressed TAR). Member data is served via
  byte-range views at `offset_data`, not by relying on two concurrent
  `extractfile`s on one shared handle/view.
- **Forward-cursor view policy:** reuse one view for forward seeks; mint another
  only when an earlier offset is needed while a view is still busy — do not open a
  new view per member unconditionally. (This is the solid-stream *optimization* under
  the opt-in; it does not make interleaving cheap on a compressed TAR — see cost model.)
- **Document TAR-RA behavior in `format-tar`** (SharedSource + forward-cursor); the
  concurrent-open contract/gating lives in `concurrent-open-opt-in`.
- **Streaming TAR** (`streaming=True` / `r|`) stays single-pass and out of scope.
- No public API break. **Not BREAKING.**

## Capabilities

### New Capabilities

_(none)_

### Modified Capabilities

- `format-tar`: Add requirements for random-access concurrent member open (SharedSource
  on the uncompressed stream, forward-cursor view reuse, streaming still exempt). These
  apply only when the caller has opted in per `concurrent-open-opt-in`.

> The `archive-reading` concurrent-open + reentrancy rewrites (dropping the TAR-RA
> exemption, adding the opt-in gate) are owned by `concurrent-open-opt-in`, not this change.

## Impact

- Code: `src/archivey/internal/backends/tar_reader.py` (open path + `_open_member`);
  possibly small helpers around SharedSource view pooling / forward-cursor policy.
- Specs: `openspec/specs/format-tar/spec.md` (via this change's delta). The
  `archive-reading` edits + ABC docstring changes are owned by `concurrent-open-opt-in`.
- Depends on: `concurrent-open-opt-in` (the `allow_multiple_open_streams` gate + the
  reentrancy/exemption rewrite this mechanism relies on).
- Docs: `docs/parallel-reader.md` audit row for TAR-RA; design notes in
  `shared-source-streams` / exploration that called TAR exempt.
- Tests: interleaved concurrent opens on plain and compressed TAR-RA fixtures;
  sequential-read regression (forward-cursor must not regress single-pass extract).
- Dependencies: none new (`SharedSource` already landed).
- Out of scope: multi-decoder / indexed codecs for compressed TAR (→ `IDEAS.md`);
  parallel extraction feature; streaming TAR.
