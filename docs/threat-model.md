# Threat model and security/compatibility gap register

> The trust boundaries archivey defends, what is already enforced, and — importantly —
> the **known open gaps** identified in the 2026-07 architecture review, recorded here so
> they are not lost. Each open item should become an OpenSpec change (usually a
> `safe-extraction` or `archive-reading` delta) when tackled; this document is the
> holding area and the rationale, not the normative spec.

## Trust boundaries

- **The archive is untrusted.** Every byte of it: member names, link targets, sizes,
  timestamps, comments, header structures, compressed streams. Crafted and adversarial
  archives are in scope for *all* guarantees, not just well-formed ones.
- **The destination directory and local filesystem are trusted at rest** — but not
  their *contents produced by the extraction itself*: an earlier extracted member is
  untrusted input to the handling of every later member (this is why symlink targets
  are re-resolved against the live tree after creation).
- **The local process and other local processes are trusted.** Concurrent hostile
  modification of the destination *by another process* during extraction (a local
  attacker racing us) is out of scope; if that ever changes, `O_NOFOLLOW`/`openat`-style
  extraction is the direction.
- **Optional dependencies and external tools** (`pycdlib`, codec packages, the `unrar`
  binary) are trusted code but *not* trusted to be robust: their failures must surface
  as translated archivey errors, never silently wrong data.

## What is already enforced (implemented + specced)

- **Path traversal:** `..` components (any separator), absolute paths, drive letters,
  UNC prefixes, and null bytes are rejected before any write; the destination parent is
  resolved and containment-checked (`safe-extraction`, `internal/filters.py`).
- **Symlink escapes, three layers:** lexical target check at planning time; parent-dir
  resolution; and post-`os.symlink` re-resolution against the real filesystem (catches
  chained-symlink attacks staged by earlier members). Escaping links are removed and
  rejected.
- **Hardlink targets** are containment-checked and resolved positionally (an earlier
  same-named member), so a crafted duplicate-name archive cannot redirect a link.
- **Never write through a symlink:** overwrite handling replaces symlinks, never
  follows them; atomic temp-file + `os.replace` writes mean interrupted extraction
  never leaves a half-written destination file.
- **Special files** (devices, FIFOs, sockets) are always rejected; NTFS junctions are
  detected, flagged, and never traversed.
- **Decompression bombs at extraction:** cumulative output cap, per-member ratio,
  archive-wide static ratio, **live** ratio for unknown-size/pipe sources, and an entry
  count cap — the global guards halt even under `OnError.CONTINUE`.
- **Permission hygiene:** setuid/setgid/sticky stripped except under `TRUSTED`;
  ownership applied only under `TRUSTED` as root.
- **Error honesty:** codec/library exceptions are translated to typed `ArchiveyError`s
  with context; genuine I/O errors propagate unchanged; no catch-all handlers.
- **Accelerator lifecycle:** C++-threaded accelerators are close-guarded
  (`weakref.finalize`) so crafted-input error paths cannot leave aborting threads
  (see `known-issues.md`).

## OPEN gaps — security

### O1. Listing-time resource exhaustion (metadata bombs)

`max_entries` and the byte/ratio guards protect **extraction only**. `members()` /
`scan_members()` will happily materialize whatever the header claims: a small ZIP can
carry hundreds of thousands of central-directory entries (or enormous
comments/PAX blobs), costing gigabytes of `ArchiveMember` objects at *listing* time —
before any extraction guard runs. `read()` is likewise documented as unbounded.

*Direction:* listing guards in `ArchiveyConfig` (max member count, max total metadata
bytes) enforced in `_get_members_registered`/the progressive pass; keep iteration
(`stream_members`) as the unguarded-by-design escape hatch since it is O(1) in members.

### O2. Case-insensitivity and Unicode-normalization collisions at extraction

Two members whose names differ only by case (`README` / `readme`) or Unicode
normalization form (NFC vs NFD `café`) are distinct in the archive but the **same file**
on default Windows/macOS filesystems. Today: under `OverwritePolicy.ERROR` the second
member fails with a confusing "already exists"; under `REPLACE` it **silently merges**
— a crafted archive can use this to make content clobber other content on
case-insensitive systems only (behavior differs by platform: a "surprise" squared).

*Direction:* the coordinator tracks a casefolded+NFC key per written path and treats a
collision as a first-class event on **all platforms** (deterministic cross-platform
behavior): apply the `OverwritePolicy` deliberately, record the collision on the
`ExtractionResult`, and consider a future `OverwritePolicy.RENAME` (extract as
`name (1)`) for the archives-with-intentional-duplicates case. Needs a
`safe-extraction` delta.

### O3. Windows name mangling: reserved names, trailing dots/spaces

`CON`, `NUL`, `COM1`… are device names; `foo.` and `foo ` are silently stripped by
Win32 to `foo` (silent clobber / mismatch between reported and actual path). None of
this is currently checked; behavior is platform-dependent.

*Direction:* decide per policy — recommendation: `STRICT` rejects Windows-reserved and
trailing-dot/space names on **every** platform (portability is part of no-surprises);
`TRUSTED` allows what the local OS allows. `safe-extraction` delta.

### O4. NTFS alternate data streams

A member name containing `:` (`file.txt:hidden`) writes an invisible alternate data
stream on NTFS. Not currently rejected.

*Direction:* fold into the O3 policy work (reject `:` in names under `STRICT` on all
platforms; it is never a portable filename character).

### O5. Fuzzing does not exist yet

The safety claims rest on curated tests. Before the native 7z/RAR parsers (which parse
untrusted binary headers in Python) ship — and before any public "safe" claim:

1. **Now:** property-based tests (Hypothesis) for the pure safety logic
   (`normalize_member_name`, `check_universal`, `resolve_link_target_name`, volume
   discovery, detection over arbitrary prefixes); a mutation harness (bit-flips /
   truncations over the generated corpus) asserting *never crashes, never hangs,
   always a typed `ArchiveyError` or correct data*.
2. **Native-reader entry gate:** coverage-guided fuzzing (Atheris) of the 7z/RAR
   header parsers, seeded from the corpus + adversarial fixtures; nightly short runs
   in CI.
3. **At public release:** OSS-Fuzz onboarding; `SECURITY.md` with a disclosure
   process.

### O6. Nested-archive amplification

Opening archives-inside-archives is supported (and `size` advertisement makes it
cheap); recursion is caller-driven, so a zip-quine (`droste.zip`) only loops if the
caller loops. Still worth an explicit documented stance + a recipe for bounded
recursive processing, since "index my backups" — the founding use case — does exactly
this.

## OPEN gaps — compatibility

### C1. The RAR decompressor matrix (and unrar licensing)

RAR member data requires an external tool. `unrar` is **non-free** (freeware license);
`unrar-free` handles little of RAR5; `7z`/`bsdtar` coverage varies by build; `unar`
exists on macOS. "Maximum compatibility" will otherwise degrade into "works on my
machine".

*Direction:* decide and **test** a supported decompressor matrix (candidate: prefer
`unrar`, fall back `7z`, document capabilities per tool), surface which tool was used
via `MissingComponent`-style data, and document the licensing situation prominently
(archivey itself stays permissively licensed; the binary is a system dependency).
Feeds the Phase-6 native-RAR design directly.

### C2. Warnings that should be data

Name normalization, format-detection conflicts, and O(n) rewinds warn via `logging` —
invisible to most applications, so the "no surprises" property silently degrades to
"surprises, but logged". Sweep candidates: normalization changes → a flag/field on the
member (`raw_name` already preserves truth); detection conflicts → already in
`FormatInfo`; rewind cost → already on `CostReceipt`; audit for the rest. Backlog in
`IDEAS.md`.

### C3. Metadata fidelity boundary (xattrs/ACLs/forks)

PAX xattrs currently survive only inside `extra["tar.pax_headers"]`; ACLs, macOS
resource forks, and NTFS ADS are untouched. Read-side promotion to a first-class field
later is additive/cheap; applying xattrs at extraction is moderate (policy
interactions); true fidelity only binds when **writing** lands (deferred, possibly
post-1.0). Decision recorded in `IDEAS.md`; revisit at writing-spec time.

### C4. Free-threaded Python

`3.13t+` makes parallel extraction and parallel pure-Python decode realistic; the
"one reader per thread" rule and the C++-thread accelerators need a position statement
before users ask. Backlog.
