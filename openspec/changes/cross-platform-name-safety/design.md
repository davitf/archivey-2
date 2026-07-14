## Context

`ExtractionPolicy` (STRICT default / STANDARD / TRUSTED) already gates metadata transforms
(permission normalization, uid/gid). `check_universal` already rejects traversal, non-UTF-8
that can't be `os.fsencode`d, and root-named non-directory members; the extraction
coordinator already *translates* the O7 write-time `EILSEQ` `OSError` into a typed
`ExtractionError`. `OverwritePolicy` (ERROR/REPLACE/SKIP) handles existing destinations but
keys on the exact `Path`, so it misses casefold/normalization collisions (O2). The
in-flight `adversarial-string-corpus-contract` owns bidi-control warnings + NUL link-target
rejection — orthogonal to the filesystem-representability dimension here.

## Goals / Non-Goals

**Goals:**
- Deterministic, cross-platform name handling: the same archive extracts to the same
  logical result (collision events, rejections) regardless of the runner OS.
- Anchor every rule to an existing `ExtractionPolicy` level; STRICT is portable-by-default.
- Settle the O7 normalization scheme (the spike's core question).

**Non-Goals:**
- Duplicating the bidi/NUL work in `adversarial-string-corpus-contract`.
- Changing traversal/symlink-escape safety (already non-bypassable).
- A full `OverwritePolicy.RENAME` implementation (scoped/spec'd here, may land separately).

## Decisions (settled)

- **O2 collision key.** The coordinator maintains a map from `casefold(NFC(relative_path))`
  → written path. A second member hitting the same key is a **collision event on all
  platforms**, handled by `OverwritePolicy` exactly as a real existing-file would be, and
  recorded on the member's `ExtractionResult`. This removes the platform-dependent silent
  merge under `REPLACE`.
- **O3/O4 rejection under STRICT.** Reserved device names (`CON`, `PRN`, `AUX`, `NUL`,
  `COM1`–`COM9`, `LPT1`–`LPT9`, case-insensitive, with or without extension), trailing dot/
  space in any path segment, and `:` anywhere in a segment are rejected under `STRICT` on
  **every** platform. `TRUSTED` defers to the local OS. `STANDARD` = reject the
  unambiguously-dangerous set (reserved names, `:`), allow trailing dot/space (rare and
  low-risk) — a middle ground consistent with STANDARD's "portable but not paranoid" role.

## Open decision (the spike)

**O7 — unrepresentable names: reject vs sanitize.** Two candidate STRICT behaviors:

1. **Reject** — a name that cannot be represented portably is an `ExtractionError`
   (`SKIPPED`/`FAILED` per `OnError`). Simple, honest, no lossy rewrite; cost: a member the
   user could have extracted on Linux is refused under STRICT.
2. **Sanitize to a reversible portable spelling** — decode-lossy/unrepresentable bytes are
   escaped to a deterministic form (e.g. percent-escaped `caf%E9.txt`), collision-tracked
   like O2, rejecting only when even that can't be formed. Faithful-ish, always extracts,
   round-trippable; cost: names differ from the archive, needs a documented, stable escape
   scheme (and interacts with the O2 collision map).

**Recommendation:** sanitize (option 2) under STRICT — it matches "damaged/odd input is a
first-class citizen" and keeps the founding backup-indexing use case extracting everywhere;
TRUSTED keeps today's faithful-bytes behavior. But the escape scheme (which bytes, how
reversed, interaction with Windows mangling) needs one focused exploration pass before
implementation — hence spike. O2/O3/O4 do **not** depend on this and can land first.

## Risks

- Over-rejection surprising users who only extract on Linux → mitigated by policy levels
  (TRUSTED = today's behavior) and clear docs.
- The escape scheme becoming a compatibility surface (once chosen, it's hard to change) →
  argues for settling it deliberately now, before the first public release freezes behavior.
