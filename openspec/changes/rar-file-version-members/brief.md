# rar-file-version-members — expose WinRAR version history as members

**Status:** Ready to implement — the design decisions are all settled. Depends on nothing. Breaking to the listing contract. Effort: small to medium.

**Why it matters:** WinRAR's version-keeping mode stores prior revisions of a file as real payloads inside the archive. Archivey currently drops those rows, matching the rarfile library, which hides recoverable content. The philosophy prefers exposing format features as data. And because this changes what the member list contains, it is a breaking change — which is free to make now, before the first release, and costly to make after. That is the main reason to land it in this release even though it is niche.

**What it does:** surfaces the history revisions as members marked not-current, with WinRAR-shaped names like "path;2", while the live revision keeps the plain path and the current flag. Reading a history member returns that revision's bytes through unrar. Default extraction still skips non-current members, so safe defaults do not change — history is visible in listings but not written to disk unless asked.

**Decided:** present the name as "path;n" so it matches unrar and WinRAR tooling; for solid archives, pass the version flag to unrar only when the member list actually contains history, otherwise keep the plain single-pipe path; history rows count against listing limits like any other member, so a hostile version-bomb is still bounded; and no new extract flag is added.

**Your call later:** none of substance — optionally add a RAR3 fixture in addition to RAR5 if generating one stays cheap.

**Bottom line:** small, already fully designed, and worth landing now precisely because it is a breaking listing change.
