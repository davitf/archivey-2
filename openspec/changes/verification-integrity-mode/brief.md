<!--
The "coffee brief": a spoken-word-friendly summary of this change, readable (or
read aloud) in under a minute. Prose only — NO tables, NO code blocks, minimal
symbols — so text-to-speech reads cleanly. Aim for ~200–280 words. Derive it from
proposal.md / design.md / tasks.md; do not introduce new decisions here.
-->

# verification-integrity-mode — A streaming default and a strict opt-in for content verification

**Status:** Design proposal, open questions on naming and where the small first half lands. Depends on the gzip truncation change. Effort: the default-consistency half is small; the strict mode is larger.

**Why it matters:** Content verification today is verify-as-you-go. If you read a member fully, you get a verdict; if you read part of it, or seek, or read then close, verification is quietly abandoned. That is a deliberate streaming choice, but it means integrity is not guaranteed for every access pattern. Two things are inconsistent. Encrypted members break the rule the strict way: WinZip AES drains and checks its authentication tag on close, raising a corruption error from close, verifying a partial read that a checksummed member would not. Checksummed members break it the lax way: there is no way to demand full verification, which is exactly what extracting an untrusted archive wants.

**What it does:** It makes verification a mode. The default, streaming, is uniform: a verdict only from the read that finishes the stream; partial reads, seeks, and close are quiet, and close never surfaces a first content fault, for checksums and encrypted members alike. The encrypted close-drain goes away, though a full read still authenticates. A new opt-in strict mode guarantees a verdict no matter how you read: it verifies the whole member before handing out untrusted bytes, forces a full pass around a seek, and completes verification on close, the same way for checksums and authentication tags. Strict can force a full decompress or decrypt in advance, so it knowingly breaks the performance budget, and that cost is documented.

**Assessment:** Not overkill. It fits the safe-by-default and honest-cost goals, and it turns the encrypted always-authenticate behavior into one uniform mode instead of a per-format surprise.

**Your call:** Names for the mode, whether the small default-consistency fix rides with the gzip change, and whether a strict seek verifies ahead or simply fails.
