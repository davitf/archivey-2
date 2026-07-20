# rapidgzip-truncation-investigation — pin down gzip truncation handling

**Status:** §2 **locked**; task 1.3 **done** (CI: Windows≡Linux wide silent set; macOS mostly raises, residual cut=10). §3 not started. **Pre-0.2.0 pay item**.

**Locked stack:** empty→stdlib fallback on zero-byte rapidgzip EOF **plus** single-member ISIZE backstop (close `<18`). Multi-member ISIZE sum **deferred** (forward `1f 8b 08` scan has false-header risk). No stderr / `tell_compressed` heuristics. Soft EOF documented in `docs/internal/rapidgzip-upstream-report.md` (not filed as bug). Keep `parallelization=0` (all cores).

**Next:** §3 implement.
