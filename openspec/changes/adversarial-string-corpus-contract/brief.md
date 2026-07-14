# adversarial-string-corpus-contract — honest adversarial fixtures and name safety

**Status:** Complete — all nine tasks done; ready to archive. Depends on nothing. Not breaking. Effort: small.

**Why it matters:** the testing contract's Unicode-bomb coverage needs proof that each adversarial archive actually carries the bytes and flags it claims, and the older prose describing committed binary fixtures conflicts with the current generate-on-demand policy. Separately, a link target containing a null byte needs a defined, safe outcome before it ever reaches a filesystem path.

**What it does:** generates clean adversarial ZIP and TAR bases deterministically in memory with no committed binaries, mutates ZIP names, comments, and stored symlink data at their real fields while repairing checksums and checking the UTF-8 flags independently, requires exactly one warning when a member name contains a bidirectional formatting control regardless of backend, and rejects null-bearing link targets as a symlink-escape error before anything touches the path.

**Decided:** raw ZIP general-purpose flags stay internal rather than becoming public metadata; the bidi controls are warned about but not stripped out of names; and extraction evidence is scoped to returned paths and explicit escape candidates rather than claiming to detect arbitrary writes.

**Your call later:** essentially none — one task remains to close it out.

**Bottom line:** almost finished; it tightens the adversarial corpus and defines two name-safety fail-safes, and it neighbors the larger cross-platform name-safety change without overlapping it.
