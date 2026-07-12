# Deep pass 3 — Unknown unknowns, round two

Assumption per the brief: the first pass (`unknown-unknowns.md`, U1–U6) found the obvious
ones. This pass hunted in four places: stdlib *leniency* (not just stdlib errors), platform
divergence in timestamp/paths, private-API reliance beyond the one already fixed, and
guarantees we hold but don't exploit. Items W1–W9; corrections to the first review inline.
Threat-model items already registered (O1–O7) are acknowledged, not re-reported.

## Wrong (or unstated) assumptions

### W1 — "Corruption raises." For TAR, mid-archive corruption is a *silent clean EOF* (VERIFIED, repro)

The error-handling story assumes corrupt structure surfaces as `CorruptionError`. For TAR
that is false in the most common corruption position: **stdlib `tarfile.next()` treats an
`InvalidHeaderError` on any header after the first as end-of-archive** — no exception, the
iterator just stops (CPython `tarfile.py`, the `except InvalidHeaderError` arm re-raises
only `if self.offset == 0`). Reproduced against this tree:

- 6-member tar, second member's header checksum corrupted → iteration yields **1 member,
  no error**; `scan_members()` returns 1 member; the only signal is
  `ARCHIVE_EOF_MARKER_MISSING` at WARNING (default `strict_archive_eof=False`,
  `config.py:81`).
- 6-member `.tar.gz`, 64 bytes zeroed mid-deflate → decodes to garbage that parses as an
  invalid header → iteration yields **2 members, no error**, same warning-only signal.

So for the flagship "inventory a million archives" use case, a bit-rotted TAR produces a
*short listing that looks complete*. The EOF-marker check (`tar_reader.py:342-396`) is the
only backstop and it is (a) WARNING-severity by default and (b) semantically mislabeled for
this case — the diagnostic and the strict escalation both say *truncated*, but the archive
is corrupt-in-the-middle; a user investigating "truncated" will look at the wrong end of the
file. The `format-tar` spec's EOF matrix (spec.md:145-153) documents the missing-marker
case but nowhere states "a corrupt non-first header silently ends the listing" — that
behavior is currently an undocumented consequence of tarfile internals.

Directions (maintainer decision, QUESTIONS-worthy): (1) document the leniency in
`format-tar` + `docs/formats.md` explicitly; (2) consider defaulting `strict_archive_eof`
to True for *random-access* readers (a seekable source can verify cheaply; the lenient
default mainly serves pipes); (3) longer term, the native-reader strategy (7z/RAR) applied
to TAR's 512-byte headers would make this class of leniency archivey's own decision instead
of tarfile's.

### W2 — "Every `datetime.fromtimestamp` is guarded" (first review U3) — false at two sites, one attacker-reachable (VERIFIED code / platform-conditional)

`internal/timestamps.py:6-8` states the lesson precisely: "the out-of-range guard is the
load-bearing part … OSError on Windows … must degrade to None + a reported issue, never sink
the whole listing." Two sites in the tree don't follow it:

- **ZIP Extended Timestamp (0x5455), attacker-controlled** — `zip_reader.py:261-264`:
  `ts` is read as a **signed** 32-bit value and passed to
  `datetime.fromtimestamp(ts, tz=timezone.utc)` naked. On Windows, tz-aware
  `fromtimestamp` still routes through `gmtime()` (both the C and Python implementations),
  which fails with `OSError` for negative inputs. A ZIP whose UT field carries any pre-1970
  time — hostile, or a genuinely old file — makes `members()` raise a **raw, untranslated
  OSError** on Windows while working fine on Linux. That is exactly the
  hostile-input-crashes-the-listing class the same function's NTFS branch (via the shared
  guarded helper, `timestamps.py:44-59`) was hardened against, three lines above. Fix is
  mechanical: same try/except + `TimestampIssue` as its siblings. A CI job on Windows would
  have caught it only with a pre-1970 UT fixture — worth adding one.
- **Directory reader** — `directory_reader.py:198-207`: `st_mtime`/`st_atime`/`st_birthtime`
  converted unguarded. Not attacker-controlled in the archive sense, but network/FUSE
  filesystems do return garbage timestamps, and one bad file then sinks the whole walk on
  Windows (SUSPECTED severity, low).

**Correction to the first review:** U3's "the code correctly wraps *every*
`datetime.fromtimestamp`" was an over-claim; it checked the three backends it cites and
generalized. (Checked the remaining sites: `_pax_time` guarded, gzip header mtime is
unsigned-32 so its max is year 2106 — inside range on all platforms — and detection has no
timestamp path. These are fine.)

### W3 — `ZipInfo._raw_time`: the same hazard class as the lzma one, still silent (VERIFIED)

The first review's D1 (private `lzma._decode_filter_properties`) was fixed by binding at
import and failing loud (`sevenzip_reader.py:169-184`). The tree has a second private-stdlib
dependency with a *worse* failure mode that wasn't inventoried:
`zip_reader.py:514-520` reads `getattr(info, "_raw_time", 0)` to derive the ZipCrypto check
byte for data-descriptor members. If a future CPython renames `_raw_time`, the fallback `0`
makes the check byte `0x00` for every such member — so **every candidate password fails the
1-byte check and the library reports "Wrong password" for correct passwords**, silently,
only for flag-bit-3 ZipCrypto members. Apply the D1 recipe: resolve once at import (or
first use) and raise a loud "archivey needs updating" error when absent, instead of
defaulting. Related but lower risk, for the inventory: `ZipFile._lock`
(`zip_reader.py:522-524`, fails loud with AttributeError), the `_SharedFile`-locking audit
that `CloseLockedStream` rests on (`locked.py:71-78`, behavioral — silent if it changes;
already pinned-version-audited), and `ZipInfo.orig_filename` (undocumented but stable
attribute; loud if removed).

### W4 — Member identity is *positional*, and two enumerations must agree (VERIFIED reliance, currently sound)

Selection-by-member and extraction progress both key on `(archive_id, member_id)`
(`selection.py:24-37`), and `member_id` is a stamp of enumeration position. The extraction
coordinator gets its totals/selector list from `get_members_if_available()` →
`_get_members_index_only()` — which for ZIP and ISO builds **fresh member objects by
re-enumerating** — and then matches them against the members yielded by
`stream_members()`, which come from the *separately enumerated* materialized cache
(`extraction.py:259-287`). The whole scheme works only because `_iter_members()` yields in
identical order every call. True today for every backend that has `_MEMBER_LIST_UPFRONT`
(zipfile infolist order, 7z/single-file yield cached objects, pycdlib walk order), and the
one backend where it could genuinely break — a directory on a live filesystem — was moved
off this path by the C2/C3 fix. But nothing states the invariant; a future backend whose
enumeration order depends on anything mutable (or a cache-refresh feature) would corrupt
selection *silently* (wrong members extracted). Worth one sentence in the
`_iter_members` contract in `base_reader.py:153-156`: "must yield the same members in the
same order on every call."

Two adjacent behavioral quirks of the same path, worth knowing: every
`get_members_if_available()` call on ZIP/ISO re-runs `_to_member`, so (a) per-member
diagnostics (name-normalization, bad timestamps) are **re-emitted and re-counted** on every
call — `reader.diagnostics` counts inflate by calling a documented "cheap, safe" method —
and (b) the returned objects are distinct from the ones `members()` yields, so callers
comparing them by identity get surprises. Both disappear if the index-only peek caches (or
if it reuses the materialized cache when present — which it already prefers).

### W5 — `os.replace`-based atomicity leaves `.archivey-tmp-*` orphans on hard kill (VERIFIED, minor)

`_write_file_atomic` (`extraction.py:879-907`) cleans its temp on any Python-level failure
(`BaseException` arm), but SIGKILL/power-loss leaves `.archivey-tmp-<random>` files in the
destination — and nothing in the library or docs names the prefix as safe to sweep. Since
partial-extraction recovery is exactly when a user re-runs extraction into the same dest,
either export the prefix as a documented constant or note in `safe-extraction` docs that
`.archivey-tmp-*` files in the destination are archivey's and are safe to delete. (First
review U2 covered `os.replace` semantics themselves; this is the operational leftover.)

### W6 — Free-threading correctness rests on PEP 703 per-object atomicity in four places (cross-ref)

Inventoried in deep-concurrency.md ("R1/R5"): `SevenZipKeyCache._cache`,
`_folder_passwords`, double member-id stamping, and the unlocked snapshot fast path. Under
the GIL these are bytecode-atomicity assumptions; under 3.13t they are PEP 703
per-object-lock assumptions. Both are *real* guarantees today, but they are the kind that
silently stops holding on PyPy/GraalPy or if a cache becomes a two-step structure. One lock
each makes the assumption explicit; noted here because "which builds are we actually
promising?" is a packaging/docs question as much as a concurrency one — the README/docs
currently don't state a position on free-threaded support even though CI tests it.

## Guaranteed and unexploited

### W7 — `os.pread`: the OS already provides what `SharedSource`'s lock simulates (POSIX)

`SharedSource` views do lock→seek→read to fake positioned reads on one fd
(`slice.py:124-134`). POSIX guarantees `pread(2)` is atomic at a given offset with **no
shared file position mutation** — for a path-backed `SharedSource` (the common case: every
7z open, single-file path sources), each view could issue `os.pread(fd, n, abs_offset)`
with *no lock at all and one syscall instead of two*. This is strictly stronger than the
dormant `independent_handles` seam (`shared.py:19-22`): no extra fds, no per-view open
cost, true parallelism for the future parallel-extraction work. Windows keeps the locked
path (`os.pread` is POSIX-only). Contained change: `SharedSource` grows a
"positioned-read" strategy when `_owns_handle` and the handle has a real fd;
`SlicingStream` already isolates the re-seek policy behind `_seek_before_read`
(`slice.py:82-87`), which is exactly the seam to hang it on.

### W8 — Extraction copies allocate a fresh 1 MiB `bytes` per chunk

`_copy_to_fileobj` (`extraction.py:909-931`) is `read(1 MiB)` → `write()`, allocating and
discarding a megabyte `bytes` per iteration for the entire extraction volume. Every
`ArchiveStream` supports `readinto` (`archive_stream.py:255-266`), so a reusable
`bytearray`/`memoryview` loop halves allocator traffic on the hottest byte path in the
library, with the bomb-tracker `count()` unchanged. (First review E3 pointed at
`posix_fadvise`; this one is bigger and portable. Both belong with the benchmark-gate work
— measured, not assumed.)

### W9 — `zipfile.ZipFile` already accepts our `SharedSource` discipline for free

Noting the positive: the choice to let stdlib zipfile keep owning ZIP's shared-handle
serialization (rather than re-plumbing ZIP through `SharedSource`) exploits `_SharedFile`'s
per-read locking that CPython already maintains — one of the few places the tree leans on
an implementation detail *and* has the audit + pinned note + free-threaded stress test
(`test_multithread_zip_open_close_refcnt_stress`) to justify it. This is the pattern W3
should be brought up to.

## Acknowledged, already registered — not re-reported

- Windows reserved names / trailing dots+spaces (threat model **O3**), NTFS ADS via `:` in
  names (**O4**), case/Unicode collision bookkeeping (**O2**, first review U4), metadata
  bombs (**O1**, U1 — the 7z count bound is in and correct: `sevenzip_parser.py:578-592`;
  spot-checked the remaining loops: every unbounded count is consumed against the bounded,
  size-checked header buffer, so reads fail before allocation can run away), names
  unrepresentable on the target FS (**O7**, with the EILSEQ translation in
  `extraction.py:352-358` already landed).
- `os.replace` Windows sharing-violation semantics (U2), surrogateescape round-trip
  asymmetry (U5), GC/finalizer-vs-reader interleaving (U6 — now sharpened into
  deep-concurrency N5 and R4).

## Corrections to the first review

1. **U3 over-claimed** — "wraps every `datetime.fromtimestamp`" is false; see W2 (ZIP UT
   field is the attacker-reachable one).
2. **U1's "general lesson" was applied but framed too narrowly** — count fields were
   bounded, but the *silent-leniency* sibling (W1: tarfile swallowing corrupt headers) is
   the same "the parser trusts the container more than it should" class on the read path,
   and the first review's fuzzing/mutation framing (corruption ⇒ exception) missed the
   corruption-⇒-*silence* outcome entirely.
3. **D1's recipe (bind private stdlib API loud) was treated as complete after one instance**
   — the inventory in W3 shows at least one more silent-failure instance (`_raw_time`) that
   should have been swept in the same pass.
