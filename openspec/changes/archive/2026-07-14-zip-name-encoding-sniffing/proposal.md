## Why

ZIP stores a per-entry flag (general-purpose bit 11) that declares the member name is
UTF-8. When the flag is **absent**, APPNOTE says the name is code page 437. But a large
share of real-world archives — anything zipped by modern Info-ZIP, many Linux tools, and
language runtimes — write **UTF-8 bytes without setting the flag**. Decoding those as cp437
produces mojibake: `Español.txt` becomes `Espa├▒ol.txt`.

v1 handled this by decoding unflagged names as `utf-8 → cp437 → cp1252 → latin-1` (UTF-8
first). v2 dropped that: it passes names straight to `zipfile` with no `metadata_encoding`,
so unflagged names fall back to cp437 and regress the common case. The regression is caught
by a stored real-world fixture (`encoding_infozip_jules.zip`), which v2 currently decodes to
mojibake unless the caller happens to pass `encoding="utf-8"`.

The maintainer's questions this change must answer: can we be *smarter* about detection
without making *worse* guesses, and does an off-the-shelf charset-detection library help, or
is the missing-UTF-8-marker case narrow enough to fix directly? Those are settled in
`design.md`; the short answer is a bounded UTF-8-validity sniff, no new dependency.

## What Changes

- **Sniff UTF-8 for unflagged ZIP names** when — and only when — there is no authoritative
  signal: the UTF-8 flag is unset **and** the caller passed no explicit `encoding=`. A name
  whose bytes are valid UTF-8 is decoded as UTF-8; otherwise it falls back to a configurable
  legacy encoding (default cp437, per APPNOTE).
- **Never sniff when a signal exists.** A set UTF-8 flag is authoritative; an explicit
  `encoding=` is authoritative and disables sniffing. Both are honored unchanged.
- **Make the decision observable.** When the sniff overrides the APPNOTE cp437 default, emit
  a `diagnostics` warning carrying the member and chosen encoding, so a caller can inspect or
  escalate rather than silently trust a guess.
- **Reject a statistical charset-detection dependency** (`charset-normalizer` / `chardet`)
  for member names — see design; filenames are too short for reliable statistical detection
  and it violates the zero-dep core. Recorded as a rejected alternative, not adopted.

## Capabilities

### New Capabilities

<!-- none -->

### Modified Capabilities

- `format-zip`: decode unflagged member names by UTF-8-validity sniff with a configurable
  legacy fallback, gated on the absence of an authoritative encoding signal, and surface a
  diagnostic when the sniff overrides the cp437 default.

## Impact

- ZIP backend (`internal/backends/zip_reader.py`): member-name decoding no longer defers
  unconditionally to `zipfile`'s cp437 default for unflagged names; adds the gated sniff +
  fallback and the diagnostic.
- Config (`ArchiveyConfig`): a legacy-fallback encoding knob (default cp437) — exact name is
  a design decision.
- Public surface: unflagged non-ASCII names may now decode differently (correctly) than in
  the current v2; behavior is documented and the explicit `encoding=` escape hatch is
  unchanged. A residual, documented false-positive risk exists (short legacy bytes that are
  coincidentally valid UTF-8).
- Tests: port `encoding_infozip_jules.zip` as a regression fixture asserting the recovered
  names; add unit coverage for the gate (flag set / explicit encoding / invalid UTF-8).
- Related but **out of scope**: TAR raw-byte names (no marker at all) and the same
  UTF-8-first principle for other byte-name formats — noted in design as a possible follow-on.
