# Six diagnostic codes from the simplicity & consistency review

## Why

Six separate review decisions each need a `DiagnosticCode`. They share the taxonomy, the
context dataclasses and the policy plumbing, so they land as one change: six changes
would mean five rebases over the same files.

Five of the six are the same failure shape — **the library knows something the caller
would want and says nothing** — and the sixth (`MEMBER_NAME_BIDI_CONTROL`) is the
library's *only* advisory with no code at all, the `VISION.md` warnings-as-data gap
verbatim.

The batch also settles a rule that has been argued three times and decided ad hoc each
time: **what happens when an explicit argument names something the resolved backend
cannot act on.** `#225` made a wrong `format=` on a directory path raise; `password=` on
an unencrypted archive raised for a string and a list but opened fine for a provider
callable; `encoding=` was silently discarded by five backends. O5 resolved it by
splitting on intent, and this change writes that rule into `archive-reading` so the next
argument's category is a question with an answer rather than a fresh debate.

## What Changes

### The rule (`archive-reading`)

> Refuse when an argument is an **assertion about this archive** (`format=` — "I claim
> this is a ZIP"). Permit, and record a diagnostic, when it is a **resource offered for
> use if needed** (`password=`, `encoding=`).

### The codes (`diagnostics`)

| Code | Fires when |
|---|---|
| `ENCODING_ARGUMENT_UNUSED` | an explicit `encoding=` reaches a backend that decodes names some other way (7z, RAR, ISO, directory, single-file) |
| `PASSWORD_ARGUMENT_UNUSED` | `password=` reaches a format with no encryption a password could unlock |
| `MEMBER_NAME_BIDI_CONTROL` | a presented member name carries a Unicode bidi formatting control |
| `EMPTY_ARCHIVE` | a listing completes, without error, with zero members |
| `EXPLICIT_FORMAT_LISTED_EMPTY` | an explicit `format=` produced an empty listing and detection disagrees |
| `EXTENSION_FORMAT_UNCONFIRMED` | the format came from the **extension** with no content confirmation, and the listing is empty |

### The behaviour change that comes with 4b

**`password=` becomes permissive in every form.** Today a provider callable opens fine on
an unencrypted archive while a plain string or a list raises `UnsupportedOperationError`
— an asymmetry with no defence, reachable only by wrapping your password list in a
lambda, which nobody would guess. All three forms now behave the same: accepted, never
consulted, diagnostic recorded. A *wrong* password on an *encrypted* archive still fails
loudly, which is the case that actually matters.

This falsifies a documented rule in three places, all fixed here:
`review/docs/independent/must-explain.md:169`, `review/docs/outline.md:167`, and the gate
itself in `src/archivey/core.py`.

### Detection (`format-detection`)

`EXTENSION_FORMAT_UNCONFIRMED` is the layer `EXPLICIT_FORMAT_LISTED_EMPTY` does not
reach: a zero-filled file named `z.tar` has no explicit `format=` to disagree with, and
content detection *does* refuse those bytes — it just never gets consulted, because the
extension fallback fires first. Both checks run **only on an empty listing**, so they
cost nothing on a normal archive.

### `testing-contract`

The RTL clause is a disjunction — "rejected **or** exactly one warning" — which is why
nobody noticed the code only ever warned. Tightened to name the warn half, which is what
this change ships; W5 (`reject-bidi-overrides-in-safe-extraction`) owns the reject half.

## Impact

- Specs: `diagnostics`, `archive-reading`, `format-detection`, `testing-contract`.
- Code: `src/archivey/diagnostics.py`, `src/archivey/core.py`,
  `src/archivey/internal/base_reader.py`, `src/archivey/internal/naming.py`,
  `src/archivey/internal/backends/*_reader.py` (one `USES_ENCODING` declaration each).
- Docs: `docs/errors-and-diagnostics.md`, `docs/opening-and-listing.md`, `docs/gotchas.md`.
- Caller-visible: `password=` stops raising on unencrypted archives; six new codes appear
  in `reader.diagnostics`.
