<!--
The "coffee brief": a spoken-word-friendly summary of this change, readable (or
read aloud) in under a minute. Prose only — NO tables, NO code blocks, minimal
symbols — so text-to-speech reads cleanly. Aim for ~200–280 words. Derive it from
proposal.md / design.md / tasks.md; do not introduce new decisions here.
-->

# extraction-progress-in-file — progress bars that move within a large file

**Status:** Small, ready to implement. Enables the nicer command-line progress bar but is a library change on its own. Not breaking for published users, since nothing is published yet. Effort: small.

**Why it matters:** Today the extraction progress callback fires once per member, after the member is fully written, and the byte count it reports is the running total for the whole operation. So when you extract one big file, the progress bar sits frozen and then jumps by the entire file size in a single step. There is no way to draw a bar that fills as a single large member is written.

**The key insight:** the number we need already exists. The decompression-bomb guard is fed every one-megabyte copy chunk, and it already keeps a running count of the current member's output, because the per-file ratio check needs it. That in-file position is measured on every chunk; it just never leaves the guard.

**What it does:** Expose that per-member byte count and add it to the progress payload as member-bytes-written. Emit the progress callback from inside the copy loop, not only at the file boundary, throttled naturally by the one-megabyte chunk — so about one update per megabyte, and still a single update for small files. The callback may now fire several times per file, and a final update always lands exactly at the file's size so a consumer can complete the bar. Directories and links keep their single update. When no callback is set, nothing extra happens.

**Your call later:** Whether one update per megabyte is the right cadence or wants a coarser floor for very large files.

**Bottom line:** Reuse the bomb guard's existing per-chunk count; surface it; extract gets real in-file progress bars.
