## Context

`archive-data-model` already defines `hashes: Mapping[str, int | bytes]` with real
algorithm keys (`"crc32"` int, `"blake2sp"` bytes) and allows completing hashes in place
during streaming. Backends currently populate: 7z `crc32`, ZIP `crc32` (central
directory, FILE/SYMLINK only), RAR5 `crc32`/`blake2sp`. `format-single-file-compressors`
§89–100 handles decompressed *size* from trailers (gzip ISIZE untrusted; lzip trailer via
the seekable backend) but never surfaces the trailer *CRC*. gzip's 8-byte trailer is
`CRC32 || ISIZE`; lzip's trailer carries a CRC-32 of the decompressed member. Both are
cheaply readable from a seekable/path source.

## Goals / Non-Goals

**Goals:**
- Surface gzip/lzip stored CRC-32 as `member.hashes["crc32"]` without decompressing.
- A documented, regression-gated stored-digest parity matrix across all read backends.

**Non-Goals:**
- Computing digests (that is `VerifyingStream`'s job on read; here we only surface the
  *stored* value cheaply).
- Changing the gzip ISIZE / size handling (size stays untrusted per existing spec).
- A new digest algorithm (BLAKE2sp verification is a separate change).
- bzip2/xz/zstd/`.Z` stored-digest surfacing (bzip2 stores a per-block CRC not a
  whole-stream one; xz stores a check but under its own model — out of scope, documented
  as "no cheap whole-member stored digest").

## Key decisions

- **Cheap-only, source-gated.** Surface the CRC only when the source is seekable/path
  (peek the trailer) — never force a decompression pass. On a non-seekable source, omit
  `crc32` (dedupe callers fall back to computing it while reading). This mirrors how size
  is already conditioned on the seekable lzip backend.
- **Single-member gzip only.** A concatenated multi-member gzip's final trailer CRC covers
  only the last member; a single `Member` cannot honestly carry it. Reuse the existing
  member-count detection from the truncation backstop: surface `crc32` iff exactly one
  member, else omit. Document the caveat.
- **Provenance recommendation, not a new field.** Rather than add a stored-vs-computed
  provenance field now, document the recipe: `member.hashes` values are *stored* digests
  (cheap, may be absent); `VerifyingStream` computes on read. A helper that returns "best
  available digest + provenance" is noted as a possible later ergonomic add (`IDEAS.md`),
  not built here.
- **Parity as a sweep invariant.** The conformance sweep gains a per-format expectation:
  for each applicable member the documented stored digest(s) are present, and absent where
  the format stores none — turning "backend X quietly stopped populating crc32" into a
  test failure.

## Open questions (resolve during apply)

- Whether to surface lzip CRC only through the seekable backend (consistent with size) or
  also via a cheap trailer peek when the accelerator is absent — decide with the same
  seekable-source gate used for size.
