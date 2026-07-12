# 0001 — Native 7z reader; do not use py7zr for reading

- **Status:** accepted
- **Date:** 2026-07 (v2 direction; implementation via `native-7z-reader`)
- **Provenance:** `VISION.md`; `docs/grab-bag/ARCHITECTURE.md` §5.6; OpenSpec `format-7z`;
  DEV `sevenzip-native-reader` exploration

## Context

Wrapping `py7zr` for reads looked like a shortcut, but solid-folder access tended toward
per-folder caching / re-decompression patterns that made “iterate and hash every member”
intractable. Third-party quirks also leaked into the unified contract.

## Decision

Parse 7z headers natively. Decode common codecs with stdlib `lzma` / `bz2` / `zlib`
(pull-based, folder decoded once per streaming pass). Keep `py7zr` only for optional
writing (`[7z-write]`) and as a test oracle. Reject BCJ2 explicitly rather than falling
back to another reader.

## Consequences

- 7z **reading** is part of the zero-dep core for common codecs.
- Optional `[7z]` covers PPMd / Deflate64 / Zstd / Brotli / AES.
- Longer implementation road than a wrapper; memory-safety and streaming control improve.
