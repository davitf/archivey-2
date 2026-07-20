<!--
The "coffee brief": a spoken-word-friendly summary of this change, readable (or
read aloud) in under a minute. Prose only — NO tables, NO code blocks, minimal
symbols — so text-to-speech reads cleanly. Aim for ~200–280 words. Derive it from
proposal.md / design.md / tasks.md; do not introduce new decisions here.
-->

# gzip-zlib-truncation-recovery — Recover truncated gzip prefixes via zlib DecompressorStream

**Status:** Ready to implement. Depends on nothing. Blocks a cleaner rapidgzip empty-to-stdlib fallback once that lands, because large reads become safe. Not breaking. Effort: medium.

**Why it matters:** Truncated gzip must not look like a clean short success, and callers should still see every byte the decoder already recovered. CPython GzipFile drops that prefix on a large read, and today’s DecompressorStream can raise TruncatedError while the prefix sits unused in an internal buffer. That undercuts honest damaged-input handling.

**What it does:** Fixes DecompressorStream so incomplete end-of-file delivers the recoverable prefix first, then raises TruncatedError on the next empty read or on close. Moves the stdlib gzip path off GzipFile onto a gzip-window zlib decoder on that same engine, including concatenated multi-member files.

**Decided:** Use the existing pending-error pattern, not a hard raise that drops bytes. read-all returns the prefix and leaves the error pending. Gzip gets a dedicated decoder that chains members through unused data; raw deflate and zlib keep the shared zlib decoder and inherit the stream fix. Do not retarget the rapidgzip backstop in this change unless that code is already present and trivial.

**Your call later:** None — the design is settled. Optional later audit of bzip2 for the same oversize-read trap is out of scope.

**Bottom line:** A focused codec-engine fix plus gzip backend swap; implement after or beside the rapidgzip truncation work, not inside that investigation’s docs dump.
