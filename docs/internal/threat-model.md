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
- **Cross-platform name safety (STRICT/STANDARD):** casefold+NFC collision tracking,
  reserved device names and `:` rejected, trailing-dot/space strip, non-UTF-8
  percent-escape sanitization, `OverwritePolicy.RENAME` (ADR 0013 / PRs #109/#123).
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
`CorruptionError`; RAR member-count ceiling at parse) stay as defense-in-depth.
Indexed formats (7z/RAR) may still allocate up to those parser ceilings during
`open_archive()` before spine listing caps apply.

`read()` / `open()` stream sizes remain unbounded (follow-on); prefer chunked
reads for untrusted member payloads.

### O2. Case-insensitivity and Unicode-normalization collisions at extraction — implemented

Two members whose names differ only by case (`README` / `readme`) or Unicode
normalization form (NFC vs NFD `café`) are distinct in the archive but the **same file**
on default Windows/macOS filesystems. Pre-fix behavior under `OverwritePolicy.ERROR`
was a confusing "already exists"; under `REPLACE`, a silent merge on case-insensitive
systems only.

**Implemented** (`cross-platform-name-safety` / ADR 0013 / PR #109): the coordinator
tracks a casefolded+NFC key per written path and, under `STRICT`/`STANDARD`, treats a
collision as a first-class event on **all platforms** (`TRUSTED` keys on the exact path
and defers to the local OS): apply the `OverwritePolicy` deliberately, record
`requested_path` on the `ExtractionResult` plus an `EXTRACTION_NAME_COLLISION`
diagnostic, and support `OverwritePolicy.RENAME` (extract as `photo (1).jpg`, counter
before the suffix). Only content-bearing members (file/symlink/hardlink, including the
deferred orphan-hardlink pass) are tracked; **directories are intentionally untracked**
(they merge structurally), so a *file* `Foo` vs a *directory* `foo/` collision stays
OS-dependent — a known, deferred residual (ADR 0013).

### O3. Windows name mangling: reserved names, trailing dots/spaces — implemented

`CON`, `NUL`, `COM1`… are device names; `foo.` and `foo ` are silently stripped by
Win32 to `foo` (silent clobber / mismatch between reported and actual path).

**Implemented** (ADR 0013, revised 2026-07 / PR #109 + #123): reserved device names and
`:` are *unsafe* (device capture / NTFS ADS) → rejected under `STRICT` and `STANDARD` on
every platform. A trailing dot/space is a *legitimate* macOS/Linux name Win32 merely
trims → `STRICT` **strips** it to the portable spelling (`stuff_etc.` → `stuff_etc`),
deterministic per-OS, collision-tracked, and surfaced as an `EXTRACTION_NAME_SANITIZED`
diagnostic (an all-dots segment like `...` has no portable spelling and is still
rejected); `STANDARD`/`TRUSTED` keep it faithful.

### O4. NTFS alternate data streams — implemented (folded into O3)

A member name containing `:` (`file.txt:hidden`) would write an invisible alternate data
stream on NTFS.

**Implemented** as part of O3: `:` in names is rejected under `STRICT` and `STANDARD` on
all platforms (it is never a portable filename character).

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
   and RAR header parse (CRC mutate-then-fixup), 7z/RAR open+members (CI installs
   RARLAB `unrar` so RAR open is not skipped), `detect_format`, ZIP open+list+bounded
   member read (native codec/AES), TAR/ISO open+list, and standalone stream/codec
   targets (unix-compress, xz, lzip, gzip, bzip2, lzma-alone, zlib; optional
   zstd/brotli/lz4/deflate64 skip-clean when absent). CI runs a **short** partition on
   every **pull request** (sharded for wall time), and the **full** partition on a
   **change-guarded nightly** (skip unless default-branch HEAD moved in ~3 days) plus
   **`workflow_dispatch`** — same pattern as the benchmark wall job; not an always-on
   nightly and not a full run on every `main` push. `atheris` lives in the PEP 735
   `fuzz` dependency group only — never a runtime extra. See
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

### O7. Names representable as bytes but not by the target filesystem — implemented

`check_universal` rejects names that cannot be `os.fsencode`d at all (a lone surrogate
outside the surrogateescape range — see `internal/filters.py`). Names that *are*
fsencodable but that some filesystems refuse at `write()` (e.g. surrogateescape
`caf\udce9.txt` → `EILSEQ` on APFS) used to surface as a platform-dependent per-member
write failure.

**Implemented** (ADR 0013 / PR #109):

- Write-time `OSError` (`EILSEQ`) for a filter-accepted but unrepresentable name is
  translated to a typed `ExtractionError` naming the member
  (`test_unrepresentable_name_oserror_is_translated`).
- Under `STRICT`/`STANDARD`, non-UTF-8 bytes are **percent-escaped** to a deterministic
  reversible portable spelling (`%XX`; literal `%` → `%25`), applied on every platform
  and collision-tracked like O2; only names that cannot be `os.fsencode`d at all are
  still rejected. `TRUSTED` attempts the faithful bytes and lets the local OS decide.

Residual: a public un-escape helper is deferred (addable non-breakingly). User-facing
notes: [Gotchas — Extraction](../gotchas.md#extraction), ADR 0013.

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
