# Theme 3 — Specs & docs vs. the code

> **Post-review status (PR #73):** S1 FIXED (ZIP crc32 populated + test). S2/S3 FIXED (directory is REQUIRES_SCANNING / _MEMBER_LIST_UPFRONT=False; spec + cost docstring updated). S4 was already correct.

The spec discipline here is real: `openspec/specs/` is dense and mostly matches the code. The
findings below are where they diverge or contradict each other. Two doc-only fixes I already
applied are logged in FIXES.md; the rest need a decision because the *spec* (not the code) is the
thing that's arguably wrong, or the two disagree.

## S1 — ZIP CRC32 is specced into `member.hashes` but never populated (VERIFIED, medium)

`openspec/specs/archive-data-model/spec.md:193`:

> | ZIP CRC32 and RAR5 Blake2sp hashes | Stored under `"crc32"` int and `"blake2sp"` bytes keys respectively |

The ZIP backend's `_to_member` (`zip_reader.py:472-488`) sets no `hashes=` at all — the field
defaults to `{}`. `info.CRC` is right there on the `ZipInfo` and is unused for the member record.
So `reader.get("x").hashes` is empty for every ZIP member, contradicting the spec.

This isn't cosmetic: it's the **founding use case**. `VISION.md:62` — "Hashes without
decompression where possible … a dedupe pass should be able to use `member.hashes` without
reading data." 7z already does this (`sevenzip_reader.py:669`, `hashes["crc32"] = record.crc32`);
ZIP, the most common format, is the gap. The one-line fix is
`hashes={"crc32": info.CRC}` in `_to_member`.

I did **not** apply it directly: populating a public data-model field is a behavior change (a
caller could branch on emptiness), and there's a downstream question — should ZIP member reads
then run through `VerifyingStream` like 7z does, or keep relying on zipfile's own CRC check? That's
a design call. See QUESTIONS Q3 (recommendation: apply the population, keep zipfile's CRC check).

## S2 — `get_members_if_available()` "without scanning" contradicts the directory backend (VERIFIED spec-vs-spec)

Two authoritative specs disagree, which is exactly the "pause and ask" case in CONTRIBUTING:

- `archive-reading/spec.md:181-182`: `get_members_if_available()` "returns the list only when
  available **without scanning** or reading member data, else `None`. Never scans or starts the
  [pass]."
- `format-directory/spec.md:31`: directory listing cost is `ListingCost.INDEXED` — and the
  directory backend sets `_MEMBER_LIST_UPFRONT = True`, so `get_members_if_available()` returns a
  list by doing a full recursive `os.scandir` walk (`directory_reader.py:85-165`), uncached, every
  call.

A recursive filesystem walk *is* a scan by the archive-reading spec's own definition. The
directory spec treats "filesystem directory listing" as free/indexed. Both can't be the mental
model. This surfaces the design question in QUESTIONS Q2. (It also has a free-threading race — see
concurrency.md C2 — and a re-walk-every-call cost that no caller would expect from a method sold
as a cheap peek.)

## S3 — `cost.py` `INDEXED` docstring is stronger than the directory backend honors (VERIFIED, low)

`cost.py:19`: `INDEXED` = "listing is **O(1) regardless of archive size**." The directory backend
reports `INDEXED` for an O(entries) walk. Either the docstring should soften to "an index/listing
is available without decompressing payload" (the honest common denominator across ZIP central
directory, 7z header, *and* a filesystem walk), or directory should report `REQUIRES_SCANNING`.
Tied to S2 — resolve together.

## S4 — TAR docstring vs. plain-tar listing cost (VERIFIED, informational)

`tar_reader.py:551` reports `ListingCost.REQUIRES_SCANNING` for plain tar and
`REQUIRES_DECOMPRESSION` for compressed — this is correct and honest, and is a nice contrast that
makes S3 look like the odd one out. No action; noting because it's the model the directory backend
*should* follow if S2/S3 land on "be honest".

## Doc-only fixes already applied (see FIXES.md)

- `open_stream` docstring said it "returns the decompressed bytes"; it returns an `ArchiveStream`.
- `ArchiveReader.open` docstring said a foreign-member open raises `ValueError`; it raises
  `ArchiveyUsageError` (confirmed by `test_reader_contract.py`).

## Things I checked that are consistent

- Link-following "no fixed depth limit, cycle detection" (`archive-reading`) matches
  `_open_with_link_follow`'s `visited`-set approach with no depth cap.
- `ArchiveMember` "deliberately unhashable" (`archive-data-model`) matches the `__hash__` override.
- The `error-handling` "never a catch-all; genuine OSError propagates" contract is honored across
  every backend translator I read (ZIP, TAR, ISO, 7z) — each maps a *closed set* of exception types
  and returns `None` otherwise.
- `packaging-and-extras` extras ↔ codec requirements line up with the `MissingComponent`
  declarations on the codec descriptors.
- The `PLAN.md` phase table and `openspec/project.md` are internally consistent about the
  native-7z-before-writing resequencing.
