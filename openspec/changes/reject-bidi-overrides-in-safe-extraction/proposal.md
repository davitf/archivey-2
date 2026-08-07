# Reject bidi overrides during safe extraction

## Why

A Unicode bidi **override** in a filename makes the displayed order differ from the
stored order. The classic trick turns `evil‮gnp.exe` into something that reads as
`evil.png` in every file listing a user will ever see, and it is a real
social-engineering vector for anything that shows people what it just extracted.

Today the library presents the name and (after `review-diagnostics-batch`) emits
`MEMBER_NAME_BIDI_CONTROL`. That is right for listing and reading. It is not enough for
**extraction**, which is where a name becomes a filesystem path that a human then looks
at — the same place path traversal and null bytes are already refused.

**The distinction that makes this answerable — and the trap in it: bidi controls are
not one category.**

- **Overrides and isolates** — U+202A–202E (LRE/RLE/PDF/LRO/RLO) and U+2066–2069
  (LRI/RLI/FSI/PDI) — reorder *surrounding* text. The disguise needs one. No legitimate
  filename does.
- **Directional marks** — U+061C (ALM), U+200E (LRM), U+200F (RLM) — are invisible hints
  that do **not** reorder anything, and they **do occur in legitimate Arabic and Hebrew
  filenames**.

> ⚠️ The library's existing `_BIDI_CONTROLS` frozenset contains **all twelve**
> codepoints, marks included. Rejecting that set would break legitimate RTL filenames.
> The reject set is written out explicitly as the two override/isolate ranges.

RTL *script* is not affected at all: an Arabic or Hebrew filename gets its direction
from its own letters' properties. Nothing in `فهرس.txt` is in either list, so rejecting
overrides costs legitimate RTL users nothing.

## What Changes

- **`safe-extraction`** — the universal (non-bypassable, `TRUSTED` does not lift it)
  string checks gain one row: a member name or link target containing a bidi
  override/isolate is rejected.
- **`error-handling`** — a new `DeceptiveNameError(FilterRejectionError)`. Not
  `PathTraversalError`: the name does not traverse anywhere, and a caller triaging a
  batch should be able to tell "tried to escape the root" from "tried to look like
  something else". Not `UnportableNameError` either — the name is perfectly portable; it
  is *misleading*, which is a different complaint.
- **`testing-contract`** — the bidi clause now has both branches implemented, so it can
  name them: listing presents and diagnoses, extraction rejects the override subset.

Not changing: listing and reading. They still present the name exactly as stored, with
the diagnostic. The library does not get to decide a name is unreadable — only that it
will not write it to a filesystem where a person will read it back.

## Impact

- Specs: `safe-extraction`, `error-handling`, `testing-contract`.
- Code: `src/archivey/internal/naming.py` (the two sets, named apart),
  `src/archivey/internal/filters.py` (the check), `src/archivey/exceptions.py`.
- Docs: `docs/safe-extraction.md`, `docs/gotchas.md`.
- Depends on `review-diagnostics-batch` for the warn-only half's diagnostic code.
