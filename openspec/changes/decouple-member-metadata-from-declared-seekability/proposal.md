# Decouple member metadata from declared seekability

## Why

`seekable_members=True` is a **stream capability declaration** — "I intend to `seek()`
inside member streams". Today it also decides what **metadata** you get back:

```python
open_archive("x.lz").members()[0].size                        # None
open_archive("x.lz", seekable_members=True).members()[0].size # 44
open_archive("x.lz").members()[0].hashes                      # {}
open_archive("x.lz", seekable_members=True).members()[0].hashes  # {CRC32: …}
open_archive("x.xz").members()[0].size                        # None → 44 with the flag
```

Same for a `Path` and a `BytesIO`. This is the simplicity & consistency review's
headline finding (F1). Three things are wrong with it:

1. **It is not what the flag says.** The size comes from the xz stream index and the
   lzip trailer — a bounded backward peek over a source that is *already* seekable.
   Nothing about reading it needs the caller to want `seek()`.
2. **It is internally inconsistent.** gzip already surfaces its trailer CRC-32 from a
   bounded peek regardless of the flag. xz and lzip are the outliers, not gzip.
3. **It costs the founding use case its cheap answer.** `VISION.md` names "hashes
   without decompression" as a priority for the dedupe/indexing caller, who does a plain
   `open_archive` and never wants to seek — and therefore misses the lzip CRC-32 that is
   sitting in the trailer.

The specification already reads the way the fix does for XZ ("Header size when encoder
wrote it", unconditioned) — so the code is wrong there in the *other* direction, and the
LZIP row's "through the seekable lzip backend" is the clause that has to move.

**This also closes O3/Q16 in the sense that mattered.** The review's verdict was that the
problem with `seekable_members` was never its name; it was this. After this change the
flag means only what it says.

## What Changes

- Cheap trailer/index metadata (xz stream index, lzip trailer walk) is harvested at open
  from **any seekable source**, regardless of `seekable_members`. xz and lzip converge on
  the behaviour gzip already has.
- A non-seekable source is unaffected: it still reports `size=None` and no digests, and
  no probe forces a decode pass. The gate moves from *the caller's declaration* to *the
  source's shape*, which is where it always belonged.
- **`format-single-file-compressors`** — the LZIP size row and the lzip/xz digest rules
  drop "through the seekable lzip backend" / the declared-capability wording and state
  the source-shape rule instead; the XZ row, already unconditioned, becomes true.

Not changing: `seekable_members` itself, its name, its placement, or what it does to
member streams (index construction for *seeking*, accelerator `AUTO` resolution). Those
are settled by O3.

## Impact

- Specs: `format-single-file-compressors`.
- Code: `src/archivey/internal/backends/single_file_reader.py` (a metadata-probe config
  separate from the member-stream config).
- Tests: three red halves in `tests/test_review_simplicity_consistency.py` flip, and the
  pin that recorded the divergence is rewritten.
- Caller-visible: `member.size` and `member.hashes` gain values on a default
  `open_archive` of `.xz` / `.lz`. Nothing loses a value.
