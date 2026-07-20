# rapidgzip-truncation-investigation — pin down gzip truncation handling

**Status:** §2 **locked** (2026-07-20). Linux + upstream research done; macOS/Windows CI probe added (task 1.3). §3 not started. **Pre-0.2.0 pay item** (debt-ledger Q4).

**Locked stack:** empty→stdlib fallback on zero-byte rapidgzip EOF **plus** single-member ISIZE backstop (close `<18`). Multi-member ISIZE sum **deferred**. No stderr parsing, no `tell_compressed` heuristic, no upstream issue filing (soft EOF is by design — see `docs/internal/rapidgzip-upstream-report.md`). Keep `parallelization=0` (all cores).

**Your call done.** Next: §3 implement after CI confirms macOS/Windows silent set, or in parallel if you prefer.
