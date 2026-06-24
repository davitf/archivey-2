# Characterize rapidgzip truncation/corruption handling; refine or remove the ISIZE backstop

## Why

`_open_gzip` wraps the rapidgzip accelerator in a `_GzipTruncationCheckStream` that backstops
truncation detection: on a full read to EOF (seekable **path** sources only), it compares the
decompressed length (`mod 2³²`) against the gzip `ISIZE` trailer and raises `TruncatedError` on a
mismatch, disambiguating concatenated multi-member gzip by scanning for a further gzip header.
This exists because rapidgzip does **not** reliably surface truncation — it raises for some cuts
but silently returns short/zero output for others.

That backstop is a heuristic built on incomplete knowledge of rapidgzip's actual behavior, and
it has real weaknesses:

- **It only handles a single member.** For concatenated multi-member gzip it bails out entirely
  (any further gzip header ⇒ "assume valid"), so truncation of a multi-member file is not caught.
  Doing better means summing per-member ISIZE values — but a naive sum risks false positives.
- **The multi-member disambiguation can false-negative.** A truncated single member whose partial
  deflate tail coincidentally contains the bytes `1f 8b 08` is treated as multi-member and not
  flagged. (It never false-*positives* on a valid file, which is the important direction.)
- **Rarity is unknown.** Maintainer testing suggests rapidgzip actually reports an error for
  almost all truncations and only stayed silent for a file **exactly 10 bytes** long (a bare gzip
  header with no deflate payload), e.g.:

  ```
  10 OK 0
  [stderr] Unexpected end of file when getting block at 10 B 0 b (block index: 0) on demand
  11 std::exception
  ```

  If the silent case is that narrow, the ISIZE backstop may be far more machinery than the
  problem warrants — a targeted check (or none) could be enough.

So before committing to the full ISIZE approach we should **characterize rapidgzip's
truncation/corruption behavior precisely** (across input sizes, member counts, and cut points, on
both Linux and macOS), then decide whether to: keep a narrowed backstop, extend it to multi-member
with a safe size comparison, or remove it in favour of rapidgzip's own errors plus a small
special-case.

An interim, low-risk fix already landed in the originating PR (#14): the multi-member header scan
no longer reads the whole file into memory — it scans in fixed-size blocks with overlap.

## What Changes

- **`seekable-decompressor-streams`** — refine the truncation requirement for the rapidgzip gzip
  path once its behavior is characterized: state precisely which truncations rapidgzip reports
  itself, which require a backstop, and how multi-member files are handled (or explicitly out of
  scope). The backstop becomes "as small as correctness allows" rather than a broad ISIZE compare.
- No detection/format changes; this is about the gzip accelerator's truncation reporting only.

This change is **investigation + specs**: it records what to measure and the decision criteria. The
chosen implementation (narrow / extend / remove) lands when the change is accepted.

## Specs

Proposed delta (kept here until accepted, per the "propose in `changes/`, don't edit shipped specs
ad hoc" rule).

### seekable-decompressor-streams — MODIFIED Requirement: Accelerator backends surface corruption and truncation uniformly

The system SHALL surface corrupt or truncated input read through the rapidgzip accelerator as the
same `compressed-streams` error types as the stdlib path (`CorruptionError` / `TruncatedError`),
never a raw third-party exception. For truncation specifically, the system SHALL rely on
rapidgzip's own end-of-input errors where it raises them, and SHALL apply a backstop **only** for
the characterized cases where rapidgzip silently returns short/zero output. The backstop SHALL be
the narrowest check that covers those cases without ever false-flagging a valid file, and its
scope (single-member vs. multi-member) SHALL be stated explicitly rather than implied.

#### Scenario: a truncation rapidgzip reports itself

- **WHEN** a truncated gzip is read through rapidgzip and rapidgzip raises its own end-of-input error
- **THEN** that error is translated to `TruncatedError` (or `CorruptionError`), with no reliance on the ISIZE backstop

#### Scenario: a truncation rapidgzip does not report

- **WHEN** a truncated gzip is read through rapidgzip in a characterized silent-truncation case (e.g. a bare-header-only input)
- **THEN** the backstop raises `TruncatedError`, and the check is scoped so a valid single- or multi-member file is never false-flagged
