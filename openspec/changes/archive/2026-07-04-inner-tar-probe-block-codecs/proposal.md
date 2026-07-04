# Inner-TAR probe reads a full block, so large-block `.tar.bz2` is detected

## Why

The inner-TAR probe (`detection.py`) decompresses only the peeked detection prefix
(`DETECTION_LIMIT` = 4096 bytes) and checks for the `ustar` signature at offset 257. That
works for **stream-oriented** codecs (gzip, xz, zstd, lz4, lzip, zlib, brotli,
unix-compress), which emit decompressed output incrementally, so 4 KiB of compressed input
already reaches the header region.

It does **not** work for **bzip2**, which is block-transform (BWT) based: it emits *no*
decompressed output until an entire block (up to 900 KB uncompressed) has been read. When a
`.tar.bz2`'s first block compresses to more than the 4 KiB prefix — which happens for any
tarball whose leading member holds even ~5 KB of incompressible/binary data (a JPEG, an
already-compressed file, random bytes) — the codec raises `TruncatedError` on the prefix, the
probe returns `False`, and the archive is mis-reported as a bare `.bz2`. There is no
open-time re-probe, so a real tarball is then presented as a single opaque decompressed blob
instead of a TAR with members. This affects a large fraction of real-world `.tar.bz2` files,
and the `.tar.bz2` filename does **not** rescue it (magic wins over extension; the failed
probe reports bare `BZ2`).

This was surfaced by the maintainer after the analogous rapidgzip/gzip truncation fix, which
addressed a *different* cause (rapidgzip rejecting a truncated prefix) but not bzip2's block
structure.

## What Changes

- **`format-detection`** — the inner-TAR probe reads from the **actual source**, not only the
  peeked prefix: when the prefix decodes to too little output (a block codec whose first block
  exceeds it), the probe reads up to one maximum block (bounded, position-restoring /
  buffered in the `PeekableStream`, exactly like the prefix peek) and retries. The bound is
  `_INNER_TAR_MAX_PROBE_BYTES` = 1 MiB, covering a worst-case filled bzip2 level-9 block
  (~900 KB uncompressed → ~904 KB compressed) with margin. Stream-oriented codecs resolve
  from the ordinary prefix and never trigger the larger read.
- The probe forces the **sequential** backend for the decode (`StreamConfig(streaming=True)`),
  so the rapidgzip accelerator — which rejects a bounded/truncated prefix — is not engaged.
  (This subsumes the separately-landed rapidgzip inner-tar fix; see the note below.)
- No public API change; detection returns the correct combined format
  (`TAR_BZ2` / `TAR_GZ` / …) for more inputs than before. No format is *newly* rejected.

### Relationship to the rapidgzip inner-tar fix

A separate change on the live-decompression-ratio-guard branch forces the sequential backend
in the same probe to fix a rapidgzip-specific gzip case. This change incorporates that same
`streaming=True` forcing plus the block-reading escalation, so it is a **superset**; if the
two land in either order the overlapping edit reconciles to this version.

## Impact

- Affected specs: `format-detection` (the "Compressed streams are probed for an inner TAR"
  requirement).
- Affected code: `src/archivey/internal/detection.py` (`_probe_inner_tar`,
  `_resolve_single_file_or_tar`, `detect_format`); `tests/test_detection.py`.
- Behavioural: large-block `.tar.bz2` now detects as `TAR_BZ2`. Detecting a large-block bare
  `.bz2` (or a `.bz2` whose payload isn't a tar) now reads up to one block (≤ 1 MiB) rather
  than 4 KiB — a bounded cost paid only for bzip2 sources whose first block exceeds the prefix.
