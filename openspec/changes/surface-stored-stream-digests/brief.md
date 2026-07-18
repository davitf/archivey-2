<!--
The "coffee brief": a spoken-word-friendly summary of this change, readable (or
read aloud) in under a minute. Prose only — NO tables, NO code blocks, minimal
symbols — so text-to-speech reads cleanly. Aim for ~200–280 words. Derive it from
proposal.md / design.md / tasks.md; do not introduce new decisions here.
-->

# surface-stored-stream-digests — fill zlib Adler-32 and multi-member lzip CRC32 without decompressing

**Status:** Ready to implement. Depends on the api-coherence hashes typing fix shipping HashAlgorithm with byte values first. Blocks nothing. Not breaking once that typing landed. Effort: medium.

**Why it matters:** VISION wants hashes without decompression for cheap dedupe. Docs still say zlib has no stored digest, but RFC 1950 puts Adler-32 at the end of every zlib stream. Lzip’s index already walks every member’s CRC and size, then throws the CRC away, so multi-member lzip stays blank for no good reason.

**What it does:** Peeks zlib’s trailer into hashes as Adler-32 on seekable single streams. For lzip, keeps per-member CRCs from the backward index and combines them into one whole-member CRC32 that matches hashing the concatenated uncompressed payloads. Teaches the verify path to compute Adler-32. Updates specs, formats docs, and the corpus digest matrix.

**Decided:** Pure-Python crc32 and adler32 combine helpers because CPython only adds those in three point fifteen. Lzip always surfaces a combined CRC when the index exists. Gzip and xz multi-unit combine stay deferred. Derived combined digests are documented as derived, not as a single on-disk field.

**Your call later:** None — the design is settled.

**Bottom line:** Additive parity after the hashes type cleanup; do the typing PR first, then this.
