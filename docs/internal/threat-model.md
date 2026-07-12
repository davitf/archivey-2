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
- **Extraction-root overwrite:** a *file* member whose normalized name is `"."` or `""`
  is rejected (`PathTraversalError`); only a directory member may name the extraction
  root. Prevents a corrupt archive from replacing the destination directory with a
  regular file (`internal/filters.py` `check_universal`).
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

### O1. Listing-time resource exhaustion (metadata bombs) — mitigated

`ListingLimits` on `ArchiveyConfig` (`max_members`, `max_metadata_bytes`) are enforced
when members are registered into a materialized / resolved list (`members()`,
`scan_members()`, extract-prep materialization). Crossing a cap raises
`ResourceLimitError`. Defaults match extract `max_entries` on the count side
(`1_048_576`) and budget 64 MiB of retained string/bytes metadata.
`stream_members()` / forward-only iteration remain unguarded by design (O(1) escape
hatch). Format-local parser bounds (e.g. 7z `num_files` vs header size →
`CorruptionError`) stay as defense-in-depth.

`read()` / `open()` stream sizes remain unbounded (follow-on); prefer chunked
reads for untrusted member payloads.

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

### O5. Fuzzing — mutation + Hypothesis + Atheris gate landed; OSS-Fuzz / SECURITY.md later

The safety claims rest on curated tests plus three complementary fuzz layers. Remaining
work before any public "safe" claim is release packaging (OSS-Fuzz + disclosure docs),
not the in-tree gate:

1. **Landed:** the corpus **mutation harness** (`tests/test_mutation_fuzz.py`) — every
   corpus archive is deterministically mutated (truncations, bit flips, zeroed blocks,
   garbage prefixes/suffixes) and driven through open/list/read/extract + detection,
   asserting *typed `ArchiveyError` or success — never a raw exception, never a hang*. It
   exercises archivey's own **deterministic zero-dep parsing path** (accelerators forced
   off) and already found and fixed a batch of untranslated-exception bugs in the ZIP and
   ISO backends. `ARCHIVEY_FUZZ_MUTATIONS` deepens the sweep; green at 500 mutations/kind.
   Env-gated 7z parser mutation (`ARCHIVEY_FUZZ=1` / `tests/fuzz_sevenzip_parser.py`)
   remains available for local deepening.
2. **Landed:** property-based tests (Hypothesis) for the pure safety logic
   (`tests/test_property_safety.py` — `normalize_member_name`, `check_universal`,
   `resolve_link_target_name`, volume discovery, detection over arbitrary prefixes).
3. **Landed:** coverage-guided **Atheris** harness (`tests/atheris_fuzz/`) over native 7z
   and RAR header parse (CRC mutate-then-fixup), 7z/RAR open+members, `detect_format`,
   and shallow ZIP/TAR/ISO open+list. CI runs on **push to `main`** + **`workflow_dispatch`**
   (partitioned ~150s; not the PR matrix; not always-on nightly). `atheris` lives in the
   PEP 735 `fuzz` dependency group only — never a runtime extra. See
   `openspec/specs/testing-contract/spec.md`.
4. **Still open (public release):** OSS-Fuzz onboarding; `SECURITY.md` with a disclosure
   process. Accelerator hang sandbox (below) remains a separate follow-up.

**Accelerator hang (found by the mutation harness).** The optional `[seekable]`
accelerators (`rapidgzip`, and its bundled bzip2 decoder) are third-party C++ that can
**busy-loop on crafted input** — a hang no Python-level translator can convert into an
`ArchiveyError`, and one that SIGALRM/pytest-timeout cannot cleanly interrupt (the loop is
in a C++ thread). So the mutation and Atheris harnesses run with accelerators **off**, and
fuzzing that native code is deferred to a **resource-limited subprocess sandbox**
(wall-clock + memory capped, killed on breach). Until then: the accelerators are an
opt-in performance path, not part of the defended parsing surface for untrusted input —
callers processing untrusted archives under a hard latency budget should leave them off
(`AcceleratorMode.OFF`) or enforce their own timeout. Worth surfacing in the eventual
`SECURITY.md`.

**pycdlib directory-cycle hang (found by the mutation harness).** `pycdlib` can **loop
forever** in ``_walk_directories`` whenever corrupt directory records form a back-edge
(plain ISO 9660 PVD, Rock Ridge PVD, Joliet SVD — any namespace ``open_fp`` walks). The
harness found a Joliet case (`bitflip@71746:0x01` on `basic-iso`); the same one-bit
corruption in ``/subdir``'s directory extent reproduces on plain-only and Rock-Ridge-only
images built the same way (`tests/test_iso.py::test_pycdlib_directory_cycle_does_not_hang`
parametrizes all three). The ISO backend installs a one-time guard that skips
re-enqueueing a directory extent already scheduled (valid trees never revisit an extent).

**Destination-root poisoning via `"."` file member (found by the mutation harness).**
Corrupted headers can surface a *file* (not a directory) whose normalized name is `"."`
— e.g. `bitflip@107:0x10` on `adversarial-tar.tar.gz`. Extracting it would write through
the destination path itself, replacing the extraction directory with a regular file
("poisoned dest"). `check_universal` now rejects non-directory members that name the
extraction root; the parametrized fuzz loop also asserts the destination stays a
directory after any successful extract. Unit coverage:
`test_check_universal_rejects_root_named_file` and `test_extract_error_when_dest_is_a_file`
in `tests/test_extraction.py`.

### O6. Nested-archive amplification

Opening archives-inside-archives is supported (and `size` advertisement makes it
cheap); recursion is caller-driven, so a zip-quine (`droste.zip`) only loops if the
caller loops. Still worth an explicit documented stance + a recipe for bounded
recursive processing, since "index my backups" — the founding use case — does exactly
this.

### O7. Names representable as bytes but not by the target filesystem

`check_universal` rejects names that cannot be `os.fsencode`d at all (a lone surrogate
outside the surrogateescape range — see `internal/filters.py`). It does **not** reject a
name that *is* fsencodable but that the destination filesystem refuses at `write()`: a
non-UTF-8 byte sequence carried via surrogateescape (`caf\udce9.txt`) is transparent on
ext4/most Linux but raises `OSError` (`EILSEQ`, "Illegal byte sequence") on APFS/macOS
and other UTF-8-enforcing filesystems. Today that surfaces as an ordinary per-member
write failure (a `FAILED` `ExtractionResult`, or a re-raised `OSError` under
`OnError.STOP`) — safe (no traversal, no abort) but **platform-dependent** and *not* a
faithful round-trip. On Windows the mirror hazard is the O3 one: a name the OS silently
mangles or that becomes hard to delete/rename. Covered by
`test_surrogateescape_name_extracts_safely_or_is_cleanly_refused` (asserts the
safety-or-clean-refusal invariant, not round-trip).

**Landed (error honesty):** the write-time `OSError` (`EILSEQ`) for a filter-accepted
but unrepresentable name is now translated by the extraction coordinator to a typed
`ExtractionError` naming the member ("Member name cannot be represented on the
destination filesystem"), so callers get a typed signal instead of a bare `OSError`
(`internal/extraction.py`; `test_unrepresentable_name_oserror_is_translated`).

**Open follow-up (portable-name normalization):** fold into the O3/O4 policy work as the
"cross-platform portable name" dimension. Recommendation: `STRICT` normalizes to an
always-representable, portable form on **every** platform — decode-lossy names sanitized
to a deterministic safe spelling (a reversible/percent-style escape, collision-tracked
like O2), rejecting only when even that cannot be formed; `TRUSTED` attempts the faithful
bytes and lets the local OS decide (today's behavior). This sanitization is **deferred**
(deliberately not built with the error-translation above) and needs a `safe-extraction`
delta shared with O3/O4.

## OPEN gaps — compatibility

### C1. The RAR decompressor matrix (and unrar licensing) — won’t-do / closed

RAR member data requires an external tool. `unrar` is **non-free** (freeware license);
`unrar-free` handles little of RAR5; `7z`/`bsdtar` coverage varies by build; `unar`
exists on macOS. A multi-tool fallback matrix would otherwise degrade into "works on my
machine" plus divergent solid/password behavior.

*Decision (closed):* Archivey supports **RARLAB `unrar` only** for RAR member data.
Non-RARLAB binaries on `PATH` raise `PackageNotInstalledError` naming RARLAB `unrar`;
there is no silent fallback to `unrar-free` / `unar` / `7z`. Licensing remains a
documented system dependency (archivey itself stays permissively licensed). See
ADR [`0002-native-rar-metadata-unrar-data`](../decisions/0002-native-rar-metadata-unrar-data.md)
and OpenSpec `format-rar`.

### C2. Warnings that should be data — addressed

Addressed by the lifecycle-aware diagnostics capability (`diagnostics-warnings-as-data`):
advisories are immutable `Diagnostic` values with stable codes, attached to
lifecycle-appropriate surfaces (`FormatInfo`, `ArchiveReader`/`ArchiveStream`,
`ArchiveMember`, `ExtractionReport`), with per-code policy (`IGNORE`/`COLLECT`/`RAISE`)
and a shared retention budget. Logging remains the zero-config projection.

### C3. Metadata fidelity boundary (xattrs/ACLs/forks)

PAX xattrs currently survive only inside `extra["tar.pax_headers"]`; ACLs, macOS
resource forks, and NTFS ADS are untouched. Read-side promotion to a first-class field
later is additive/cheap; applying xattrs at extraction is moderate (policy
interactions); true fidelity only binds when **writing** lands (deferred, possibly
post-1.0). Decision recorded in `IDEAS.md`; revisit at writing-spec time.

### C4. Free-threaded Python

`3.13t+` makes data races visible and parallel pure-Python decode realistic.
On readers that declare `MemberStreams.CONCURRENT`, after random-access member
materialization, concurrent `open()` plus independent operations on different member
streams are data-race-free on ordinary builds and on backend/runtime combinations covered
by the required Linux CPython `3.13t` `free-threaded-concurrency` job; optional backends
are not claimed covered until a dedicated free-threaded job can run them. The undeclared
default is one live member stream (a second overlapping open raises `ConcurrentAccessError`),
so accidental cross-thread stream sharing fails fast instead of racing. Iteration,
materialization, extraction, `stream_members()`, and reader close remain single-owner,
with explicit private child scopes allowing extraction to drive its pass and
yielded-stream I/O. Implementation
must use real synchronization rather than relying on the GIL. Parallel extraction scheduling
remains future, and speed claims require measurements proportionate to the mechanism changed.
Accelerator close-before-finalize
(`known-issues.md`) still applies, so member-stream lifecycle leases defer backend teardown
until the final stream closes. See [`parallel-reader.md`](../grab-bag/parallel-reader.md) §4.
