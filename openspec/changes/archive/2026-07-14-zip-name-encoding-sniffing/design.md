# Design — ZIP name encoding detection

## The problem, precisely

A ZIP local/central header carries the filename as raw bytes plus general-purpose bit 11
("language encoding flag", EFS). Bit 11 set ⇒ the bytes are UTF-8. Bit 11 clear ⇒ APPNOTE
says code page 437. Three populations of unflagged archives exist in the wild:

1. **Actually cp437 / OEM** — genuinely old DOS-era archives. Rare today.
2. **Actually a local ANSI codepage** (cp1252, cp932/Shift-JIS, …) — written by older
   Windows tools that never set the flag. Uncommon and shrinking.
3. **Actually UTF-8, flag not set** — modern Info-ZIP, many Linux/CI tools, and language
   runtimes. **This is the common case now**, and the one v2 currently gets wrong.

So the practical question is almost always "is this UTF-8 or a legacy codepage?", not "which
of 30 codepages is this?".

## Key insight: UTF-8 is self-validating

UTF-8 multibyte sequences have rigid structure (lead byte announces length; every
continuation byte is `10xxxxxx`). Arbitrary cp437/cp1252 filename bytes almost never satisfy
that structure by accident — the longer the non-ASCII run, the lower the collision odds. So
"decode as UTF-8; if it succeeds, it *is* UTF-8" is a high-precision test for population (3)
without needing frequency analysis. This is exactly why v1's `utf-8 → cp437 → …` ladder
worked. Pure-ASCII names are unaffected (valid in every candidate).

## Options considered

### A. Status quo — cp437 per APPNOTE (rejected)
Spec-literal, deterministic, but wrong for the dominant real-world case and a regression from
v1. Produces silent mojibake on exactly the files users care about.

### B. Gated UTF-8-validity sniff + legacy fallback (**chosen**)
For an unflagged name **with no explicit `encoding=`**: try UTF-8; on success use it; on
`UnicodeDecodeError` fall back to a configurable legacy encoding (default cp437). Cheap,
zero-dependency, predictable, and reversible in behavior. Mirrors v1's proven approach,
tightened with an explicit gate and a diagnostic.

### C. Statistical charset-detection library — `charset-normalizer` / `chardet` (rejected)
The maintainer asked whether a "smart detection" library helps. Investigated:

- **Short-input unreliability.** Both libraries are tuned for *documents* (paragraphs). A
  filename is often < 20 bytes with one or two non-ASCII chars — far below where n-gram /
  frequency models are dependable. They frequently disagree on short strings and can return
  cp1250/cp1252/latin-1 interchangeably.
- **Wrong tool for the actual ambiguity.** Our real fork is UTF-8-vs-legacy, which UTF-8
  *validity* answers more reliably than statistics. A detector can even override a
  valid-UTF-8 string with a legacy guess — strictly worse.
- **Dependency cost.** The core is zero-dependency by charter (`decision 0011`).
  `charset-normalizer` is MIT / pure-Python (viable as an *extra*), `chardet` is LGPL; either
  way it buys unreliability on short inputs for a case option B already covers.
- **Verdict:** do not adopt for names. Revisit only if a future opt-in "aggressive/legacy
  recovery" mode targets population (2) specifically — and even then behind an extra and off
  by default.

### D. Explicit `encoding=` only, no sniffing — the "never guess" stance (partially adopted)
Deterministic and never wrong-guesses, but pushes the burden onto callers who usually don't
know the encoding, and keeps the common regression. We keep its *good* half: an explicit
`encoding=` is authoritative and **disables** sniffing entirely. We reject it as the sole
behavior.

## Avoiding wrong decisions (the maintainer's real concern)

The sniff is constrained so it only acts where there is genuinely no better signal, and it
never silently overrides an authoritative one:

1. **Gate on absence of signal.** Sniff only when bit 11 is clear *and* no `encoding=` was
   passed. Flagged names → UTF-8 as declared. Explicit `encoding=` → used verbatim, no sniff.
2. **Validity, not frequency.** UTF-8 is accepted only when the bytes decode cleanly; the
   fallback is a single configurable legacy encoding, not a guess among many.
3. **Observable.** When the sniff overrides the cp437 default, emit a `diagnostics` warning
   (member name + chosen encoding). Silent-but-different is the thing to avoid; a diagnostic
   makes every non-APPNOTE decision inspectable and escalatable via `DiagnosticPolicy`.
4. **Configurable fallback.** Default cp437 (APPNOTE), but a caller who knows their corpus is,
   say, Shift-JIS can set the legacy fallback so population (2) is handled deterministically
   without statistics.
5. **Documented residual risk.** A short legacy byte run that is *coincidentally* valid UTF-8
   will decode as UTF-8. This is rare, strictly better than today for the common case, and
   fully overridable with `encoding=`. We accept and document it rather than paper over it
   with a detector that adds its own, larger error surface.

## Config surface (decision pending on naming)

`ArchiveyConfig` gains an optional legacy-fallback encoding for unflagged ZIP names, default
`"cp437"`. Working name `zip_unflagged_fallback_encoding`; confirm against existing config
naming before implementing. The per-call `encoding=` on `open_archive` continues to override
everything (and disables the sniff).

## Scope and follow-ons

- **In scope:** ZIP unflagged member names (the flag/no-flag fork and the concrete
  regression). Comments already try UTF-8→cp437 and are unchanged.
- **Out of scope (noted):** TAR names are raw bytes with *no* encoding marker at all
  (GNU/pax frequently UTF-8); the same UTF-8-first-then-fallback principle could be lifted
  into a shared helper and applied there as a follow-on change. RAR3 non-BMP truncation is
  already fixed separately; 7z stores UTF-16/UTF-8 natively. Single-file compressors derive
  names from the outer filename (OS encoding), not archive metadata.
