<!--
The "coffee brief": a spoken-word-friendly summary of this change, readable (or
read aloud) in under a minute. Prose only — NO tables, NO code blocks, minimal
symbols — so text-to-speech reads cleanly. Aim for ~200–280 words. Derive it from
proposal.md / design.md / tasks.md; do not introduce new decisions here.
-->

# surface-stored-stream-digests — multi-member lzip CRC32 without decompressing

**Status:** Implemented (zlib Adler peek deliberately omitted). Depends on the
api-coherence hashes typing fix shipping HashAlgorithm with byte values first.
Blocks nothing. Not breaking once that typing landed. Effort: medium.

**Why it matters:** VISION wants hashes without decompression for cheap dedupe.
Lzip’s index already walks every member’s CRC and size, then previously threw the
CRC away or only surfaced the single-member case, so multi-member lzip stayed blank
for no good reason. Zlib’s Adler-32 trailer cannot be surfaced honestly the same
way: the wrapper has no size fields, so a last-four-byte peek lies under concat or
trailing junk. Adler stays decompressor-checked on read.

**What it does:** For lzip, keeps per-member CRCs from the backward index and
combines them into one whole-member CRC32 that matches hashing the concatenated
uncompressed payloads. Teaches the verify path to compute Adler-32 when an expected
digest is installed. Updates specs, formats docs, and the corpus digest matrix.
Does not put zlib Adler-32 on member hashes.

**Decided:** Pure-Python crc32 and adler32 combine helpers because CPython only
adds those in three point fifteen. Lzip always surfaces a combined CRC when the
index exists. Zlib omit on hashes. Gzip and xz multi-unit combine stay deferred.
Derived combined digests are documented as derived, not as a single on-disk field.

**Your call later:** None — the design is settled.

**Bottom line:** Additive lzip parity after the hashes type cleanup; zlib stays
out of the cheap-digest matrix on purpose.
