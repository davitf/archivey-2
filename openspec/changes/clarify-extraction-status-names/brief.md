<!--
The "coffee brief": a spoken-word-friendly summary of this change, readable (or
read aloud) in under a minute. Prose only — NO tables, NO code blocks, minimal
symbols — so text-to-speech reads cleanly. Aim for ~200–280 words. Derive it from
proposal.md / design.md / tasks.md; do not introduce new decisions here.
-->

# clarify-extraction-status-names — give every ExtractionStatus value a self-evident name

**Status:** Ready to implement once the api-coherence Q1 follow-up with the new SUPERSEDED status has merged. Breaking rename, no aliases, pre-one-point-zero. Effort: small.

**Why it matters:** The extraction status enum now has three different not-written outcomes, and the two older names stopped carrying their own reason. Skipped reads as skipped for some reason even though it now means exactly one thing, an existing file left in place because the overwrite policy said skip. Rejected does not tell you a safety or policy gate blocked the entry. A caller should be able to branch on the status without opening a docstring.

**What it does:** Renames Skipped to Not Overwritten and Rejected to Blocked. Blocked stays honest for both a hardwired path-safety block and a policy filter, so no by-policy suffix, because a zip-slip block is safety, not policy. The rename cascades to the paired diagnostic, so the code becomes Extraction Member Blocked and its status field becomes blocked, keeping one vocabulary across the result and its diagnostic. Extracted, Superseded, and Failed keep their names.

**Also fixes two drifts in the same area:** The spec claimed a filter returning None yields a skipped result, but the coordinator actually records no result at all, like a selector exclusion, so the spec is corrected. And the RAR file-version spec still said history rows extract as skipped, when they are now superseded.

**Your call later:** Whether the enum should become a string enum to match the spec text and the hash algorithm enum. Flagged, not bundled.

**Bottom line:** Honest, self-documenting status names, plus two contract corrections, stacked on the Q1 follow-up.
