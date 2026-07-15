# Accelerators on the hot path (F2)

Context: #105 put rapidgzip on **deflate/zlib/gzip** behind the AUTO size gate (#102).
Before that it backed gzip/bzip2 only. This review found that the move regressed
truncation handling on the commonest codecs.

## F2 (High) — rapidgzip silently swallows truncation that stdlib raises

### deflate / zlib: no backstop at all

`DeflateCodec.open` and `ZlibCodec.open` hand the source straight to the accelerator:

```
# codecs.py:944 (DeflateCodec) / :980 (ZlibCodec)
if _rapidgzip_enabled(config, available=_rapidgzip is not None):
    ...
    return _open_rapidgzip(source)          # raw _AcceleratorStream, no truncation check
```

Only `GzipCodec` wraps the accelerator in `_GzipTruncationCheckStream`
(`codecs.py:604`), and only for a path source. There is **no** truncation backstop on
the deflate/zlib accelerator path. rapidgzip decodes to the last fully-decodable block
and returns short output without raising, so a truncated stream is accepted as valid
partial data.

Measured (`repro.py` F2), a 2 MiB raw-deflate stream truncated to half:

```
rapidgzip: returned 999997 bytes, NO error (truncation SWALLOWED)
stdlib:    TruncatedError raised (this is the correct behaviour)
```

The exact byte count is block-boundary dependent — a truncation before the first fully
decodable block yields **0** bytes, one after it yields a partial prefix; either way, **no
error**. The stdlib `ZlibDecompressorStream` raises `TruncatedError` (via the base
`not self._decoder.finished` check, `decompressor_stream.py:279`) on the *same* input.
So whether truncation is a first-class error now depends on a size threshold and whether
the `[seekable]` extra is installed — a VISION #3 regression, and a
same-input-different-answer inconsistency across dependency configs.

**Mid-stream corruption (secondary, data-dependent).** The parallel review (PR #121)
additionally measured mid-stream *corruption* of a deflate/zlib body returned as a
truncated prefix with no error, where stdlib raised. In this session that split did **not**
reproduce across several single-byte flips: a corrupt raw-deflate stream frequently makes
*both* backends stop at a spurious clean EOF (raw deflate has no trailing integrity check),
so stdlib does not reliably raise either. Corruption is therefore reported as a
data-dependent observation, not a firm claim; the firm, repeatable F2 finding is
truncation. (rapidgzip has no integrity signal for a mid-stream body flip in raw deflate,
so where a real integrity gap exists it is downstream CRC — see below.)

### gzip: the ISIZE backstop is defeated by a false second member

`_GzipTruncationCheckStream._verify_not_truncated` (`codecs.py:347`) compares the
sequential byte total (mod 2³²) against the gzip ISIZE trailer, and — on a mismatch —
suppresses the error if `_has_additional_gzip_member` finds another gzip header:

```
# codecs.py:365
if self._has_additional_gzip_member():
    return                                  # treat as multi-member; do NOT raise
raise TruncatedError(...)
```

For a truncated file the last 4 bytes are mid-deflate garbage, so the ISIZE comparison
mismatches (correct so far) — but `_has_additional_gzip_member` then scans the truncated
compressed bytes for `1f 8b 08` (`gzip_has_additional_member`, `codecs.py:391`). Any
truncated payload larger than ~1 MiB of incompressible data almost certainly contains
that 3-byte sequence by chance, so the scan reports a spurious "second member" and the
backstop bails without raising. Measured (`repro.py` gzip variant), consistent across
runs:

```
path  trunc-half: 999996B complete=False NO error     # ISIZE backstop defeated
```

(A 1-byte truncation of the final deflate block *does* make rapidgzip itself raise
`Unexpected end of file` → `CorruptionError`, so the swallow is specific to truncations
that leave a clean block boundary — which is the common case for a mid-transfer cut.)

The >1 MiB payloads that reach rapidgzip via the AUTO gate
(`RAPIDGZIP_AUTO_MIN_COMPRESSED_SIZE = 1 MiB`, `config.py:73`) are exactly the ones most
likely to defeat the scan. And the backstop is applied only for a **path** source
(`GzipCodec.open`, `codecs.py:605-607`): a truncated gzip from a seekable **`BytesIO`**
source skips it entirely and swallows the truncation unconditionally (confirmed by both
reviews).

### Scope / mitigation

- **Reproduces only with `[seekable]` (rapidgzip) installed** and AUTO/ON selecting it
  (seekable + size ≥ 1 MiB). `[core-only]` uses the stdlib backend and is safe.
- A **ZIP** deflate member has a CRC32 + uncompressed size in the central directory,
  verified downstream by `verify.py`'s `VerifyingStream`, so an in-archive truncation is
  still caught there. The exposed surface is **standalone** `RAW_STREAM` deflate/zlib/gzip
  single-file streams (and any consumer reading a member stream without the verifying
  wrapper), where there is no independent length/CRC to check against.

### Fix direction (maintainer decision — see QUESTIONS Q2)

Either accept the gap explicitly (documenting that standalone accelerated
deflate/zlib/gzip does not surface truncation, and relying on downstream CRC for the
in-archive case), or give the deflate/zlib accelerator path the same class of backstop
the gzip path has — and harden the gzip one so a random `1f 8b 08` in compressed data
cannot mask a truncation (e.g. require the candidate second member to actually parse as
a gzip header at a 4-byte-plausible position, or verify against a known member count).

## Other Hunt-C items (checked, not findings)

- **Lifecycle / `weakref.finalize`.** `_AcceleratorStream.__init__` still attaches the
  finalizer at the object's birth site (`codecs.py:139`) with a staticmethod callback
  that holds only the raw inner (no `self` capture) — the GC-time close is intact. A
  million-member sweep that never explicitly closes streams would rely on GC to run
  the finalizers, but the accelerator is gated on seekable + ≥ 1 MiB, so a
  million-member all-≥1 MiB archive is > 1 TB; not a realistic leak vector. No change.
- **AUTO size-gate boundary.** The `input_size < min_size` test (`config.py:62`) is
  strict, so a member exactly at 1 MiB decodes through rapidgzip; both backends produce
  identical bytes for valid data (the only divergence is F2's truncation behaviour).
  No correctness cliff for valid input.
- **Free-threading.** Unchanged by this review; the accelerators remain single-live-stream
  and the GIL-only stance is not widened by #105 (the hot-path change is codec routing,
  not concurrency). Not re-derived here.
- **rapidgzip error-message tables** (`_translate_rapidgzip`, `codecs.py:259`) still map
  the pinned-floor (`rapidgzip>=0.16.0`) messages; the corpus-mutation-derived strings
  are matched against the installed 0.16.0 in `[all]`. One nit surfaced by F2: when a
  final-block truncation *is* caught, rapidgzip raises `RuntimeError("std::exception")`
  (verified — the informative "Unexpected end of file" text goes to stderr, not into the
  Python exception), so the `"End of file"/"Unexpected end of file"` arm at
  `codecs.py:284` never matches and the error falls through to the `std::exception` arm
  at `codecs.py:292` → **`CorruptionError`**, not `TruncatedError`. So even the
  truncations rapidgzip *does* surface are mislabeled as corruption — the accelerator's
  `TruncatedError` mapping is effectively dead. Minor next to the F2 swallow.
