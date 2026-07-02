# Design — Minimal, meaning-preserving name normalization

## Problem reframe

`member.name` conflates two jobs:

1. a **canonical lookup key** (for `get()`, `_members_by_name`, link resolution), and
2. a **faithful representation** of the stored path (for security decisions and honesty).

Today's normalization optimizes (1) at the cost of (2): collapsing `..` and stripping a
leading `/` makes lookup tidy but hides an unsafe stored name and moves the safety-relevant
string out from under the extraction check. This design chooses (2) and pushes unsafe-path
rejection to extraction, where the destination is actually computed.

## The guarantor already exists

`phase-4-safe-extraction`'s `check_universal` resolves the destination's **parent directory**
and requires it to stay within `dest`:

```
member.name = "foo/x"
   parent = (dest / "foo").resolve()      # follows on-disk symlinks
   inside dest  → OK        outside dest → PathTraversalError
```

This single check catches, on the faithful name:

- an **escaping `..`** — `dest/..` always resolves above `dest`;
- an **absolute path** — resolves outside `dest`;
- a **symlinked intermediate component** — `foo`→/outside then `foo/x`, *with or without* any
  `..`; this threat exists today independent of `..` collapsing.

So making names faithful does not weaken safety — the check that matters runs on the true
name. Read-time `..` collapsing never protected against the symlinked-parent case anyway.

## Decisions

### D1 — Internal, non-escaping `..` is allowed (not rejected)

`foo/../bar` (with `foo` a real directory) resolves to `dest/bar`; the parent-resolve check
still guarantees it cannot escape. `testing-contract` requires rejecting only **escaping**
traversal (`../evil`, `../../etc/passwd`, `./../../outside`), and allowing the internal case
preserves today's net behavior for benign archives (which is exactly what the old collapse
produced, just realized at write time by the OS).

Rejected alternative — *reject any `..`*: simpler mental model but rejects a benign case the
current code silently accepts, exceeding what `testing-contract` asks.

### D2 — `\` → `/` is retained as a documented cosmetic step

Backslash is a legal filename character on POSIX, so converting it is technically
meaning-altering; but a stored `\` is almost always a Windows separator, and extraction's
checks split on **both** separators (`/` and `\`) and treat a leading `\`/UNC as absolute, so
the conversion carries **no safety weight**. Kept for cross-platform ergonomics and to avoid
churn; documented as the one deliberate exception to "meaning-preserving only."

### D3 — TOCTOU hardening (openat2 / O_NOFOLLOW) is out of scope

`resolve()`-then-`open()` is not atomic, but for single-archive, single-threaded extraction
there is no concurrent attacker, and an intra-pass symlink is already on disk when the later
member is checked. This change neither introduces nor worsens the window. Hardening to
`openat2(RESOLVE_BENEATH)` or per-component `O_NOFOLLOW` is a separate, platform-sensitive
improvement.

## Blast radius

`member.name` feeds `get()`, `_members_by_name`, and link resolution. Legitimate archives
carry no `..` and no leading `/`, so their names are byte-identical to today and these paths
are unaffected. Only hostile/malformed names change shape; for them a lookup miss or an
unresolved link is a fail-safe, not a regression. A light audit confirms nothing keys on the
*collapsed* form for correctness on legitimate input.

## Normalization rule set (after)

```
1. \  → /                     (cosmetic separator; see D2)
2. strip leading ./ , /./     (meaning-preserving)
3. //  → /                    (meaning-preserving)
4. trailing / for directories (type annotation)
5. empty / bare root → "."    (never-empty)
—  leading /   : RETAINED in name (was stripped)   → rejected at extraction (absolute)
—  ..          : RETAINED in name (was collapsed)  → escaping rejected, internal allowed
```

A warning on the `archivey.normalization` logger is still emitted when a meaning-preserving
rule changes the presented path.
