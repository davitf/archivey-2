# Minimal, meaning-preserving name normalization

## Why

`normalize_member_name` performs **meaning-altering** rewrites at read time: it strips a
leading `/` (absolute → relative) and collapses `..` sequences, so `/etc/passwd` becomes
`etc/passwd` and `../../etc/passwd` becomes `etc/passwd`, emitting only a warning. Two
problems follow:

1. **`member.name` is not truthful.** A caller listing members before extracting sees a
   silently sanitized path, not the archive's actual (potentially hostile) one — the stored
   intent is hidden, and the `archive-data-model` "traversal collapsed" behavior directly
   contradicts the `safe-extraction` / `testing-contract` requirement that extracting a
   `../evil` member **raise** `PathTraversalError`.
2. **The safety check and the path computation look at different strings.** Extraction
   computes the destination from `member.name`, but the danger lived in the *pre*-normalized
   value. `phase-4-safe-extraction` bridges this with an interim check on `member.raw_name`
   (the verbatim stored bytes) — duplicating path-structure logic in two places that must
   stay in sync.

Collapsing an *internal* `foo/../bar` → `bar` is also only equivalent when `foo` is a real
directory; if `foo` is a symlink (planted by an earlier member) the two differ, so even
"internal" `..` collapse is a filesystem-dependent decision read-time normalization cannot
safely make.

## What Changes

Normalization keeps only **meaning-preserving** steps; unsafe paths are rejected at
**extraction** time, checked on `member.name` (now faithful).

- **`archive-data-model` (MODIFIED):** `normalize_member_name` keeps `//`→`/`, `./` and `/./`
  cleanup, trailing `/` for directories, and empty/root → `"."`. It **no longer** strips a
  leading `/` or collapses `..`; those are retained verbatim in `member.name`. The `\`→`/`
  conversion becomes **format/entry-aware** (a `backslash_is_separator` parameter the backend
  supplies): TAR/POSIX keep `\` as a literal filename character, Windows-origin entries (RAR,
  and ZIP entries whose `create_system` is DOS/Windows) convert it.
- **`safe-extraction` (MODIFIED):** `check_universal` enforces the path constraints directly
  on `member.name` — reject an absolute path, reject **any** `..` component (escaping *or*
  internal), and reject null bytes; the pre-extraction resolution of the destination's
  **parent directory** within `dest` remains the guarantor for `..`-free names (it also catches
  a symlinked-parent escape). The interim `raw_name` structural check from
  `phase-4-safe-extraction` is removed.

This is the default (`RAISE`) extraction behavior. A future opt-in `SANITIZE` policy and a
read-time `on_unsafe_name` block option are part of the phase-5 config surface — see the
layered model in `design.md`.

## Key insight (why this is smaller and safer than it looks)

The `(dest / member.name).parent.resolve()`-within-`dest` check already present in
`phase-4-safe-extraction` is the real guarantor. It catches an escaping `..`, an absolute
path, and — importantly — a **symlinked intermediate component** (`foo`→/outside, then
`foo/x`), a threat that exists today *regardless* of `..` collapsing. So this change does not
introduce that threat and does not need to solve it beyond what extraction already does; it
simply makes `member.name` faithful and lets the existing check run on the true name.

For **legitimate** archives — which carry no `..` and no leading `/` — `member.name` is
byte-identical to today, so the blast radius on lookup / link resolution is nearly zero; only
hostile/malformed names change shape, and for those a lookup miss or unresolved link is a
fail-safe.

## Decisions (see design.md)

- **Extraction `RAISE` (default) rejects any `..`** — escaping *and* internal (`foo/../bar`) —
  plus absolute paths and null bytes. A well-formed archive has no reason to carry `..`, so it
  is treated as almost-certainly-malicious. A future opt-in **`SANITIZE`** policy re-roots such
  names; there is **no** path-safety `TRUST` (traversal is never something to just trust —
  `ExtractionPolicy.TRUSTED` governs permissions only).
- **`\`→`/` is format/entry-aware** — TAR/POSIX keep `\` literal (a legal filename char),
  Windows-origin entries (RAR; ZIP by `create_system`) convert it. Not a safety mechanism:
  extraction checks both separators regardless.
- **`openat2(RESOLVE_BENEATH)` / per-component `O_NOFOLLOW` hardening is out of scope** — the
  `resolve()`+`open()` TOCTOU window is irrelevant for single-archive, single-threaded
  extraction and is unchanged by this work; it is a separate future hardening.

## Impact

- **Sequencing:** land and merge **before** `phase-4-safe-extraction` is finalized, then drop
  that change's interim `raw_name` check and point `check_universal` at `member.name`.
- **Affected code:** `internal/naming.py` (`normalize_member_name`); a light audit of
  `get()` / `_members_by_name` / link resolution for reliance on the collapsed form;
  `internal/filters.py` (`check_universal`, once phase-4b lands).
- **Affected tests:** `tests/test_naming.py` normalization cases; the `safe-extraction`
  traversal/escape tests continue to assert rejection.
