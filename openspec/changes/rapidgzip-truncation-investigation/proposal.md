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

Debt-ledger **Q4** (2026-07-20) decided this change **PAY before 0.2.0**: shipping a
self-described under-characterized guard on a supported (even opt-in) path is unacceptable
release debt. Measurement and the narrow/extend/remove implementation are deferred to a
follow-up PR; `design.md` collects code/threat-model/ledger pointers so that work does not
have to re-discover AUTO↔ISIZE coupling, the VerifyingStream vs ISIZE split, or why fuzz
jobs cannot stand in for the characterization matrix.

## Investigation outcome (Linux; awaiting lock-in)

Full write-up: [`FINDINGS.md`](FINDINGS.md). Headline results:

- The “silent only for ~10-byte header” hypothesis is **false**. Mid-body truncations
  with a valid header commonly return `b""` with no exception while stdlib raises.
- **`readall()` misleads:** stdlib sized/`read(1)` loops **do** stream correct partial
  prefixes then raise; a bare `read()` raises with no return value. rapidgzip’s defect
  is staying **silent** at EOF (empty or short/full), not refusing to stream.
- **Priorities for the fix:** (1) no silent success, (2) recover partial data,
  (3) seekability on good inputs.
- **Recommended shape (not locked):** keep rapidgzip for (3); on EOF with **0 bytes
  delivered**, fall back to stdlib sized-reads (covers silent-empty → (1)+(2); valid
  empty gzip still OK); **keep/extend ISIZE** for silent short/full that empty-fallback
  misses (multi-block / trailer strip); close the `< 18` hole; safe multi-member ISIZE
  sum. **Reject** remove, narrow-only, and DIY reverse deflate-block seek (gzip trailer
  is CRC+ISIZE only — not an xz/lzip index).

macOS/Windows confirmation (task 1.3) still open.

## What Changes

- **`seekable-decompressor-streams`** — refine the truncation requirement for the rapidgzip gzip
  path once its behavior is characterized: state precisely which truncations rapidgzip reports
  itself, which require a backstop, how silent-empty vs silent-short/full are handled
  (stdlib fallback vs ISIZE), and how multi-member files are handled. Prefer recovering
  partial data where stdlib can, without ever false-flagging a valid file.
- No detection/format changes; this is about the gzip accelerator's truncation reporting only.
- No DIY gzip seek index from trailers (out of scope / rejected — see `FINDINGS.md`).

This change is **investigation + specs**: it records measurements, decision criteria, and a
maintainer-facing recommendation. The chosen implementation lands when §2 is locked.

## Specs

Proposed delta (kept here until accepted, per the "propose in `changes/`, don't edit shipped specs
ad hoc" rule). See also `specs/seekable-decompressor-streams/spec.md`.

### seekable-decompressor-streams — MODIFIED Requirement: Accelerator backends surface corruption and truncation uniformly

The system SHALL surface corrupt or truncated input read through the rapidgzip accelerator as the
same `compressed-streams` error types as the stdlib path (`CorruptionError` / `TruncatedError`),
never a raw third-party exception. For truncation specifically, the system SHALL rely on
rapidgzip's own end-of-input errors where it raises them. Where rapidgzip reaches EOF having
delivered **no** decompressed bytes without raising, the system SHALL fall back to the stdlib
gzip path (sized reads) so truncation is signaled and any recoverable prefix is available.
Where rapidgzip delivered a non-empty prefix (or full payload) and reached EOF without raising,
the system SHALL apply a length/ISIZE backstop that covers those characterized silent cases
without ever false-flagging a valid file; multi-member scope SHALL be stated explicitly
(safe per-member ISIZE sum, not “any further header ⇒ accept”).

#### Scenario: a truncation rapidgzip reports itself

- **WHEN** a truncated gzip is read through rapidgzip and rapidgzip raises its own end-of-input error
- **THEN** that error is translated to `TruncatedError` (or `CorruptionError`), with no reliance on the ISIZE backstop

#### Scenario: silent empty EOF from rapidgzip

- **WHEN** a truncated gzip is read through rapidgzip and the first EOF delivers zero decompressed bytes without an exception
- **THEN** the system falls back to stdlib gzip sized-reads on the same source, surfaces `TruncatedError` (from stdlib `EOFError`), and exposes any correct partial prefix stdlib recovered (a valid empty gzip still succeeds with zero bytes)

#### Scenario: silent short or full EOF from rapidgzip

- **WHEN** a truncated gzip is read through rapidgzip and a non-empty decompressed prefix (or full payload) is returned without an exception
- **THEN** the ISIZE/length backstop raises `TruncatedError` at sequential EOF, and a valid single- or multi-member file is never false-flagged
