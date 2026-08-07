# Safe extraction

Archivey extracts **safely by default**. You opt *out* of protections; you do not opt in.

## One-shot

```python
archivey.extract("archive.zip", "out/")
# policy=ExtractionPolicy.STRICT, overwrite=ERROR, on_error=STOP
```

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

## What is enforced

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
- **Deceptive names:** a member name (or link target) containing a Unicode bidi
  **override or isolate** — U+202A–202E, U+2066–2069 — is rejected with
  `DeceptiveNameError`. Those characters reorder the surrounding text, which is how
  `evil‮gnp.exe` displays as `evil.png` in every listing a person will see. The three
  *directional marks* (U+061C, U+200E, U+200F) are **not** rejected: they reorder
  nothing and occur in legitimate Arabic and Hebrew filenames. Right-to-left script
  itself is unaffected — `فهرس.txt` contains no control character at all. Listing and
  reading still present either kind exactly as stored, with a
  `MEMBER_NAME_BIDI_CONTROL` diagnostic; only writing one to disk is refused.
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

Atomic file writes stage into temp siblings named `.archivey-tmp-<random>` inside the
destination directory. Any Python-level failure removes them; only a hard kill
(SIGKILL, power loss) can leave one behind. Leftover `.archivey-tmp-*` files in an
extraction destination are archivey's staging files and are safe to delete before
re-running the extraction.

## Policies

```python
from archivey import ExtractionPolicy, OverwritePolicy, OnError, ExtractionLimits, ListingLimits

archivey.extract(
    "archive.zip",
    "out/",
    policy=ExtractionPolicy.STRICT,       # default
    overwrite=OverwritePolicy.ERROR,      # or REPLACE / SKIP
    on_error=OnError.STOP,                # or CONTINUE — failures only
    limits=ExtractionLimits(...),         # or ExtractionLimits.UNLIMITED
)

with archivey.open_archive(
    "huge.zip",
    config=archivey.ArchiveyConfig(listing_limits=ListingLimits(max_members=10_000)),
) as reader:
    reader.members()  # ResourceLimitError if the central directory is larger

```

`OnError` governs per-member **failures** (corrupt/truncated data, write errors,
overwrite conflicts under `ERROR`). A policy **block** — an unsafe member refused by a
universal path-safety check or a policy filter — is always recorded as `BLOCKED` and
extraction continues, under either `STOP` or `CONTINUE`. Aborting the whole archive on
the first unsafe member (fail-closed strict security) is a separate, future opt-in; until
then, inspect the returned `ExtractionReport` for `BLOCKED` results if you need to raise
yourself.

| Policy | Intent |
| --- | --- |
| `STRICT` | Untrusted archives (default) |
| `TRUSTED` | Allow ownership / sticky bits when running as root; still no traversal |

Selective extract on an open reader:

```python
with archivey.open_archive("a.zip") as reader:
    reader.extract_all("out/", members=["only/this.txt"])
```

## Names change on disk

Archive order and identity matter more than “the” name.

- `get(name)` is **last-wins** when names collide.
- `extract_all(members=["x"])` matches **every** member named `x`; pass an
  `ArchiveMember` when you mean one identity.
- Hardlink targets resolve to an **earlier** same-named member by `member_id`, not
  to “whichever `get` would return.”
- Members with `is_current=False` (for example RAR version history) stay visible in
  listings but are skipped on extract by default.

| Need to know | Detail |
| --- | --- |
| Safe ≠ unlimited | Traversal, symlink escapes, and bombs are blocked; huge/hostile archives can still raise `ResourceLimitError` unless you raise limits. |
| STRICT rewrites some names | Trailing dots/spaces stripped; non-UTF-8 bytes percent-escaped. Disk path may differ from `member.name` — see `EXTRACTION_NAME_SANITIZED` / `requested_path`. |
| Collisions are first-class | Under `STRICT`/`STANDARD`, `README`/`readme` (and NFC/NFD twins) collide on **all** platforms. `OverwritePolicy` applies; `REPLACE` is not a silent merge — a collision diagnostic fires. Use `OverwritePolicy.RENAME` (`photo (1).jpg`) for intentional duplicates. |
| Reserved names / `:` | Rejected under `STRICT`/`STANDARD` on every platform (`CON`, `NUL`, `file:ads`, …). |
| `OnError.CONTINUE` ≠ ignore bombs | Per-member failures can continue; global bomb and listing guards still stop. |
| `OnError.STOP` is failures-only | Policy blocks are always recorded and continued; inspect the report (or exit `3` on the CLI) for `BLOCKED`. Abort-on-unsafe is a separate future opt-in. |
| `TRUSTED` still won’t traverse | Ownership / sticky bits only when allowed; path safety stays on. |
| Hardlinks + filters | Excluding a hardlink’s source can orphan the link (especially on streaming sources); `OnError` decides fail vs continue. |
| Symlink-hostile filesystems | Unlike `tarfile`, archivey does **not** copy target bytes through a symlink; you get a typed failure or skip. |
| Staging leftovers | `.archivey-tmp-*` under the destination are safe to delete (left only after hard kill / power loss). |
| Nested archives | Recursion is caller-driven; a zip-quine loops only if you loop. Bound depth/size yourself. |
| Listing vs extract limits | Bomb guards apply during **extraction**. `ListingLimits` apply when materializing `members()`; `stream_members()` is intentionally unguarded. |

## Limits

Defaults (via `ExtractionLimits` / `ListingLimits` on `ArchiveyConfig`) cap:

- **Extraction bombs** — total extracted bytes, compression ratio, and entry count
  (`ExtractionLimits`). Trips raise `ResourceLimitError`.
- **Listing materialization** — member count and retained metadata bytes
  (`ListingLimits`) on `members()` / `scan_members()` / extract-prep materialization.
  Trips raise `ResourceLimitError`. `stream_members()` stays unguarded by design.

Loosen per call with `limits=` (extraction only), raise `listing_limits` at
`open_archive(config=…)`, or use `ExtractionLimits.UNLIMITED` /
`ListingLimits.UNLIMITED` for trusted inputs you control.

Bomb guards apply during **extraction**. Listing caps apply when a full member list is
materialized — prefer `stream_members()` for huge untrusted archives when you only need
a sequential subset.

**The bomb tracker is per-archive, not nesting-aware.** It measures the expansion of
the archive it is extracting, so a zip-of-zips can amplify past your budget one level
at a time. Recursion into nested archives is caller-driven: if you open extracted
members as archives, bound the depth and the cumulative size yourself.

## Hardening notes for callers

**Optional `[seekable]` accelerators** (`rapidgzip` and its bundled bzip2
decoder) are a performance path, not part of the defended fuzz surface. Third-
party C++ can busy-loop on crafted input in a way Python timeouts cannot cleanly
interrupt. Callers processing untrusted archives under a hard latency budget
should leave accelerators off (`AcceleratorMode.OFF`) or enforce their own
resource limits. Mutation and Atheris harnesses run with accelerators off for
this reason.

**External tools:** RAR member *data* may be decompressed by the system `unrar`
binary. Keep that tool updated; treat its availability and behaviour as part of
your deployment’s trust boundary.

Prefer extracting untrusted archives into a dedicated directory with limited
permissions, then validating results before promoting them elsewhere.

## Diagnostics

Every block and every name rewrite is recorded on the returned `ExtractionReport`, not
only in logs — see [Errors and diagnostics](errors-and-diagnostics.md).
