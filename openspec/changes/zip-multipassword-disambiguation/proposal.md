# Multi-candidate password disambiguation (ZipCrypto and beyond)

## Why

The candidate-password model (`archive-reading` → "Password candidates and provider")
says the reader tries candidates in order and adds "every password that **succeeds**" to
the known-good list. It does not define what *succeeds* means when the format's
per-open password check is **weak**.

Traditional ZIP encryption (ZipCrypto / PKWARE) validates only a **single verification
byte** when a member is opened; the authoritative integrity check is the CRC (and, for a
compressed member, the decompressor), which stdlib `zipfile` performs only as the data is
*read*. So ~1/256 of wrong passwords pass `open()`. When several candidate passwords are
supplied, accepting the first candidate whose `open()` succeeds lets a wrong candidate
**false-accept**: the correct password is never tried, and the CRC mismatch surfaces later
as a spurious `CorruptionError`. This is an intermittent, ~1/256 failure — it is exactly
what made the `encrypted-multi` corpus entry flaky in CI (`very_secret.txt`, whose
password differs from the other members).

A **minimal correctness fix already landed on this branch** (PR #53): when disambiguating
among multiple candidates, the ZIP reader confirms a candidate by reading the member to
completion before accepting it. That closes the bug but is deliberately unoptimized — it
reads the whole member during the trial, with no size cap and no cheap pre-filter. This
change specifies the **full disambiguation ladder** the fix should grow into, and lifts
the "confirm before accept" idea to a **cross-format** contract so future weak-check
ciphers (and 7z/RAR paths) inherit it. **Specs only — no code lands here.**

## What Changes

- **`archive-reading`** — one added requirement: *a candidate password is confirmed by
  the format's authoritative integrity check before it is accepted or added to the
  known-good list.* Where the per-open check is weak, the reader MUST disambiguate among
  the candidates that pass it, MUST NOT accept a candidate on a weak check alone when it
  is disambiguating, and has a defined behavior for the genuinely-ambiguous residual
  (multiple candidates satisfy every available check): pick deterministically **and
  surface that it guessed**, or fail with `EncryptionError` — never silently return data
  decrypted with an unconfirmed password.

- **`format-zip`** — one added requirement: the ZipCrypto instantiation of the ladder —
  (1) the 1-byte verification filter (`open()`); (2) for a compressed member, a
  first-block decode; (3) for a member within a size budget, a full decode + CRC;
  (4) content-affinity and neighbour-member heuristics for the residual; (5) the default
  choice + warning, or fail-fast. The single-candidate fast path is unchanged (no eager
  full read), and a genuinely corrupt archive is still reported as corruption, not as a
  password problem.

- **Dependency, not built here — the warnings-as-data mechanism (threat-model C2).** The
  "we guessed the password" and "we had to disambiguate" outcomes want to be *structured
  data* on the result, not just a log line — the same primitive name normalization and
  detection conflicts need. This change **specifies the behavior** (warn / record an
  occurrence, or fail) and **consumes** that mechanism when it exists; the mechanism
  itself is a separate C2 change. In the interim the reader logs via the `logging`
  capability.

Not changing detection, the public `password=` API, or the `PasswordProvider` contract.

## Impact

- Affected specs: `archive-reading` (added requirement), `format-zip` (added requirement).
- Affected code (when scheduled): `internal/password.py` (`_PasswordCandidates`:
  confirm-before-accept, ambiguity resolution), `internal/backends/zip_reader.py` (the
  ladder: cheap pre-filters, size budget, heuristics), and — when C2 lands — the result
  surface that carries the "disambiguated / guessed" occurrence.
- Builds on the minimal fix in PR #53; the ladder supersedes its unbounded full-read.
- Sequencing: land C2 (warnings-as-data) first or concurrently, so the residual outcome is
  reported as data rather than only logged.
