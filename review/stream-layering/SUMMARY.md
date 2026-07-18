# Stream wrapper layering — SUMMARY

**Brief:** `brief.md` (correctness of slice/verify/outer wrappers + whether
slicing+verification can collapse into one stream under `ArchiveStream`).

**Reviewed at:** PR #136 @ `2a6b91b` (solid selective decode + nested
`ArchiveStream` collapse), on top of `main` @ `38f7b99`. The brief's sharpening
(STORED isolation, collapse already fixed, irreducible floor) lives on that PR;
this review evaluates the **post-#136** stack. On plain `main` before #136,
`stream_members` still double-wraps (`ArchiveStream → ArchiveStream →
VerifyingStream → …`); after #136 the public handle's `_inner` is
`VerifyingStream` directly — confirm with `measurements.py stack`.
**Baseline:** `[all]` on #136 — **1748 passed / 87 skipped**, `ruff` / `pyrefly` /
`ty` clean.
**Host:** shared x86_64 runner, CPython 3.11. Numbers from `measurements.py`;
ratios directional but stable across ≥2 runs.

## Headline

**Partial yes — fuse verification into `ArchiveStream`; leave slicing structural.**

A single public `ArchiveStream` **can** absorb `VerifyingStream` without weakening
the Part-1 invariants that matter (sequential-EOF verify, size cap + trailing
probe, seek-disables-verify, typed-error precedence, `readinto` hashing). Doing so
also **unblocks collapsing the codec `ArchiveStream`** that today sits *under*
`VerifyingStream` and survives #136's `_collapse_nested` (which only flattens
direct nesting). Member-boundary / `SharedSource` `SlicingStream` must stay —
CONCURRENT re-seek-under-lock and internal codec slices are not 1:1 with the
public handle.

**How much it buys:** on STORED ZIP (no decode noise) the live stack is
`ArchiveStream → VerifyingStream → ArchiveStream(codec) → SlicingStream`. A true
fused `ArchiveStream → SlicingStream` removes ~8% of the *synthetic stack* time
and ~5% of end-to-end archivey read-all; archivey stays ~1.75× zipfile because
most of the residual is per-member open machinery (local-header parse, codec
resolve, lease/finalizer) plus CRC work that zipfile also does. Fusion does
**not** close the #134 deflate 2.0–2.3× gap — that remains Topic 6
(`DecompressorStream`). The stack is already near the irreducible floor on the
per-`read(64K)` path; the ranked move is still worth doing (correctness
simplification + unblocks codec-AS collapse + small measurable win), not as the
budget closer.

## Top findings

| # | Sev | Finding | Where | Status |
|---|-----|---------|-------|--------|
| F1 | **Medium** | `VerifyingStream.read(0)` (and mid-stream `read(0)`) is treated as EOF — false `CorruptionError` / `TruncatedError`. `_GzipTruncationCheckStream` already pins the opposite contract. | `verify.py` (`MemberVerifier.read`) | **fixed** (#137) |
| F2 | Low | `VerifyingStream.close()` can leave wrapper+inner unclosed when the EOF probe raises a non-`CorruptionError`/`TruncatedError` `ArchiveyError` (e.g. `EncryptionError`). | `verify.py` (`finish_on_close`) | **fixed** (#137) |
| D1 | design | **Fuse verify into `ArchiveStream`** (conditional when hashes/size present); leave `SlicingStream`/`SharedSource`/`LockedStream` and the decode engine separate. Ranked sequence in `collapse-design.md`. | `archive_stream.py`, backends | **implemented** (#137) |
| D2 | design | Nested codec `ArchiveStream` under `VerifyingStream` remains after #136; verify-fusion is what lets `_collapse_nested` finish the job. | live STORED stack: `AS → SlicingStream` | **fixed** by D1 (#137) |
| Q4 | follow-up | Real `SlicingStream.readinto` (seek+`readinto` under lock) | `slicing.py` | **parked** — future / archive-copy |

> **Archive readiness (2026-07-18):** all actionable findings done. Park Q4 then
> move this directory to `archive/`. See `../STATUS.md`.

## What is actually fine

- **`readinto` side-effect contract** holds today: `VerifyingStream` is a
  `ReadOnlyIOStream` (readinto→read); counting streams override `readinto`;
  `_GzipTruncationCheckStream` sets `readinto_passthrough=False`; no
  side-effecting `DelegatingStream` subclass was found that forgets the flag.
- **`SlicingStream` dual mode** (eager single-consumer vs locked re-seek),
  BytesIO-matching negative-seek clamp, `own_source` close, and
  construction-without-unlocked-`tell` are correct and well tested.
- **`SharedSource` / CONCURRENT** view minting and the “never unlocked
  tell/seek on a shared `BufferedReader`” rule hold; this is the real reason
  member-boundary slicing cannot fully fuse into the public handle.
- **`ArchiveStream` lazy-open claim**, `_fail` ordering (closed-file before
  translator), finalizer/lease, and #136 `_collapse_nested` (including
  translate/stamp/rewind adoption) are sound; public `_inner` after first read
  is not an `ArchiveStream` (pinned).
- **Stream-decoder F6** (over-long hashed members) is closed via
  `expected_size` on ZIP/7z/RAR verify wraps.
- **#136 nested-outer collapse** confirmed: `stream_members` no longer double-wraps;
  solid lazy `open_member` keeps verify inside `open_fn` so close cannot probe
  unselected members.

## Files

- `correctness.md` — per-wrapper audit + invariant list a fusion must keep.
- `layer-map.md` — per-backend stacks (post-#136), composition sites.
- `collapse-design.md` — verdict table, fused design, measured win, floor.
- `QUESTIONS.md` — maintainer decisions.
- `measurements.py` — runnable STORED isolation + stack dump + synthetic fusion.
