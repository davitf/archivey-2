# Why the RAR corpus sweep ran nowhere — measured

**Status:** finished evidence. Answers F16 / Q11 / O6 of the simplicity & consistency
review. Run on Linux (Ubuntu noble, RAR 7.00 from multiverse, unrar 7.0.7), 2026-08-07.

## The question

The declarative corpus builds every entry in every format and asserts that each backend
opens, lists, reads and extracts it identically. Its RAR column runs on **no CI leg and
in no provisioned dev environment**, because building RAR fixtures needs the proprietary
RARLAB `rar` writer. CI installs `unrar` only, and on macOS it installs the bundle and
then deletes the writer, with this comment:

> the `rar` writer enables corpus RAR builds whose digest expectations are
> Linux-fixture-oriented; keep writer off the PATH here.

O6 decided to close the hole and left *how* open, between two routes: make the digest
expectations platform-independent (keeps the "no committed binaries" property, more
work), or commit a small pre-built fixture set (straightforward, against the design).
It also redirected the diagnosis away from payload digests toward **metadata** — mode
bits, uid/gid, mtime granularity.

## What was actually measured

`apt-get install rar`, then run the corpus sweep. **Four of the eight RAR entries
failed**, and every failure was in `_assert_stored_digest_parity`:

```
symlinks-rar         rar 'symlink_to_file1.txt' unexpected digests {CRC32}
hardlinks-rar        rar 'subdir/hardlink_to_file1.txt' unexpected digests {CRC32}
encrypted-rar        rar FILE 'secret.txt' missing stored digest
encrypted-mixed-rar  rar FILE 'secret.txt' missing stored digest
```

None of that is platform-dependent, and neither is anything else in the RAR path:

| Hypothesis | Status |
|---|---|
| Payload digests differ per platform | **Ruled out before this** — the corpus asserts `act.size == len(exp.contents)` and digest *key presence*, never digest values. |
| Mode bits (umask, the executable bit) | **Ruled out** — `_MODE_FORMATS` does not contain `rar`, so `member.mode` is never asserted for a RAR entry. |
| uid / gid | **Ruled out** — asserted only for `key.startswith("tar")`. |
| mtime granularity | **Ruled out** — `modified` is asserted only in the single-file (`gz-meta`) branch. |
| The digest-parity assertion itself | **Confirmed. This is the whole blocker.** |

So the writer was withheld for a reason that had already stopped being true, if it ever
was: the corpus makes no platform-dependent assertion about a RAR member.

## The two real bugs, both in the test's expectations

1. **Link members legitimately carry a CRC.** RAR stores a checksum of the *link
   payload* (the target string) for symlinks and hardlinks. The 7z arm of the same
   function already tolerates exactly this — *"SYMLINK may carry a CRC of the stored
   link payload; do not require or forbid"* — while the RAR arm forbade any digest on a
   non-FILE member.

2. **Encrypted RAR5 members deliberately have no plaintext digest.** With
   `RAR5_XENC_TWEAKED` set, the stored CRC32 and BLAKE2sp are key-tweaked MACs
   (`ConvertHashToMAC`). Comparing one to a plaintext digest would be wrong, so
   `rar_reader._member_hashes` keeps them out of `member.hashes` and verifies them by
   forward-transform once a password is available. The reader is right; the assertion
   demanded a digest the format does not expose.

Both are fixed in `tests/test_corpus_sweep.py`. With those two arms corrected and the
writer present, **the whole suite runs 2326 passed / 23 skipped, against 2284 / 65
without it — 42 tests that previously ran nowhere, all green.**

## What this leaves open

A **third route** that was not on O6's list, and is better than both of them: install the
writer on one CI leg. It needs no digest rework (the digests were never the problem) and
commits no binaries.

Two things stop this document from making that call:

1. **Licensing.** RARLAB `rar` is trialware; `unrar` is freeware. Installing `rar` in CI
   is common practice and is a maintainer decision, not a test-infrastructure one.
2. **macOS is still unmeasured.** Everything above is Linux. Nothing found here is
   plausibly platform-specific — none of the failing assertions touch a platform-varying
   field — but "no reason to expect a difference" is not a measurement. If the writer is
   enabled on Linux only, the claim to make is "the RAR column is exercised on Linux",
   not "the RAR column is exercised".

`tests/test_review_simplicity_consistency.py::test_rar_column_is_unmeasured_without_the_rar_writer`
documents the gap and skips when the writer is present, so it needs no change either way.
