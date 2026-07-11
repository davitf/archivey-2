## Context

`format-7z` already specifies a native reader; no backend is registered yet. The DEV
`sevenzip-native-reader` design (`docs/sevenzip-native-reader-design.md` in
`archivey-dev`) remains the format/codec reference. v2 already has the host layers:
`compressed-streams` (incl. raw LZMA + filter IDs; PPMd/AES stubs), `SharedSource`,
password candidates, and volume *discovery*. This change fills the reader and finishes
the stubs.

Provenance: DEV exploration + `ARCHITECTURE.md` §5.6 / §7.3.

## Goals / Non-Goals

**Goals:**
- Zero-dep core 7z **read** (common codecs) with true pull streaming.
- Oracle parity with `py7zr` / `7z` CLI on supported archives.
- Correct password handling with KDF caching; never silent garbage.
- Anti-items listed and extracted like the `7z` CLI (delete destination).
- Atheris harness for the header parser (Phase 6 fuzz entry companion).

**Non-Goals:**
- RAR reader; ZIP Deflate64/PPMd wiring; 7z writing.
- Decoded-folder spool / disk cache.
- Native BCJ2; general `is_current` / skip-non-current iteration filters (sibling if needed).
- Pulling `pybcj` into the runtime dependency surface.

## Decisions

### 1. Module layout
- `internal/backends/sevenzip_parser.py` — header parse → member list + folder/coder map.
- `internal/backends/sevenzip_reader.py` — `BaseArchiveReader` + `ReadBackend` registration.
- Folder pipeline stays in the reader (or a tiny private helper); it **only** composes
  `compressed-streams` / `crypto` APIs.

### 2. Solid random access = re-decode only
No spool, no temp files, no decoded-folder RAM cache. `stream_members()` decodes each
folder once and slices forward; `open()` on a solid member re-decodes from the folder
start and skips to the substream. Peak memory = decompressor working set.

**Rejected:** disk spill / single-folder spool (complexity; contradicts "no disk writes").

### 3. SharedSource for pack ranges
Pack streams are `SharedSource.view(pack_offset, pack_size)` so concurrent `open()` under
`MemberStreams.CONCURRENT` is correct by construction.

### 4. Password KDF cache (correctness first)
7z has **no check value**. Cost of a try ≈ SHA-256 KDF (`1 << NumCyclesPower`, commonly
`2^19` rounds) **per distinct `(password, salt, cycles)`**, then decrypt+decode until CRC
fails (usually fast). Salt can differ per folder/header, so the cost is **not** "once per
archive" unless salts match.

Cache derived 32-byte keys by `(password, salt, cycles)` for the life of the reader.
Try known-good candidates first; promote successes. Wrong password →
`EncryptionError`/`CorruptionError`, never wrong bytes.

### 5. Unsupported codec combinations
- **BCJ2** / unknown method IDs / newer BCJ absent from liblzma → `UnsupportedFeatureError`.
- **LZMA1+BCJ**: attempt a correct stdlib-only composition (separate stages if needed). If
  that cannot be validated against a fixture (never guess), leave **unimplemented** and
  raise `UnsupportedFeatureError`, documenting it for later (py7zr uses `pybcj` for this
  path — we will not add that runtime dep here).

### 6. Anti-items (`is_anti`)
CLI validation (7z 23.01): members with `Anti = +` appear in `7z l -slt`; on `7z x` the
destination path is **deleted** if present; size is 0. **py7zr 1.1.3 cannot parse**
archives with the `ANTI` (`0x10`) property (`Bad7zFile: invalid type b'\x10'`), so the
oracle for anti is the **`7z` CLI**, not py7zr.

Design:
- Add `ArchiveMember.is_anti: bool = False`.
- Always include anti members in listing / iteration.
- `open` / stream data: empty (no payload).
- `extract`: if `is_anti`, **unlink** the destination (file or empty dir per 7z behavior)
  when it exists **inside the extract root**, after universal path checks — do not write
  content. Emit a diagnostic if useful.
- Defer a general `is_current` flag and optional iteration skip filters to a sibling
  change; `is_anti` is the precise 7z signal.

### 7. Multi-volume
Discovery already lives in `volumes.py`. Join = sequential concatenation into one logical
seekable stream, then normal parse. Missing/out-of-order parts → error, not garbage.

### 8. Writing / oracles
Reads never import `py7zr`. Tests use `py7zr` + `7z` CLI as oracles (skip if absent).
`[7z-write]` stays Phase 9.

### 9. Fuzzing
Ship an Atheris (or documented harness scaffold if Atheris packaging is awkward) targeting
the header parser, seeded from corpus + adversarial bytes; env-gated like the mutation
harness. Mutation/Hypothesis gates are already green.

## Risks / Trade-offs

| Risk | Mitigation |
|------|------------|
| LZMA1+BCJ silent corruption | Fixture test; unsupported raise if unsure |
| Anti delete surprises callers | Spec + docs; only under extract; path-confined |
| py7zr oracle gaps (anti, some codecs) | Prefer `7z` CLI for those fixtures |
| KDF cost on multi-password archives | Cache by `(password, salt, cycles)`; known-good first |
| Solid `open()` O(prefix) cost | Honest `CostReceipt`; prefer `stream_members()` |
| Encoder header / empty archives | Handle absent `FILES_INFO` / empty folders explicitly |

## Migration Plan

- Register the backend → corpus 7z entries activate automatically.
- No API break beyond additive `is_anti`.
- Stale "Phase 7" comments in `crypto.py` / `PpmdCodec` updated to Phase 6.

## Open Questions

None blocking. Sibling follow-ups if needed: `is_current` + skip filters; LZMA1+BCJ via
optional helper; ZIP shared-codec wiring; RAR native reader.
