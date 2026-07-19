# Questions for the maintainer

> **Archived 2026-07-19.** Q1–Q3 decided and implemented in #137. **Q4** parked
> → `../../backlog.md` Topic 6 (with optional `VerifyingStream` cleanup). See
> `../../STATUS.md`.

## Q1 — Accept the partial-collapse verdict? — DONE (#137)

**Implemented in this PR** (on top of #136): `MemberVerifier` fused into
`ArchiveStream`; ZIP/7z/RAR backends pass `expected_hashes` /
`expected_size`; codec `ArchiveStream` collapses through; F1/F2 fixed.
`SlicingStream` / `SharedSource` / decode engine left alone.

Alternatives considered and rejected:

- **Fuse slice+verify into one stream** — breaks or duplicates CONCURRENT /
  `SharedSource` view minting for a sub-percent read-path win past the floor.
- **Do nothing on layering; only fix F1/F2** — leaves the codec `ArchiveStream`
  stranded under verify forever; #136's collapse cannot finish.
- **Sell fusion as closing the ≤1.3× gap** — numbers say ~5% STORED end-to-end;
  deflate remains Topic 6.

## Q2 — F1 severity / fix venue — DONE (#137)

**Done in this PR** with the fusion (`MemberVerifier.read` treats `n == 0` as
a no-op).

## Q3 — Keep `VerifyingStream` as a helper type? — DONE (#137)

**Chose (b):** `MemberVerifier` holds the logic; `VerifyingStream` remains a
thin wrapper for `codecs.py`'s length-only backstop and unit tests. Member
backends no longer construct it. Deleting the class later is optional cleanup.

## Q4 — `SlicingStream.readinto` follow-up? — PARKED (Topic 6)

The brief hoped a fused `readinto` would matter more than dispatch count. On
this host it does not, until the slice layer also stops allocating.

**Parked 2026-07-19** into `review/backlog.md` Topic 6 adjacency — not a 0.2.0
blocker; no extract path has been shown `readinto`-bound.
