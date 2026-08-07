# `strict_archive_eof` asserts what it promises

## Why

`strict_archive_eof` is documented as the knob you set when you need **a provably
complete listing**. What it actually checks is one thing: that the second 512-byte null
trailer block is present. It never looks past it. Measured:

| Input | `strict=False` | `strict=True` |
|---|---|---|
| Valid tar, 1 member | 1 member | 1 member |
| Valid tar **+ 4 KiB of arbitrary junk** | 1 member, no diagnostic | **1 member, no diagnostic** |
| Valid tar + 4 KiB of trailing zeros | 1 member, no diagnostic | same |

So a caller who explicitly asked to be told when a listing might be incomplete gets
silence on a file that continues for another 4 KiB past everything the reader listed
(review F20). That is the gap between the definition and the promise.

## What Changes

> With `strict_archive_eof=True`, after the two-block null trailer **every remaining byte
> to EOF MUST be zero**. Any non-zero byte raises. With `strict_archive_eof=False`,
> behaviour is unchanged — including the cost.

Consequences, all deliberate:

- **Zero padding still passes.** Writers pad to 10 KiB routinely, and that is the
  overwhelmingly common case. This is why the rule is "nothing but zeros" rather than
  "EOF immediately".
- **An ISO read as TAR now fails under `strict`, and the review predicted otherwise.**
  O8b argued it would still pass because "an ISO's 32 KiB system area is zeros, so under
  this rule it is a valid empty TAR with padding". Measured, that is wrong: the zeros
  stop at 32768, which is exactly where the volume descriptors begin (`\x01CD001`), and
  the corpus ISO continues for another 48 KiB of real data. So the file is not zeros to
  EOF and it raises. That is the *better* outcome — it is the F7 case failing loudly for
  a caller who asked to be told — but it is a consequence of this change the review did
  not have, and the default path is unaffected: without the flag,
  `EMPTY_ARCHIVE` + `EXPLICIT_FORMAT_LISTED_EMPTY` (`review-diagnostics-batch`) remain
  what covers it.
- **Trailing junk now fails** — the point of the change.
- **Concatenated archives now fail under `strict`.** Accepted: they *are* multiple
  archives and the reader listed only the first, which is exactly what a caller asking
  for a provably complete listing should be told. The flag is opt-in and off by default,
  which is the argument for letting it mean the strong thing.

A new `ARCHIVE_TRAILING_DATA` diagnostic carries it, escalating to `CorruptionError` —
the same treatment the adjacent rejected-header case already gets, and consistent with
"the file is not what the listing claims".

**Cost, documented on the flag:** the check must read to EOF, so `strict_archive_eof`
goes from O(512 bytes) to O(tail length). On a non-seekable source, or a compressed tar
where the tail must be decompressed, that is a real scan. This is why it stays gated on
the flag rather than becoming an unconditional advisory: a batch caller would like to
know about trailing junk, but not at the price of reading every tail in the corpus.

## Impact

- Specs: `format-tar` (the rule), `diagnostics` (one code).
- Code: `src/archivey/internal/backends/tar_reader.py`, `src/archivey/config.py` (the
  docstring/cost note), `src/archivey/diagnostics.py`.
- Docs: `docs/gotchas.md`, `docs/formats.md`.
- **Sequencing:** the `diagnostics` delta pastes the post-`review-diagnostics-batch`
  taxonomy table, so archive that change first.
