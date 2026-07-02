# Design — Minimal, meaning-preserving name normalization

## Problem reframe

`member.name` conflates two jobs:

1. a **canonical lookup key** (for `get()`, `_members_by_name`, link resolution), and
2. a **faithful representation** of the stored path (for security decisions and honesty).

Today's normalization optimizes (1) at the cost of (2): collapsing `..` and stripping a
leading `/` makes lookup tidy but hides an unsafe stored name and moves the safety-relevant
string out from under the extraction check. This design chooses (2): names are faithful, and
judgment about unsafe paths is made explicitly — never by a silent read-time rewrite.

## The layered model (north star)

Two independent layers, on the principle **reading is observation (honest, harmless);
extraction is action (safe-by-default)**:

```
                       ┌───────────────────────────────────────────────┐
   READ TIME           │ open_archive / iteration                      │
   (observation)       │  names are FAITHFUL (this change)             │
                       │  on_unsafe_name = ALLOW (default) | BLOCK      │  ← phase 5 config
                       │    ALLOW → expose the true name (triage,       │
                       │            scanners, repackers see it)         │
                       │    BLOCK → raise on ../ , absolute, null, …    │
                       │  NO read-time "sanitize" (silent rewrite is    │
                       │    exactly the bug being removed)              │
                       └───────────────────────────────────────────────┘
                       ┌───────────────────────────────────────────────┐
   EXTRACT TIME        │ extract / extract_all                         │
   (action)            │  path_safety = RAISE (default) | SANITIZE     │  ← RAISE now (4b),
                       │    RAISE    → reject any unsafe name           │    SANITIZE phase 5
                       │              (.. escaping OR internal,         │
                       │               absolute, null)                 │
                       │    SANITIZE → re-root/collapse to a safe       │
                       │               in-dest path, then extract      │
                       │  NO path-safety TRUST — traversal is never     │
                       │    something to "just trust". (ExtractionPolicy│
                       │    .TRUSTED governs PERMISSIONS only; path     │
                       │    safety stays non-bypassable.)               │
                       └───────────────────────────────────────────────┘
```

**What lands where**

| Piece | Change |
|---|---|
| Faithful names (meaning-preserving normalization, format-aware `\`) | **this change** |
| Extraction `RAISE` on `member.name` (drop interim `raw_name`) | **phase-4-safe-extraction** |
| Read-time `on_unsafe_name = ALLOW/BLOCK` | **phase 5** config surface |
| Extraction `SANITIZE` policy | **phase 5** config surface |

## The guarantor already exists

`phase-4-safe-extraction`'s `check_universal` resolves the destination's **parent directory**
and requires it within `dest`:

```
member.name = "foo/x"
   parent = (dest / "foo").resolve()      # follows on-disk symlinks
   inside dest  → OK        outside dest → PathTraversalError
```

On the faithful name this catches an escaping `..`, an absolute path, and — crucially — a
**symlinked intermediate component** (`foo`→/outside then `foo/x`, with or without any `..`),
a threat that exists today independent of `..` collapsing. So making names faithful does not
weaken safety; the check that matters runs on the true name.

## Decisions

### D1 — Extraction `RAISE` rejects any `..` (escaping and internal); `SANITIZE` re-roots

An internal `foo/../bar` is not merely an escape risk — a well-formed archive has no reason to
contain it, so it is treated as almost-certainly-malicious. Under the default `RAISE`,
`check_universal` rejects **any** name containing a `..` component (and any absolute path,
null byte). This simplifies the check: a string test on `member.name` (split on both
separators), no escaping-vs-internal resolve distinction; the parent-resolve layer remains
purely as the symlinked-parent guarantor for `..`-free names. A caller who genuinely must
extract such an archive opts into `SANITIZE` (phase 5), which re-roots/collapses to a safe
in-`dest` path — the old collapse behavior, now explicit and opt-in at extraction.

Rejected alternative — *allow internal `..`, resolve in-root*: more permissive, but treats a
suspicious path as routine and complicates the check for no real-world benefit.

### D2 — `\` → `/` conversion is format/entry-aware, not universal

Backslash is a **legal filename character** on POSIX, so TAR (and other POSIX formats) MUST
keep it literal — converting would corrupt a valid name (`tarfile` keeps it literal for this
reason). Windows-origin formats use `\` as a **separator** and convert it. The most correct
rule is per-entry where the format records origin:

- **TAR / POSIX:** keep `\` literal.
- **RAR / Windows-native:** convert `\` → `/`.
- **ZIP:** per-entry via `create_system` — convert for DOS/Windows entries (`FAT`,
  `WINDOWS_NTFS`, `VFAT`, `OS2_HPFS`, …), keep literal for `UNIX`. (A per-format fallback —
  "ZIP converts" — is acceptable if per-entry proves fiddly, but per-entry is the correct
  target.)

Mechanism: `normalize_member_name` gains a `backslash_is_separator: bool` parameter the
backend supplies (TAR `False`; RAR `True`; ZIP from the entry's `create_system`).

Security note: this is not a safety mechanism. Extraction's `..`/absolute check splits on
**both** `/` and `\`, so a literal-backslash `..\..\evil` in a tar is still rejected. The one
cost is a rare safe-side false positive — a legitimate POSIX name like `foo\..` (literal
backslash) could be over-rejected at extraction; acceptable given how exotic such names are.

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
unresolved link is a fail-safe, not a regression.

## Normalization rule set (after)

```
1. \  → /   ONLY when backslash_is_separator  (D2: TAR keeps literal)
2. strip leading ./ , collapse interior /./   (meaning-preserving)
3. //  → /                                    (meaning-preserving)
4. trailing / for directories                 (type annotation)
5. empty / bare root → "."                    (never-empty)
—  leading /   : RETAINED in name (was stripped)   → rejected at extraction (absolute)
—  ..          : RETAINED in name (was collapsed)  → rejected at extraction (RAISE),
                                                       re-rooted under SANITIZE (phase 5)
```

A warning on the `archivey.normalization` logger is still emitted when a meaning-preserving
rule changes the presented path.
