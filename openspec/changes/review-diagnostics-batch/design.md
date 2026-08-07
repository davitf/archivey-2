# Design — the review diagnostics batch

## Decisions

### Assertion versus resource is the rule, and it is written down

The three arguments behaved three ways because each was decided on its own. O5's framing
is the thing worth keeping, because it decides the *next* argument too:

| Argument | Intent | Behaviour |
|---|---|---|
| `format=` | assertion — "I claim this is a ZIP" | refuse when it cannot hold |
| `password=` | resource — a keyring | permit in every form, record a diagnostic |
| `encoding=` | resource — a hint for name decoding | permit, record a diagnostic |

The evidence that decided it was the asymmetry *inside* `password=`: a provider callable
already opened an unencrypted archive fine while a static string raised. The permissive
behaviour already existed; it was just reachable only by wrapping the list in a lambda.
Making all three forms permissive removes an inconsistency rather than adding a
permission.

What is given up, stated plainly: a caller who passes `password=` to an archive with no
encryption no longer gets an immediate error. That is the batch caller's whole point
("here are the twenty passwords we know — open whatever you can"), and the diagnostic is
there for anyone who wants to check. The typo case this used to catch is narrow; a
*wrong* password on an *encrypted* archive still fails loudly.

### `USES_ENCODING` is a backend declaration, like `SUPPORTS_PASSWORD`

`password=` already had a central gate: `ReadBackend.SUPPORTS_PASSWORD`. `encoding=` had
none, which is why five backends could `del encoding` without anything noticing. The new
`ReadBackend.USES_ENCODING` is the same shape, so the two arguments are now checked the
same way in the same place, and a new backend answers the question by declaring rather
than by silently doing nothing.

Only the caller's **explicit** `encoding=` counts. The detector's `encoding_hint` reaches
the same parameter, and a hint nobody asked for being unused is not worth a diagnostic.

### ISO is the awkward one, and this change does not fix it

Q2 noted that for 7z (UTF-16 names) and single-file (name from the filesystem) the
*behaviour* is right — there is nothing to decode. ISO is different: it has real encoding
choices (Joliet UCS-2 vs Rock Ridge), the detector already produces an `encoding_hint`,
and the caller's explicit override is dropped anyway. Making ISO *honour* `encoding=` is a
`format-iso` change with its own design; it is out of scope here. The diagnostic makes the
drop visible in the meantime, which is strictly better than the silence.

### The empty-listing checks run at publish time, not at open

`EMPTY_ARCHIVE`, `EXPLICIT_FORMAT_LISTED_EMPTY` and `EXTENSION_FORMAT_UNCONFIRMED` all
key off "the listing completed with zero members", which is only known once
`_publish_materialized` runs. They therefore need one piece of open-time context —
*how the format was chosen* — carried on the reader.

That is a small frozen `FormatProvenance` record set by `open_archive` on the reader it is
about to return, rather than a new `open_read` parameter: the alternative would touch
every backend's signature for a fact no backend uses.

### Why `EXTENSION_FORMAT_UNCONFIRMED` needs no second detection pass

O8a describes this check as "run content detection on the bytes; if detection would have
refused the file, say so". In this codebase the answer is already in hand: detection
returns `FormatInfo(confidence=GUESS, detected_by="extension")` **precisely because**
magic, content probes and far magic all declined first. `detected_by == "extension"` *is*
"content detection would have refused". So the check is a field comparison, not a rescan.

### Why `EXPLICIT_FORMAT_LISTED_EMPTY` does need one, and where it stops

An explicit `format=` skips detection entirely, so there is genuinely nothing recorded to
compare against. The check re-runs `detect_format` — but only on an empty listing, and
only when the source is a `Path`, where reopening the file cannot disturb the reader.

For a stream source the check is skipped. Re-detecting would mean seeking a stream the
reader owns back to its origin and peeking, and the value of covering that case does not
justify reaching into a live source's position. The realistic shape — a file on disk with
a misleading extension, opened with `format=` — is a path. This limit is stated in the
spec rather than left as a surprise.

### `EMPTY_ARCHIVE` says the true thing rather than guessing

O8a killed "raise on zero members" with one measurement: a legitimately empty tar, as
written by `tarfile` or `tar cf empty.tar --files-from /dev/null`, is 10240 bytes, every
one of them zero — **byte-identical** to a 10 KiB zero-filled garbage file. No predicate
over the bytes separates them.

So the diagnostic says "this archive is empty", which is true, rather than "this file is
probably garbage", which is a guess. It is format-independent for the same reason: an
empty ZIP and an empty tar are equally empty, and a caller filtering a batch wants one
code, not one per backend.

**Acknowledged gap, deliberately accepted:** none of this reaches the one-off caller who
does not read diagnostics. Per O8a that would require an exception or a default, and the
measurement above says no correct exception exists. A zero-filled `z.tar` opening as an
empty archive is *correct*; a caller who needs more must use content detection (which
refuses) or check the member count.

### The bidi code emits where the warning already did

`_warn_for_bidirectional_controls` runs in `_register_member`, not in the per-backend
decoders, so directory and inferred single-file names get the same treatment and a
backend using `normalize_member_name` cannot emit a duplicate. The diagnostic replaces
the bare `logger.warning` at that same site — the collector's own logging projection
keeps the log line, so nothing that watched the logger loses it.

The context records *which* controls were found, spelled `U+202E`, because W5 then splits
that set into a reject half and a warn half; a caller (or a policy) that wants only the
override half needs the codepoints, not just "there was one".

## Rejected alternatives

**Refuse `encoding=` at the entry point (Q2 option A).** One rule replacing three special
cases is genuinely appealing, and the review recommended it. It was rejected because it
breaks the caller who passes one configuration across heterogeneous input — the batch
indexer this library exists for — and because O5 then found the sharper rule: the split is
not "can the backend act on it" but "is the caller asserting or offering".

**Raise on an empty listing (O8 revision 1).** Dead on the byte-identity measurement
above; it would reject a file `tar(1)` itself produces.

**A per-format `EMPTY_ARCHIVE` variant.** Rejected: emptiness is not a format property,
and a caller filtering a batch would have to enumerate backends to ask one question.
