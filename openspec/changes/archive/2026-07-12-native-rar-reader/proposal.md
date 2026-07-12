## Why

Phase 6 needs a native RAR4/RAR5 reader so listing no longer depends on `rarfile`, matching the accepted ADR (`docs/decisions/0002-native-rar-metadata-unrar-data.md`) and the already-written `format-rar` spec. The v2 spine is ready: `SolidBlockReader`, volume discovery, password candidates, and the 7z reader as the solid-streaming template. DEV’s `rar-native-metadata-reader` exploration plus live `unrar p` probes pin the demux and argv rules.

## What Changes

- Implement a **native RAR4/RAR5 metadata parser** and **`RarReader` backend** (seek required; listing without `unrar`; no `rarfile` on the read path).
- **Member data** via RARLAB `unrar` only (identify the binary; never fall back to `unrar-free` / `unar` / `7z` / `bsdtar`).
- **Solid `stream_members()`**: one `unrar p -inul` pipe (no member path args) demuxed with `SolidBlockReader` over **payload members only** (symlinks / hardlinks / file-copies are absent from stdout even when headers carry sizes).
- **`_open_member`**: at most one member path arg (`unrar p … archive name`); hardlink/file_copy open goes through the ABC link layer to the target FILE.
- **Link targets**: always resolve when possible (RAR5 `file_redir`; RAR4 stored direct read) at registration / `_ensure_link_target` time.
- **Multi-volume**: full join using existing volume discovery; path sets point `unrar` at volume 1; stream sets materialize then same.
- **Header-encrypted RAR5**: native decrypt via `[rar]`/`[crypto]`; Blake2sp verify when `[rar]` present, else warn-and-skip.
- Wire **`rarfile` / `unrar` as test oracles**; activate corpus RAR entries; Atheris harness for the header parser.
- **Out of scope:** extract-hack / single-member temp RAR (benchmark-gated later); multi-name `unrar` filter optimization; rolling a native RAR decompressor; writing RAR.

## Capabilities

### New Capabilities

- (none)

### Modified Capabilities

- `format-rar`: tighten solid demux to payload-only sizes; lock `unrar` argv policy (unnamed solid pipe; single name on random open); classify `FILE_COPY` with hardlinks; require list-time link-target resolution when possible; defer extract-hack.
- `testing-contract`: activate native ↔ `rarfile`/`unrar` cross-validation for RAR; solid+links demux fixtures.
- `packaging-and-extras`: confirm RARLAB-only data path and Blake2sp/`[rar]` gating (no multi-tool matrix).

## Impact

- New: `internal/backends/rar_parser.py`, `internal/backends/rar_reader.py` (names may fold); small `unrar` process helper.
- Touch: registry/detection, `volumes.py` (RAR join / materialize for data), corpus builders/sweep; close threat-model C1 in docs as won’t-do.
- Deps unchanged for core listing; data still needs system `unrar`; `[rar]`/`[crypto]` optional; `rarfile` stays **dev oracle** only.
- Public API: no new fields; RAR becomes a registered readable format (today it raises / is unregistered).
