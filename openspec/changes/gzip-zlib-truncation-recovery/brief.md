<!--
The "coffee brief": a spoken-word-friendly summary of this change, readable (or
read aloud) in under a minute. Prose only — NO tables, NO code blocks, minimal
symbols — so text-to-speech reads cleanly. Aim for ~200–280 words. Derive it from
proposal.md / design.md / tasks.md; do not introduce new decisions here.
-->

# gzip-zlib-truncation-recovery — Recover truncated gzip prefixes via zlib DecompressorStream

**Status:** Ready to implement. Depends on nothing. Blocks a cleaner rapidgzip empty-to-stdlib fallback once that lands, because large reads become safe. Not a package-default break for happy paths; truncated decode read-all still raises, and VerifyingStream raises CRC mismatches on the empty read after all bytes were delivered instead of on close. Effort: medium-plus.

**Why it matters:** Truncated gzip must not look like a clean short success, and bounded reads should still see every byte the decoder already recovered. CPython GzipFile drops that prefix on a large read, and today’s DecompressorStream can raise TruncatedError while the prefix sits unused in an internal buffer. Content faults should surface from read, never from close — decoder engines mostly already do that, but VerifyingStream still raises short and digest errors on close instead of on the read after all data.

**What it does:** Fixes DecompressorStream so incomplete end-of-file returns the recoverable prefix on bounded read, then raises TruncatedError on the next empty read. Read-all raises when the stream is incomplete. Close stays teardown-only for content errors. Size and seek-end must not treat a truncated prefix as a complete stream. Moves the stdlib gzip path off GzipFile onto a gzip-window zlib decoder with GzipFile-parity member chaining, including zero padding and trailing junk. Aligns VerifyingStream so CRC and digest mismatches raise CorruptionError on the empty read after every byte was delivered — never on close.

**Decided:** Use pending-error for incomplete inflate EOF. Never raise content TruncatedError or CorruptionError on close. Read-all raises rather than returning a prefix for truncated decode. VerifyingStream delivers all bytes then raises on the next read. Gzip gets a dedicated decoder with full member-boundary parity; raw deflate and zlib inherit the stream fix. Do not retarget the rapidgzip backstop here unless that code is already present and trivial.

**Your call later:** None — the design is settled. Optional later audit of bzip2 for the same oversize-read trap is out of scope.

**Bottom line:** Codec-engine truncate fix, gzip backend swap, size integrity, and verifier close alignment; implement after or beside the rapidgzip truncation work, not inside that investigation’s docs dump.
