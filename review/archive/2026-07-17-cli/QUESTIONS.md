# Maintainer decisions — Brief 4 (CLI, PR #120)

Design calls the review surfaced that are *not* plain bugs. Each has a
recommendation; none blocks merging the bug fixes (F1–F2 especially), but D2 and
D3 shape user-visible behavior enough that deciding them before the first
release is worth it.

## D1 — Should the smart default dest consider the member filters?

Today `smart_dest` computes top-level entries from **all** members, so
`archivey x a.zip 'b/*'` extracts into `./a/b/…` even though everything being
extracted shares the single root `b/`. Applying the include/exclude predicate
before counting tops would give `./b/…` — consistent with the "single top-level
dir needs no wrapper" rule as experienced by the user.

**Recommend:** compute tops on the filtered set (the predicate is already in
hand; one-line change). Counter-argument to weigh: with filters the wrapper dir
name (`./a/`) advertises which archive the files came from, which partial
extractions arguably benefit from more, and `unar` has no equivalent feature to
copy. Either answer is fine — but pick one and add a scenario, since the spec's
smart-dest rows don't mention filters.

## D2 — Default verbosity of extraction outcomes (renames, skips, counts)

The CLI default `--overwrite rename` means data lands under names the user
didn't ask for. `extract_all` already returns per-member results with rename
markers (`requested_path != path`) — the CLI just doesn't read them.

**Recommend:** stderr always gets one closing summary line
(`12 extracted, 2 renamed, 0 skipped → ./photos/`), renames are listed
individually even without `-v` (they change where data lives; cf. `unar`'s
"(1)" reporting), and `-v` adds the full per-member `unzip`-style trace. This
also gives `extract -v` a meaning (today it's `del`'d — F3).

## D3 — `test` semantics when a member stream fails to *open*

Digest mismatches mid-read are counted as FAIL and the run continues (good —
verified). But an open-time failure (encrypted member without a usable
password, corrupt header) raises out of the iterator and aborts the whole run
with no summary. For the verb whose job is "tell me which members are bad",
partial reporting beats aborting.

**Recommend:** catch per-member open failures inside the loop, count FAIL,
continue; abort only on failures that genuinely poison the iterator (e.g. solid
stream desync — the library can't skip past those anyway). Needs a small
library-side answer too: can `stream_members` surface per-member open errors
without terminating iteration (e.g. yield `(member, error)` or a documented
"skip damaged unit" mode)? If not, the CLI can pre-check `member.is_encrypted`
and no-password to at least fail those members individually.

## D4 — Who configures logging/diagnostics display?

The CLI sets up no logging. Library diagnostics that log at WARNING (e.g.
`Member name normalized: 'sub' -> 'sub/'`) reach the terminal through Python's
bare last-resort handler — unformatted, unsuppressable, mid-listing.

**Recommend:** `main()` installs a stderr handler (WARNING default), `-v` (or a
separate `--debug`) lowers to INFO/DEBUG, and a future `-q` raises to ERROR.
Also decide whether member-level diagnostics belong in `list -v` output (the
spec says `-v` "SHALL surface diagnostics" — currently only `member.diagnostics`
attached to members are shown, while archive-level diagnostics are not).

## D5 — Progress on `test` (and `list` for streaming formats)?

Progress is extract-only. A `test` of a multi-GB archive is silent until the
end. The tqdm plumbing and stderr discipline already exist.

**Recommend:** yes for `test` (bytes read vs. total when sizes are known) in a
small follow-up; skip `list` (fast except degenerate cases).

## D6 — Reserve more future verbs now?

Known-verb-wins means each *future* verb changes the meaning of
`archivey <word>` for a file named `<word>` — a compat wrinkle every time.
`hash`/`create`/`convert` are pre-reserved; other plausible verbs (`cat`,
`ls`?) are not.

**Recommend:** reserve `cat` now (it's in the design's own non-goals list as a
likely future), and add a sentence to the spec stating the policy: new verbs
may be added and take precedence over same-named files, `archivey list <path>`
is the permanent escape hatch.

## D7 — Exit-code flavor for the reserved surface

Reserved verbs (`create`, …) exit **2**; reserved `--salvage` and the `-` token
exit **1** (`CliError`'s default). All are "not available yet".

**Recommend:** 2 for all three (they're grammar-level "you can't ask for that
yet"), keeping 1 for operational failures on real archives. Cheap now; a
behavior change later.

## D8 — Wrong-password message (library, CLI-visible)

`archivey t enc.7z --password wrong` prints
`Password required to decrypt this 7z member` — the same message as supplying
no password at all. Users who *did* pass one will read this as "the flag was
ignored" (especially plausible while F1 exists…). Library-side message split
(`no password supplied` vs `password(s) rejected`), or CLI-side rephrasing from
`EncryptionError` context.

**Recommend:** library-side; the distinction exists internally (candidates were
consulted and exhausted vs. never present).
