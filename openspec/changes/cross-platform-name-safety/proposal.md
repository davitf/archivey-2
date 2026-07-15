## Why

"Safe by default" is a load-bearing claim, and extraction name handling is the sharpest
cross-platform corner still open. Four threat-model items share one root cause — a member
name that is fine on the archive's origin OS but collides, mangles, or fails on the
destination OS:

- **O2** — case/Unicode-normalization collisions (`README`/`readme`, NFC/NFD `café`) are
  distinct in the archive but the same file on Windows/macOS. Under `REPLACE` a crafted
  archive **silently merges** content on case-insensitive systems only.
- **O3** — Windows-reserved names (`CON`, `NUL`, `COM1`), trailing dots/spaces (`foo.`),
  silently mangled by Win32 — unchecked today.
- **O4** — `:` in a name writes an invisible NTFS alternate data stream — unchecked.
- **O7** — a name that is fsencodable but unrepresentable on the destination FS
  (surrogateescape `caf\udce9.txt`) succeeds on ext4, raises `EILSEQ` on APFS/macOS. The
  write-time `OSError` is now *translated* to a typed `ExtractionError`, but there is still
  **no portable-name normalization** and the outcome is platform-dependent.

Today these produce platform-dependent behavior — "a surprise squared." The fix is one
coherent policy dimension: deterministic, cross-platform name handling keyed off the
existing `ExtractionPolicy` (STRICT/STANDARD/TRUSTED). All directions are now settled
(recorded in `design.md` and ADR 0013): O2 collision determinism, O3/O4 rejection, and the
O7 normalization scheme (**sanitize** to a reversible percent-escaped portable spelling).

## What Changes

- **O2 — deterministic collision handling under STRICT/STANDARD:** the coordinator tracks a
  casefold+NFC key per written path, treats a collision as a first-class event on **every**
  OS (not just case-insensitive ones) under `STRICT`/`STANDARD`, applies `OverwritePolicy`
  deliberately, records `requested_path` on the `ExtractionResult`, and emits an
  `EXTRACTION_NAME_COLLISION` diagnostic; `TRUSTED` keys on the exact path and defers to the
  local OS. Adds `OverwritePolicy.RENAME` (` (N)` before the suffix, `photo (1).jpg`) — in
  scope here because it reuses this collision map, and the CLI's `extract` wants
  rename-on-collision parity with `unzip`.
- **O3/O4 — portable-name enforcement:** under `STRICT`, reject Windows-reserved names,
  trailing dots/spaces, and `:` on **every** platform (portability is part of no-surprises);
  `TRUSTED` allows what the local OS allows; `STANDARD` sits between (decision below).
- **O7 — portable-name normalization (settled: sanitize):** under `STRICT`/`STANDARD`,
  normalize unrepresentable names to a deterministic, reversible portable spelling
  (percent-escape each non-UTF-8 byte as `%XX`, `%` as `%25`; non-decodable bytes only;
  collision-tracked like O2); `TRUSTED` attempts faithful bytes and lets the OS decide
  (today's behavior).
- Coordinates with the in-flight `adversarial-string-corpus-contract` change (bidi-control
  warning, NUL-in-link-target rejection) — this change does **not** duplicate those; it owns
  the extraction-time filesystem-collision/mangling/representability dimension.

## Capabilities

### New Capabilities

<!-- none -->

### Modified Capabilities

- `safe-extraction`: add cross-platform name-safety behavior across the
  `ExtractionPolicy` levels (collision determinism, reserved/mangled-name rejection,
  portable-name normalization); surface collisions on `ExtractionResult`; add
  `OverwritePolicy.RENAME`.

## Impact

- `ExtractionCoordinator` (`internal/extraction.py`): casefold+NFC collision map; a
  pre-write portable-name check/transform keyed on `ExtractionPolicy`.
- Public surface: STRICT/STANDARD may now *reject or rewrite* names previously written
  as-is (deliberate, documented); `ExtractionResult` gains `requested_path`; new
  `EXTRACTION_NAME_COLLISION` diagnostic; new `OverwritePolicy.RENAME` (` (N)` before the
  suffix), useful for the CLI's `extract`.
- Tests: cross-platform matrix (collision, reserved, trailing dot/space, `:`,
  surrogateescape sanitize, RENAME) asserted deterministically on all platforms (not gated
  on the runner OS).
- Rationale for all six policy decisions recorded in ADR 0013.
