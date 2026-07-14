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
existing `ExtractionPolicy` (STRICT/STANDARD/TRUSTED). **Spike:** the O2 collision and
O3/O4 rejection directions are decided; the O7 normalization scheme (reject vs sanitize to
a reversible portable spelling) is the open decision this change must settle before full
implementation.

## What Changes

- **O2 — deterministic collision handling on all platforms:** the coordinator tracks a
  casefold+NFC key per written path, treats a collision as a first-class event on **every**
  OS (not just case-insensitive ones), applies `OverwritePolicy` deliberately, and records
  it on the `ExtractionResult`. Adds `OverwritePolicy.RENAME` (`name (1)`) — in scope here
  because it reuses this collision map, and the CLI's `extract` wants rename-on-collision
  parity with `unzip`.
- **O3/O4 — portable-name enforcement:** under `STRICT`, reject Windows-reserved names,
  trailing dots/spaces, and `:` on **every** platform (portability is part of no-surprises);
  `TRUSTED` allows what the local OS allows; `STANDARD` sits between (decision below).
- **O7 — portable-name normalization (the open decision):** under `STRICT`, either reject
  unrepresentable names or normalize them to a deterministic, reversible portable spelling
  (percent/escape style, collision-tracked like O2); `TRUSTED` attempts faithful bytes and
  lets the OS decide (today's behavior). The scheme is chosen in this change's design pass.
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
- Public surface: STRICT may now *reject or rewrite* names it previously wrote as-is
  (deliberate, documented); `ExtractionResult` gains a collision signal; new
  `OverwritePolicy.RENAME` (`name (1)`), useful for the CLI's `extract`.
- Tests: cross-platform matrix (collision, reserved, trailing dot/space, `:`,
  surrogateescape) asserted deterministically on all platforms (not gated on the runner OS).
- **Spike marker:** the O7 normalization scheme is an open design decision; O2/O3/O4 can
  proceed independently if O7 needs more exploration.
