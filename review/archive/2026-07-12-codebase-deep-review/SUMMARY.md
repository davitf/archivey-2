# Deep review — summary

Branch `claude/codebase-deep-review-656xfc`, HEAD `0f4254d`. Full-tree pass: every source module
read, tests/type-checkers/linter/coverage run. Baseline is green on every gate
(1348 passed / 70 skipped, pyrefly + ty clean, ruff clean, 87% coverage).

**Headline:** this is a genuinely well-built codebase. The concurrency model is disciplined and
mostly correct, the error-translation contract is followed with rare rigor, the codec layer is
cleanly factored, and the safety posture matches the marketing (no eval/pickle/shell; real
defense-in-depth in extraction). The findings below are the exceptions, and most are edge cases or
gaps rather than live bugs. Two doc fixes applied; four decisions requested in QUESTIONS.md.

## Post-review status (PR #73, round 2)

The maintainer approved the fixes on the PR and asked me to implement them. **Done** — all of the
top findings and the approved cleanups are now fixed with tests, across all three dependency configs
and both type checkers. See `FIXES.md` for the per-change table and the replies to the open
questions. Fixed: findings **1–4, 6, 7** below (and 9, 10) plus complexity X1–X5 and latent-bugs
D1–D2. Finding **5** (benchmark gate) and **8** (case-insensitive FS, threat-model O2) remain
roadmap/backlog items, as does the single-file trailer-CRC follow-up. The table's "Status" column
reflects this.

## Top 10 findings by impact

| # | Sev | Finding | Where | Status |
|---|-----|---------|-------|--------|
| 1 | High | Native 7z parser pre-allocates `num_files` objects with no bound → OOM on a crafted header (undercuts VISION's "memory-safe hostile-input parsing" in practice). Fuzzers miss it because it needs a valid-CRC crafted header. | latent-bugs L1, unknown-unknowns U1 | Q4 |
| 2 | Med | `BaseException` (Ctrl-C / MemoryError) during member materialization leaves the reader wedged: non-concurrent → misleading error, CONCURRENT → CV deadlock. | concurrency C1, latent-bugs L2 | Q1 |
| 3 | Med | ZIP `member.hashes` never populated despite the spec mandating `"crc32"` — breaks the *founding* dedupe use case for the commonest format; the datum is already on `ZipInfo`. | specs-docs S1, latent-bugs L3 | Q3 |
| 4 | Med | `get_members_if_available()` on a directory does a full uncached `os.scandir` walk every call (spec says "without scanning") and races `_uname/_gname` caches under free-threading. | concurrency C2/C3, specs-docs S2/S3 | Q2 |
| 5 | Med | No benchmark gate exists, so the solid-block O(n²) re-decode trap (random `open()` re-decodes the 7z folder from its start per member) is documented but unenforced — exactly what VISION's perf budget says must be gated. | roadmap | roadmap |
| 6 | Med | Test scenario gaps: no BaseException-mid-materialization test, no free-threaded `get_members_if_available` test, thin extraction fault-injection, no seek-math property test. | tests T1–T4 | writeup |
| 7 | Low-Med | Private stdlib dependency `lzma._decode_filter_properties`: if a future Python drops it, *every* LZMA 7z member silently reports CorruptionError instead of failing loud. | latent-bugs D1 | writeup |
| 8 | Low-Med | Case-insensitive / Unicode-normalizing FS: extraction bookkeeping keys by exact `Path`, so collisions on macOS/Windows mis-track overwrite/anti-item state. | unknown-unknowns U4 (threat-model O2) | writeup |
| 9 | Low | `cost.py` `INDEXED` docstring ("O(1) regardless of size") is stronger than the directory backend honors; the two specs disagree on whether a walk is an index. | specs-docs S3 | Q2 |
| 10 | Low | Duplication debt: handle-lock branch (×15), NTFS FILETIME conversion (×2), ZIP exception tuple (×3), hand-rolled ZIP STORED password loop. Each is a drift-out-of-sync hazard. | complexity X1–X4 | writeup |

Plus two doc bugs already fixed (FIXES.md): `open_stream` "returns bytes" and `open()` "raises
ValueError".

## Proposed breaking / behavior changes — cost vs. benefit

None of these is required for correctness today; they're the "no users, breaking changes on the
table" opportunities.

| Change | Benefit | Cost / risk | Verdict |
|--------|---------|-------------|---------|
| Populate ZIP `member.hashes["crc32"]` (Q3) | Turns on cheap dedupe for the commonest format — the founding use case. Spec already requires it. | Public data-model field goes from empty→populated; a caller branching on emptiness breaks (implausible). One test. | **Do it.** Additive, spec-mandated. |
| Directory → `REQUIRES_SCANNING` + `_MEMBER_LIST_UPFRONT=False` (Q2) | Honest cost signal; kills the free-threaded cache race; aligns with plain-tar. | `cost` value users may read changes; `get_members_if_available()` returns `None` where it returned a list. | **Recommend**, but it's the one that changes a value callers read — your call. |
| Bound 7z parser count fields (Q4) | Closes an OOM-on-hostile-input DoS; backs VISION claim #2. | Rejects (absurd) archives that today OOM. Needs the right bound + adversarial fixture. | **Do it before public release.** |
| `except BaseException` in materialization (Q1) | Reader survives Ctrl-C; no CV deadlock. | Touches a concurrency mechanism. Small. | **Do it.** |
| Lazy/gated `capture_open_site` (D2) | Cuts per-open cost + retained memory for the million-archive dedupe sweep. | Slightly worse `ConcurrentAccessError` breadcrumb unless the site is re-derived. | Nice-to-have; do with the benchmark work. |

## Where I disagree with a premise

- The prompt framed this as a hunt for problems. The honest headline is that **most of the codebase
  is in very good shape** — I flagged where it isn't, but "this part is fine" is the correct verdict
  for the stream base hierarchy, the codec table, the error-translation contract, the extraction
  safety layers, and the bulk of `ReaderState`. Don't let the finding list read as "it's shaky"; the
  ratio of solid-to-shaky is high.
- The single most valuable *product* change isn't a bug fix — it's promoting the **benchmark gate**
  and the **salvage read mode** (roadmap). The library's identity (perf budget, "damaged input is a
  first-class citizen") is currently aspirational in code; those two make it real.

## Reading order

`00-recon-map.md` (map + baseline) → `SUMMARY.md` (this) → `QUESTIONS.md` (decisions) →
theme files (`concurrency`, `complexity`, `specs-docs`, `tests`, `roadmap`, `unknown-unknowns`,
`latent-bugs`, `other`) → `FIXES.md` (what changed).
