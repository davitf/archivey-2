# 0013 — Cross-platform extraction name-safety policies

- **Status:** accepted
- **Date:** 2026-07-15 (`cross-platform-name-safety`)
- **Provenance:** that change's design; OpenSpec `safe-extraction`; threat model
  O2/O3/O4/O7 (`docs/internal/threat-model.md`)

## Context

Four threat-model items share one root cause: a member name that is fine on the archive's
origin OS but collides, mangles, or fails on the destination OS. Today the outcome is
platform-dependent — a case/Unicode collision silently merges under `REPLACE` on
case-insensitive filesystems only (O2); Windows-reserved names, trailing dots/spaces, and
`:` are silently mangled (O3/O4); and a name that is valid bytes but unrepresentable
(surrogateescape `caf\udce9.txt`) writes on ext4 but raises `EILSEQ` on APFS (O7). "Safe by
default" is load-bearing, so this dimension must become deterministic across platforms,
keyed off the existing `ExtractionPolicy` (STRICT/STANDARD/TRUSTED). `STRICT` is redefined as
*portable-by-default*; `TRUSTED` keeps today's faithful-bytes / local-OS behavior.

The design pass had to settle six coupled questions, two of which were internal doc
conflicts rather than open space.

## Decision

1. **O7 — sanitize, not reject.** Unrepresentable names are normalized to a reversible
   portable spelling under `STRICT`/`STANDARD` (`TRUSTED` keeps faithful bytes). *Reject*
   would force a Linux user to drop to `TRUSTED` — a permission/ownership decision — just to
   extract an oddly-named backup member, coupling two unrelated axes and defeating the
   founding backup-indexing use case at the default policy. Both options change today's
   Linux-STRICT behavior anyway (reject refuses; sanitize rewrites), so the tie-breaker is
   which default is more useful — extracting beats refusing.

2. **O7 scheme.** Percent-escape each non-UTF-8 byte (surrogateescape char U+DC80–U+DCFF →
   raw byte) as `%XX` (uppercase); escape a literal `%` as `%25`. Non-decodable bytes only —
   valid Unicode (NFC/NFD) is representable everywhere and is left alone (its folding is O2's
   job). Applied on every OS for determinism. Percent-encoding of raw bytes is the single
   most standard scheme, minimizing the frozen-compatibility-surface risk. Reversibility is a
   documented property; a public un-escape API is deferred (addable non-breakingly).

3. **O2 — per policy level (resolves a doc conflict).** The casefold+NFC collision key
   applies under `STRICT` and `STANDARD` on all platforms; `TRUSTED` keys on the exact path
   and defers to the local OS. The design's "all platforms" and the delta table's "TRUSTED =
   local OS behavior" were each describing a different level, not contradicting. On a
   case-sensitive filesystem `README`/`readme` are two legitimate files, so forcing a
   collision universally would be lossy — it is a deliberate `STRICT`/`STANDARD` portability
   constraint. `STANDARD` is included because a silent-merge footgun is exactly what
   "portable but not paranoid" should still catch.

4. **O3/O4 — STANDARD allows trailing dot/space.** `:` (NTFS ADS injection) and reserved
   device names (device capture) are unambiguous hazards → rejected even under `STANDARD`.
   Trailing dot/space is a rare, low-severity, Windows-only mangle, valid on POSIX → `STRICT`
   rejects (portable-by-default), `STANDARD` tolerates. The crafted `foo.`/`foo` merge is an
   untrusted-input concern that `STRICT` already covers.

5. **`OverwritePolicy.RENAME` lands here (resolves a doc conflict).** An earlier design draft
   listed a full implementation as a Non-Goal; the proposal, tasks, spec delta, and brief all
   scope it in. It reuses the O2 collision map and the CLI's `extract` wants `unzip`-parity,
   landing before the CLI phase — so it stays. Spelling: ` (N)` inserted **before the final
   suffix** (`photo.jpg` → `photo (1).jpg`) to preserve the extension (type detection / open
   behavior), matching `unzip` / Explorer / browsers. `Path.stem`/`suffix` semantics fix the
   edge cases (dotfiles, multi-suffix, directories); `N` increments to the first name free on
   disk and in the collision map.

6. **Collision recording.** `ExtractionResult` gains `requested_path: Path | None` (intended
   destination pre-resolution); a rename is `requested_path != path and status == EXTRACTED`.
   The security-relevant audit trail — a key clash with an earlier written member, including
   the `REPLACE` case where the result is a plain `EXTRACTED` — is an
   `EXTRACTION_NAME_COLLISION` diagnostic in the report aggregate, consistent with
   "`ExtractionResult` has no diagnostics field." Inference from `member.name` is rejected
   (conflated by the policy transform, O7 sanitize, and user-filter renames); a bare
   `collided: bool` is rejected (loses location and the prior path).

## Consequences

- `STRICT`/`STANDARD` become portable-by-default: they may rewrite (O7), reject (O3/O4), or
  treat casefold/NFC clashes as collisions where Linux would keep both files. `TRUSTED`
  preserves today's faithful-bytes / local-OS behavior — the documented escape hatch.
- The O7 percent-escape scheme is a compatibility surface, pinned in the spec before the
  first public release freezes it.
- Public surface grows by one `ExtractionResult` field (`requested_path`, appended with a
  default), one diagnostic code, and one `OverwritePolicy` member (`RENAME`).
- O2/O3/O4 and O7 share one coordinator collision map; sanitized spellings flow through it.
