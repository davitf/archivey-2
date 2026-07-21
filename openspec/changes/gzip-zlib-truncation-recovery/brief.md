<!--
The "coffee brief": a spoken-word-friendly summary of this change, readable (or
read aloud) in under a minute. Prose only — NO tables, NO code blocks, minimal
symbols — so text-to-speech reads cleanly. Aim for ~200–280 words. Derive it from
proposal.md / design.md / tasks.md; do not introduce new decisions here.
-->

# gzip-zlib-truncation-recovery — Recover truncated gzip prefixes via zlib DecompressorStream

**Status:** Ready to implement. Depends on nothing. Blocks a cleaner rapidgzip empty-to-stdlib fallback once that lands, because large reads become safe. Not a package-default break for happy paths; truncated or bad-CRC complete-stream reads still raise; chunked CRC still delivers every byte then raises on the next empty read. Effort: medium-plus.

**Why it matters:** Truncated gzip must not look like a clean short success, and bounded reads should still see every byte the decoder already recovered. CPython GzipFile drops that prefix on a large read, and today’s DecompressorStream can raise TruncatedError while the prefix sits unused in an internal buffer. Content faults should surface from read, never from close — and `data = stream.read()` must not quietly accept a bad CRC.

**What it does:** Fixes DecompressorStream so incomplete end-of-file returns the recoverable prefix on bounded read, then raises TruncatedError on the next empty read. Complete-stream read-all raises when the stream is incomplete or CRC-mismatched. Close stays teardown-only for content errors. Size and seek-end must not treat a truncated prefix as a complete stream. Moves the stdlib gzip path off GzipFile onto a gzip-window zlib decoder with GzipFile-parity member chaining, including zero padding and trailing junk. Aligns VerifyingStream: chunked reads deliver all bytes then raise on the next empty read; slurping read raises so read-then-close cannot miss the fault.

**Decided:** Use pending-error for incomplete inflate EOF, and make the decoder own that detection in its flush — so document that responsibility, and optionally rename flush. Never raise content truncation or CRC on close, for chunked and slurping alike: the terminal empty read raises, close never does. Gzip gets a dedicated decoder with full member-boundary parity; raw deflate and zlib inherit the stream fix. Keep the recoverable prefix reachable only through chunked reads: read-all raises and returns nothing, because a silent lossy success is worse than not salvaging — and document that trade-off. Do not retarget the rapidgzip backstop here.

**Your call later:** Two scope calls remain. First, whether to bring xz and lzip into this fix now — they still raise from flush and drop buffered output on a large truncating read, the same bug — or file a follow-up and note the shared-engine fix is incomplete until then. Second, whether truncation should become a subclass of corruption library-wide, so an uncertain short-with-hash body can raise the more general error; that touches the whole exception tree, so decide it on its own.

**Bottom line:** Codec-engine truncate fix, gzip backend swap, size integrity, and verifier close alignment; implement after or beside the rapidgzip truncation work, not inside that investigation’s docs dump.
