# benchmark-gate — enforce the performance budget as a CI gate

**Status:** Ready to implement. Depends on nothing. The CLI should come after it. Not breaking. Effort: medium.

**Why it matters:** The vision promises staying within about 1.3 times stdlib speed on common paths, and says the real cost in practice is re-decompression and seek storms, not header parsing. None of that is enforced today. Worse, reading every member of a solid 7z archive can re-decode the folder from its start each time — quadratic work — and no test catches it. This is exactly the trap the vision says a benchmark must gate.

**What it does:** stands up a harness that measures three things per format and operation — wall time, bytes decompressed, and seek counts — and wires it into CI against committed baselines.

**Decided:** bytes and seeks are gated as exact structural invariants, since they are deterministic and host-independent, and they are what actually catches the quadratic trap; wall time is gated as a ratio against the stdlib peer with a tolerance band for noisy machines. The rule is that a sequential read of a solid archive decodes each byte at most once; out-of-order random opens may re-decode and are recorded but not failed. Baselines are committed, reviewed files, changed only by an explicit diff.

**Your call later:** the wall-time tolerance band on CI; whether the gate runs on every pull request or nightly; whether peak memory becomes a fourth axis now or later.

**Bottom line:** turns the central performance promise from prose into a gate — do this one first.
