# Questions for the maintainer

Decisions this review cannot make unilaterally (per the pause-and-ask rule).

> **Status (2026-07-18):** Q3, Q5, Q6 **resolved** (#136 / #139). Q1 has a
> **maintainer direction** (#140) — listing peers + ZIP/TAR model-build fast paths
> landed (this change); ZIP open+list still above the 2–3× band. **Still need a
> decision:** Q2 (wall enforcement), Q4 (verify-skip knob; perf case ~nil
> post-#137). See `../STATUS.md`.

## Q1 — What does "≤1.3× on common paths" cover, exactly? — DIRECTION RECORDED

VISION's sentence names "open/list/read/extract on ZIP and TAR". Measured today
(`budget-table.md`): ZIP read 2.2–2.3×, ZIP extract 2.4–3.7×, ZIP open+list 5–8×,
TAR 1.4–1.8×. Is the intended reading:

- (a) each op independently ≤1.3× (current numbers blow it badly on ZIP), or
- (b) decompression-dominated workloads ≤1.3× (gzip/tar.bz2 pass; ZIP still
  fails), with metadata ops (open/list) budgeted in absolute terms instead
  (0.3 ms/archive), or
- (c) something to re-scope in VISION/docs/costs before 0.2.0 so users who
  benchmark (`philosophy.md` invites them to) see an honest claim?

The answer decides whether P2 is "fix H2/H3 until green" or "fix + re-word".

**Maintainer direction (2026-07-18) — metadata ops get *ratio* budgets vs the
relevant peer, not absolute numbers:**

- **ZIP/TAR listing** (we wrap stdlib internally): at most **2–3× per member**
  vs `zipfile`/`tarfile`.
- **7z/RAR listing** (our own native parsers): **on par with `py7zr`/`rarfile`**
  — the peers are already test oracles, so benchmark-only optional deps.

Consequences to implement:

1. ~~Harness: add `open_list` ratio cases with those peers and bands~~ **done** —
   `zipfile`/`tarfile` peers + `py7zr`/`rarfile` listing peers in the harness;
   report labels use Q1 bands (ZIP/TAR ≤3×, native ≈1.25×). Still asserted the
   same way the wall budget ends up asserted (Q2 — informational only today).
2. ZIP open+list: measured **~3.7–4×** after #143 model-build + L2
   (`slots=True` / trimmed kwargs) — still above 2–3×. Remaining cost is
   `_to_member` derivation (~3.3 µs/member) + registration (~1.3 µs); further
   gains need L5 lazy derivation (OpenSpec). 7z open+list after L1 bulk name
   decode: probe **~2.0×** / harness ~**2.2×** vs py7zr (was ~3.4×); still
   above the 1.25× native band. RAR harness ratio remains a small-fixture
   artifact until a larger listing fixture exists (L3).
3. Read/extract regimes stay as previously framed: decompression-dominated
   ≤1.3× (met for large-member ZIP after #139), extract inside the ~2× safety
   band (met at realistic scale), many-small read_all follows the listing
   story (its cost *is* per-member machinery).

## Q2 — Where should the wall budget be *enforced*? — OPEN

Today: nowhere (`gate-efficacy.md` G1) — deliberate, because shared-runner ratios
flake. Options, not mutually exclusive:

- (a) nightly compares against the *previous nightly's* committed JSON (ratio
  drift > X% fails) — catches regressions without absolute-ratio flake;
- (b) nightly enforces the 2× safety band (not 1.3×) on the read_all cases only,
  where interleaved medians were stable in practice;
- (c) keep wall informational, but add the missing stdlib peers (open+list,
  extract) so the report at least *shows* every budgeted op (G7);
- (d) accept unenforced and say so in VISION.

Recommendation: (a)+(c); (b) once ZIP is back inside the band.

## Q3 — Tighten `SOLID_DECODE_FACTOR` from 2.0? — RESOLVED

A clean "decode every folder exactly twice" regression passes today (G3), which
contradicts VISION's own sentence. The harness only runs generated, controlled
fixtures, and the unit test already holds ×1.1. Any objection to ~1.25 in the
harness (and making the bound strict `>=`)? If the ×2 slack exists for a known
future corpus, that corpus isn't in the tree.

**Resolved in the follow-up investigation PR:** `SOLID_DECODE_FACTOR = 1.25`
(failure when `bytes > unpacked * 1.25`). `repro.py` probe 2 now CAUGHT. Also
added `NONSOLID_DECODE_FACTOR = 1.1` (G4) and ci-scale solid-random
baseline×1.5 (Q6/G5).

## Q4 — Should container-digest verification be skippable? — OPEN (lean leave-as-is)

`VerifyingStream` wraps every ZIP/7z/RAR member read; the digest itself is cheap
(CRC32/C) but the wrapper layer costs on hot paths, and some callers (e.g. a
sweep that hashes contents itself) get no value from it. `zipfile` can't skip
either, so parity is defensible. Options: a config knob
(`verify_member_digests: bool`), or leave as-is and rely on H2 making the wrapper
cheap. Leaning: leave semantics alone, fix H2 — but this is an API-design call
(overlaps the api-coherence review).

*Post-#137 evidence:* the wrapper layer is gone (verify is fused into
`ArchiveStream`) and the digest cost is measured at parity with `zipfile`'s own
CRC (~4 ms per 16 MiB pass on the review host) — the perf case for a skip knob
is now essentially zero. One nuance for whoever designs the knob anyway: the
fused verifier bounds `read(-1)` to the declared size, which *disables* the
`readall()` fast path (`residual-gap.md` §1) — worth keeping in mind so a
"verify off" mode doesn't accidentally have different chunking behaviour.

**Triage note:** unless someone wants the knob for API reasons, treat as
“leave as-is” and close when archiving this review.

## Q5 — H1 fix shape: lazy solid positioning vs extraction early-exit? — RESOLVED (#136)

Lazy positioning in `SevenZipReader._iter_with_data` honors the documented lazy
contract (`base_reader.py:435-439`) and fixes *all* selector cases, but moves the
skip-decode (and its errors: wrong password, truncation) from yield time to
first-read time of a later member — is that error-timing shift acceptable under
the stream_members contract? Extraction early-exit is contract-neutral but only
helps when the selection is exhausted early. I'd do both; flagging because the
error-timing question touches the `archive-reading` spec's scenarios.

## Q6 — Gating the random-solid case's absolute cost — RESOLVED

`sevenzip_solid_random` is recorded, not gated (G5). Bounding it to its committed
baseline ×1.5 would catch "the O(n²) got worse" (e.g. losing an incidental
cache) at zero flake risk (byte counts are deterministic). Any reason it was left
unbounded beyond "the absolute value is inherent"?

**Resolved in the follow-up investigation PR:** gated at baseline×1.5 when
`check_seek_baselines` is on (ci scale only — the committed baseline is ci-sized;
applying it to realistic corpora false-fails).
