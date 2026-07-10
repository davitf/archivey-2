# Design — multi-candidate password disambiguation

## The problem, precisely

A cipher's *per-open* password check and its *authoritative* integrity check can differ in
strength. For ZipCrypto they differ enormously:

- **Per-open check**: a single verification byte (the high byte of the CRC, or of the DOS
  mod-time when a data descriptor is used). `zipfile.ZipFile.open(info, pwd=…)` raises
  `RuntimeError("Bad password")` when it mismatches. ~1/256 of wrong passwords pass it.
- **Authoritative check**: the CRC-32 over the decompressed data (and, for a compressed
  member, the decompressor itself rejecting a corrupt stream). `zipfile` performs this only
  as the member is *read*, at EOF — outside the candidate trial.

Accepting a candidate on the per-open check alone therefore lets a wrong candidate win
~1/256 of the time; the correct candidate is never tried and the CRC failure resurfaces
later as a spurious `CorruptionError`.

## The ladder (resolves the maintainer's proposed algorithm)

For one encrypted unit with candidate set `C` (known-good ∪ sequence ∪ provider):

1. **Per-open filter.** Keep only candidates that pass the cheap per-open check (ZipCrypto's
   verification byte, via `open()`). This is already how a wrong password is rejected
   today, so **the expensive steps below run only for candidates that survive it** — in the
   common case (one right password among wrong ones) all but one wrong candidate are
   dropped here for free. **If exactly one survives, accept it without decoding.** *(The
   minimal fix in PR #53 does not yet short-circuit on a lone survivor — it validates every
   surviving candidate by reading; adding the "lone survivor ⇒ accept" fast path is the
   first optimization this change adds, and it removes the extra full read in the ordinary
   two-password case.)*
2. **Cheap decode probe (compressed members).** For a DEFLATE/BZIP2/LZMA member, decode a
   first block under each surviving candidate; a decompressor error eliminates it. This
   catches essentially all wrong ZipCrypto survivors at negligible cost (no full read).
3. **Full decode + CRC, size-gated.** For a member whose uncompressed size is within a
   budget (proposed **≤ 16 MiB**, config-overridable), decode fully and check the CRC under
   each still-surviving candidate; eliminate CRC failures. Above the budget, do **not**
   full-read every candidate — rely on step 2 plus the heuristics below, and prefer
   fail-fast over an unbounded read.
4. **Heuristics for the residual** (multiple candidates still survive — only possible for
   STORED small members, or the astronomically unlikely CRC collision):
   - **Neighbour affinity.** Prefer the password that decrypted the previous/next member of
     the same archive: members added in one pass share a password, so adjacency is a strong
     signal. (Cheap; uses state the known-good list already tracks.)
   - **Content plausibility.** Optionally, prefer the candidate whose plaintext is not
     gibberish (e.g. a decodable magic/MIME sniff). Lowest priority; opt-in.
5. **Unresolved residual.** If still ambiguous, either (a) pick the highest-priority
   candidate (known-good/neighbour order) and **record that the choice was a guess**, or
   (b) raise `EncryptionError("cannot determine the correct password")`. Proposed default:
   **fail-fast** when two candidates pass a *full CRC* (a real collision is not something to
   paper over); **guess-with-warning** only when the ambiguity is merely that a large member
   exceeded the decode budget (step 3) and could not be fully confirmed.

## Cross-format placement

The "confirm before accept" rule belongs in `archive-reading` (the candidate model), not
only in ZIP: any cipher with a weak per-open check inherits it. `_PasswordCandidates.attempt`
today records success the instant `decrypt()` returns non-`None`; the contract becomes
"record success only once the unit's authoritative check has confirmed the password."
Backends whose key derivation already authenticates strongly (7z AES with its check, RAR5)
satisfy step 1 as their authoritative check and never reach the ladder — they are unchanged.

## Warnings-as-data (C2) coupling

Steps 4–5 produce information the caller should be able to act on: "member X was decrypted
with a *guessed* password" / "candidates A and B both matched member X". A log line is
invisible to most applications (threat-model C2: *warnings that should be data*). This
change specifies the behavior and consumes a first-class occurrence/warning mechanism when
it lands; that mechanism is its own change (it also serves name-normalization and
detection-conflict warnings). Until then, `logging` via the `archivey.*` loggers.

## Why not always full-read (the minimal fix's shortcut)

The PR #53 fix full-reads every surviving candidate with no size cap. That is correct but
pays a full decode (twice, for the winner) even in the ordinary case and reads unboundedly
for large members. Steps 1–3 above make the common case free (lone survivor after the
per-open filter) or cheap (first-block probe), and cap the worst case. The size budget and
the fail-fast-vs-guess default are the two decisions this proposal pins down.
