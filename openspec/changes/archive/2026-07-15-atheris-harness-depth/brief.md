<!--
The "coffee brief": a spoken-word-friendly summary of this change, readable (or
read aloud) in under a minute. Prose only — NO tables, NO code blocks, minimal
symbols — so text-to-speech reads cleanly. Aim for ~200–280 words. Derive it from
proposal.md / design.md / tasks.md; do not introduce new decisions here.
-->

# atheris-harness-depth — Deepen Atheris: RAR open CI, ZIP member read, full stream set

**Status:** Ready to implement. Depends on nothing. Blocks nothing. Breaking? no. Effort: medium.

**Why it matters:** The coverage-guided fuzz gate already finds real parser bugs, but several high-value paths stay thin. RAR open was silently skipped in Atheris CI for lack of unrar. ZIP member reads now go through archivey’s codec and AES streams while the harness only listed names. Stream and codec bugs were hit only by accident via detect_format. Mutation fuzz still covers extract, but Atheris is smarter at deep decode paths.

**What it does:** Installs unrar in the Atheris workflow so RAR open actually runs. Deepens the ZIP target to mutate headers and content, fix up CRCs where feasible, and do bounded member open and read. Adds first-class stream targets for every archivey-owned standalone codec — unix-compress, xz, lzip, gzip, bzip2, lzma-alone, zlib, plus optional extras when installed — with hang timeouts. Grows the partitioned wall budget to fit; no short ceiling that drops stream slices.

**Decided:** Keep mutate-then-fixup in Python rather than custom mutators. Deepen ZIP in place with bounded read instead of full extract. Require the full standalone stream set now rather than unix-compress only. Mutation harness role unchanged. Exact second splits stay env-overridable.

**Your call later:** How aggressively to synthesize deflate CRCs versus accepting typed corruption on payload-only flips. Per-codec second splits can be tuned after a green longer run.

**Bottom line:** Medium harness-hardening change that closes known CI blind spots and aims Atheris at ZIP reads and the whole stream layer, accepting a longer job.
