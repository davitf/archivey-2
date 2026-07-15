# cross-platform-name-safety — deterministic extraction names on every OS

**Status:** Ready to build — all policy decisions settled (recorded in `design.md` and ADR 0013). Depends on nothing. Under the strict and standard policies it may start rejecting or rewriting names it used to write as-is, so it is a deliberate behavior change. Effort: medium to large. Also lands the rename overwrite policy.

**Why it matters:** extraction name handling is the sharpest cross-platform corner still open, and "safe by default" is a load-bearing claim. Four threat-model items share one root cause — a name that is fine on the archive's origin system but collides, mangles, or fails on the destination. Two names differing only by case or Unicode form are the same file on Windows and macOS, so a crafted archive can silently merge content there under a replace policy. Windows-reserved names like NUL, trailing dots and spaces, and colons all get silently mangled. And a name that is valid bytes but unrepresentable on the target filesystem succeeds on Linux and fails on macOS. Today all of this is platform-dependent — a surprise squared.

**What it does:** makes name handling deterministic on every platform, keyed off the existing strict, standard, and trusted extraction policies. It tracks a case-folded, normalized key so collisions are first-class events everywhere, rejects the dangerous Windows name shapes under strict, and adds a rename overwrite policy that extracts a colliding entry as "name (1)" — which the CLI's extract wants, so it should land before the CLI.

**Decided:** collision-tracking runs under strict and standard (trusted defers to the local OS); strict rejects the dangerous Windows name shapes (standard rejects the reserved names and colons but tolerates trailing dots and spaces); and the rename overwrite policy inserts a counter before the extension (`photo (1).jpg`). A collision is recorded on the result as `requested_path` plus a diagnostic.

**The normalization call:** for a name that cannot be represented on the destination filesystem, strict and standard **normalize** it to a reversible, portable spelling — each non-UTF-8 byte percent-escaped (`caf\udce9.txt` → `caf%E9.txt`) — so the backup-indexing use case keeps extracting everywhere; trusted keeps faithful bytes. The scheme is the most standard one possible (percent-encoding of raw bytes) and is pinned in the spec so it stays stable once released.

**Bottom line:** the flagship safety change, now fully specced and ready to build.
