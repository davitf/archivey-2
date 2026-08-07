# Design — reject bidi overrides during safe extraction

## Decisions

### Two sets, named apart, because one of them is a trap

`_BIDI_CONTROLS` is the *advisory* set: everything worth telling a caller about. The
reject set is a strict subset, and conflating them breaks real users:

| Set | Codepoints | Used by |
|---|---|---|
| `BIDI_CONTROLS` | U+061C, U+200E, U+200F, U+202A–202E, U+2066–2069 | the `MEMBER_NAME_BIDI_CONTROL` diagnostic |
| `BIDI_REORDERING_CONTROLS` | U+202A–202E, U+2066–2069 only | the extraction rejection |

One outside review proposed "reject the bidi control set" and separately listed exactly
the override/isolate ranges, not noticing that this library's set is broader. Reading
that as "reject everything we currently warn about" would sweep in U+061C, U+200E and
U+200F and reject legitimate Arabic and Hebrew filenames. The two constants are
therefore spelled out separately, with the reject set defined by enumerating its ranges
rather than by subtracting from the other — a subtraction expression would put the three
marks one editing mistake away from being rejected again.

### Why the marks are safe to keep and the overrides are not

The functional difference is whether the codepoint changes the rendered order of
*surrounding* text.

- LRM/RLM/ALM set the direction of a single neutral character (a digit, a punctuation
  mark) between two runs. They cannot make `exe` render as `png`, and they appear in
  real filenames — a Hebrew name containing a Latin product code often needs one to
  render correctly.
- LRE/RLE/LRO/RLO/PDF and the isolates open a *span*. Everything inside is re-ordered.
  That is exactly and only what the disguise needs.

### A new exception rather than an existing one

`PathTraversalError` would be wrong twice: the name does not traverse, and a caller
triaging a batch of rejections could no longer tell an escape attempt from a spoofing
attempt. `UnportableNameError` is closer but still false — the name is perfectly
portable; it is *misleading*, which is a different complaint and has a different fix
(the sender should rename it, not re-encode it).

`DeceptiveNameError(FilterRejectionError)` inherits the whole existing rejection
lifecycle for free: it is caught by `except FilterRejectionError`, it produces a
`BLOCKED` `ExtractionResult` under `OnError.CONTINUE`, and it counts as a policy block
in the report — all of which is what a batch extractor wants here.

### Link targets get the same check

A symlink whose *target* carries an override is the same attack with an extra hop: the
listing shows a plausible target, the link on disk points somewhere else-looking. The
target check reuses the name check and raises the same type; targets already get the
same NUL/encodability treatment as names, so this row sits beside them.

### It is universal, not policy-gated

`ExtractionPolicy.TRUSTED` lifts *portability* transforms, not safety constraints, and
this is a safety constraint. A caller who genuinely wants such a file on disk can read
the member and write it themselves — the library declines to be the thing that puts a
disguised name in front of a person.

## Rejected alternatives

**Sanitize instead of reject** (strip the controls, emit `EXTRACTION_NAME_SANITIZED`).
Tempting, and it is what the existing trailing-dot/space handling does. Rejected because
stripping changes which file the extracted tree claims to contain: `evil‮gnp.exe` would
land as `evilgnp.exe`, a name that was in no archive and that still does not tell the
user what happened. Trailing-dot stripping is safe because the *intent* of the name
survives; here the intent is the problem.

**Reject at listing time.** Rejected: listing is not where a name becomes a path, and
refusing to *report* what an archive contains would make the library useless for exactly
the forensic caller who most needs to see the hostile name. `VISION.md`'s founding use
case is indexing messy and hostile input; it has to be able to look at it.
