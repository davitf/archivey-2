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
- Anti-items and superseded revisions listed with `is_anti` / `is_current`; extraction
  reproduces the archive's final tree without deleting data it did not create.
- Atheris harness for the header parser (Phase 6 fuzz entry companion).

**Non-Goals:**
- RAR reader; ZIP Deflate64/PPMd wiring; 7z writing.
- Decoded-folder spool / disk cache.
- Native BCJ2; computing `is_current` shadowing for non-7z formats (ZIP/TAR duplicate
  names — sibling if needed); the opt-in `7z x`-style differential-restore extraction mode.
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

**Placement:** the 7z SHA-256 KDF (UTF-16LE password + salt + `1 << NumCyclesPower`
rounds, incl. the `0x3f` special case) is 7z-specific — RAR and WinZip-AES derive keys
differently — so it lives in a **7z-local area of the crypto module** (a `sevenzip`
helper beside `streams/crypto.py`), not on the generic crypto backend surface. It emits
the 32-byte key + IV that feed the shared, format-agnostic AES decrypt stage as
`AesParams`; the reader never imports `cryptography` directly. If a later format turns
out to share the exact scheme, promote it to a shared helper then — not preemptively.

### 5. Unsupported codec combinations
- **BCJ2** / unknown method IDs / newer BCJ absent from liblzma → `UnsupportedFeatureError`.
- **LZMA1+BCJ**: attempt a correct stdlib-only composition (separate stages if needed). If
  that cannot be validated against a fixture (never guess), leave **unimplemented** and
  raise `UnsupportedFeatureError`, documenting it for later (py7zr uses `pybcj` for this
  path — we will not add that runtime dep here).

**Limitation:** LZMA1+BCJ is intentionally unsupported in this change. A real py7zr
fixture exercises the path and the reader raises `UnsupportedFeatureError` until a
stdlib-only decode path is validated against an oracle.

### 6. Anti-items (`is_anti`) and superseded revisions (`is_current`)
What anti-items are *for*: 7z records them in **differential/incremental** archives —
an entry with a name, zero content, and the `ANTI` (`0x10`) bit means "this path was
deleted since the base." Their meaning is relative to a pre-existing base tree; `7z x`
consumes them by deleting the path from an already-extracted base. CLI validation
(7z 23.01): `Anti = +` members appear in `7z l -slt`; on `7z x` the destination is
**deleted** if present; size is 0. **py7zr 1.1.3 cannot parse** archives with the
`ANTI` property (`Bad7zFile: invalid type b'\x10'`), so the anti oracle is the **`7z`
CLI**, not py7zr.

The key insight: this couples two separable facts — the raw deletion bit, and which
revision of a path is *live*. We model them separately:

- **`is_anti`** (raw): the faithful ANTI bit. Listed/iterated as-is.
- **`is_current`** (derived): last-entry-wins by name. A content member deleted by a
  later anti-item, or re-added later, is `is_current=False`; the surviving entry is
  `is_current=True`. An anti-item that is the last word on its path is itself
  `is_current=True` (final state = "deleted"). This is a general concept that also
  covers duplicate-name shadowing in appended/updated ZIP/TAR — but this change only
  requires the **7z reader** to compute it; other formats keep the default
  `is_current=True` until a sibling change.

Extraction (see `safe-extraction`) then reproduces the archive's **final tree**:
- Non-current members are **skipped** by default (no redundant writes; the archive's
  own duplicates don't trip `OverwritePolicy.ERROR`).
- An anti-item **never deletes data this extraction did not create**. Since the content
  it supersedes is already skipped as non-current, an anti-item is a **no-op on disk**
  in the common case. The only delete that ever fires is bounded to a path *this same
  extraction wrote* — a safety upper bound matching the maintainer's rule ("delete only
  if the current extraction wrote it, otherwise don't"), using `lstat`/`unlink` (never
  through a symlink) and only for a file or empty dir.
- `open` / stream data for an anti-item: empty (no payload).

**Rejected:** `7z x`-style deletion of pre-existing on-disk files by default. Reaching
outside the current extraction to remove user data that merely shares a name is a
foot-gun and contradicts archivey's safety posture (cf. the deliberate `tarfile`
symlink-copy deviation). That behavior is deferred to a future explicit opt-in mode
("differential restore"), never the default.

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
| Anti delete surprises callers | Default never deletes pre-existing data; only removes paths the same extraction wrote; `7z x`-style restore is opt-in |
| `is_current` diverges from `7z x` on a populated tree | Documented: default = final tree on a fresh dest; opt-in mode matches `7z x` differential restore |
| py7zr oracle gaps (anti, some codecs) | Prefer `7z` CLI for those fixtures |
| KDF cost on multi-password archives | Cache by `(password, salt, cycles)`; known-good first |
| Solid `open()` O(prefix) cost | Honest `CostReceipt`; prefer `stream_members()` |
| Encoder header / empty archives | Handle absent `FILES_INFO` / empty folders explicitly |

## Migration Plan

- Register the backend → corpus 7z entries activate automatically.
- No API break beyond additive `is_anti` / `is_current` fields.
- Stale "Phase 7" comments in `crypto.py` / `PpmdCodec` updated to Phase 6.

## Open Questions

None blocking. Sibling follow-ups if needed: `is_current` shadowing for ZIP/TAR
duplicate names; the opt-in `7z x`-style differential-restore extraction mode;
LZMA1+BCJ via optional helper; ZIP shared-codec wiring; RAR native reader.
