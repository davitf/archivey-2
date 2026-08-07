# Design — `strict_archive_eof` trailing bytes

## Decisions

### "Nothing but zeros", not "EOF immediately"

The obvious strong rule — the file ends at the trailer — is wrong on the common case.
`tar` pads its output to a whole record (10 KiB by default, `-b20`), and many writers pad
further. Requiring immediate EOF would reject the majority of real tars under a flag
whose whole point is to be usable.

Zeros are also unambiguous: a run of zeros after the trailer carries no information the
reader could have listed, so ignoring it loses nothing. A non-zero byte does carry
something, and the reader did not list it.

### It raises `CorruptionError`, not `TruncatedError`

Nothing is truncated — the file is *longer* than the listing accounts for. The adjacent
case in the same method (a non-null block where the second trailer belongs, which is
trailing junk after a lone zero block) already raises `CorruptionError`, and these two
should not disagree about the same shape of evidence.

### Strict-only, and that is a cost decision, not a taste one

A batch indexer would genuinely like to know about trailing junk without opting into a
raise. It does not get an unconditional advisory because the check is O(tail length) —
and on a `.tar.gz` the tail must be *decompressed* to be inspected. Paying that on every
archive to produce an advisory most callers never read is the wrong trade; paying it when
the caller explicitly asked for a provably complete listing is the right one.

That asymmetry is worth stating because it looks like an inconsistency with
`EMPTY_ARCHIVE`, which *is* unconditional. The difference is that emptiness is free to
observe and this is not.

### Where the check sits

`_verify_tar_eof` already runs once at the end of the member scan, holds the handle
guard, and owns the two-block trailer decision. The trailing-bytes scan is the natural
continuation of its success path: it only runs when the trailer was found complete, so it
never competes with the existing `absent`/`short`/`nonzero` classifications.

Reading to EOF there is safe in both modes. Random access has finished `getmembers()` and
member reads seek independently of this position; streaming has finished its forward pass.

### The ISO case: the review's prediction was wrong, and the real answer is better

O8b listed "the ISO case still passes" as a deliberate consequence, reasoning that an
ISO's 32 KiB system area is zeros and therefore reads as a valid empty TAR with padding.
Measured on the corpus ISO, that only describes the first 32768 bytes:

```
iso size: 81920
first non-zero byte at offset: 32768  b'\x01CD001\x01\x00'
```

The zeros stop exactly where the volume descriptors begin, and 48 KiB of real filesystem
data follows. So `open_archive(iso, format=TAR, strict_archive_eof=True)` **raises** —
the file is not zeros to EOF.

Nothing about the rule needs changing; the review simply looked at the system area and
not at the rest of the file. The outcome is the one a caller would want: someone who
asserted `format=TAR` *and* asked for a provably complete listing gets told, loudly, that
the file is not a TAR. The default path is untouched, and there
`EMPTY_ARCHIVE` + `EXPLICIT_FORMAT_LISTED_EMPTY` remain the signal — which matters,
because those also cover the extension-fallback layer that this flag never reaches.

## Rejected alternatives

**Raise on any trailing data, zeros included.** Rejected on the padding measurement above:
it would reject what `tar(1)` itself writes.

**Emit an advisory in non-strict mode too.** Rejected on cost — see above. If a cheap
version ever exists (a seekable source could check the *last* block and the file length
without reading the middle, catching junk-at-the-end but not junk-in-the-middle), it
would be a separate, weaker signal and should not share this code.

**Scope it to the last block only** (cheap: seek to EOF, read one block). Rejected: it
catches `archive + junk` but not `archive + junk + zeros`, which is exactly what a
concatenation or a sloppy writer produces. A guarantee with a hole that shape is worse
than no guarantee, because callers would trust it.
