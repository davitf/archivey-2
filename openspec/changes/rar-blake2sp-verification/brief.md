# rar-blake2sp-verification — actually verify RAR5 BLAKE2sp members

**Status:** Ready to implement. Depends on nothing. Behavior change (a corrupt member now raises instead of reading clean), but no API break. Effort: small to medium.

**Why it matters:** RAR5 members can carry a BLAKE2sp integrity hash instead of a CRC, and archivey already shows that hash on the member. But it never actually checks it: the standard library has no BLAKE2sp, so verification silently degrades to a "cannot verify" diagnostic — meaning a corrupted BLAKE2sp-only member reads back as clean today. The two specs even disagree: the RAR spec claims verification runs and raises on mismatch, while the streams spec treats BLAKE2sp as the canonical example of something that cannot be computed. With the flagship of this release being consistency and safety, that is a real integrity gap on the one format where the stronger hash is the only integrity signal.

**What it does:** implements BLAKE2sp — the eight-way parallel BLAKE2s tree hash — on top of the standard library's blake2s, so it stays zero-dependency, and wires it into the verification stage so those members are genuinely checked.

**Decided:** build it on blake2s tree parameters rather than adding a C dependency; prove it with published known-answer vectors before trusting it on RAR fixtures; then reconcile the streams spec so BLAKE2sp counts as computable and the RAR spec's claim becomes true.

**Your call later:** confirm RAR5 uses degree-eight, unkeyed, 32-byte output against a real fixture and unrar before finalizing the tree parameters.

**Bottom line:** closes a silent "reads corrupt data as clean" gap; small and self-contained.
