# rapidgzip-truncation-investigation — pin down gzip truncation handling

**Status:** §2 locked; §3 **implemented**. Ready to archive when maintainer agrees.

**Shipped stack:** empty→stdlib fallback on zero-byte rapidgzip EOF **plus** single-member ISIZE backstop on path sources. Multi-member ISIZE sum deferred. Soft EOF documented in `docs/internal/rapidgzip-upstream-report.md`. End-user notes in `docs/gotchas.md` / `docs/formats.md`: bare `.gz` / `open_stream` detection is **best-effort** (use `use_rapidgzip=OFF` for certainty); ZIP/7z members rely on container CRC. Follow-up parked: dedicated check that **corrupted** (and truncated) container *member* payloads still fail under `use_rapidgzip=ON` — in-member corruption is the sneaky case; whole-archive ZIP truncation usually fails at the central directory (`docs/internal/open-issues.md`).
