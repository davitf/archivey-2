# cross-platform-name-safety — deterministic extraction names on every OS

**Status:** Exploration (spike) — one core decision is open, but most of it can proceed now. Depends on nothing. Under the strict policy it may start rejecting or rewriting names it used to write as-is, so it is a deliberate behavior change. Effort: medium to large. Also lands the rename overwrite policy.

**Why it matters:** extraction name handling is the sharpest cross-platform corner still open, and "safe by default" is a load-bearing claim. Four threat-model items share one root cause — a name that is fine on the archive's origin system but collides, mangles, or fails on the destination. Two names differing only by case or Unicode form are the same file on Windows and macOS, so a crafted archive can silently merge content there under a replace policy. Windows-reserved names like NUL, trailing dots and spaces, and colons all get silently mangled. And a name that is valid bytes but unrepresentable on the target filesystem succeeds on Linux and fails on macOS. Today all of this is platform-dependent — a surprise squared.

**What it does:** makes name handling deterministic on every platform, keyed off the existing strict, standard, and trusted extraction policies. It tracks a case-folded, normalized key so collisions are first-class events everywhere, rejects the dangerous Windows name shapes under strict, and adds a rename overwrite policy that extracts a colliding entry as "name (1)" — which the CLI's extract wants, so it should land before the CLI.

**Decided:** the collision-tracking and the strict rejections are settled and can be built now, independently of the open question below.

**Your call later:** the one real decision — for a name that cannot be represented on the destination filesystem, does strict reject it, or normalize it to a reversible, portable spelling? The recommendation is to normalize, so the backup-indexing use case keeps extracting everywhere, but the escape scheme needs one focused exploration pass before it is built, and once chosen it is hard to change.

**Bottom line:** the flagship safety change; start with the settled collision and rejection work, and settle the normalization scheme before it freezes.
