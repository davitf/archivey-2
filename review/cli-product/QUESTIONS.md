# Maintainer decisions — CLI product review

Decisions this review surfaced that are not plain fixes. Q1–Q3 shape 0.2.0
user-visible behavior; Q4–Q6 can land after. #131's D1–D8 are settled and not
reopened.

## Q1 — Extraction continuation: reject-and-continue for the CLI? (drives P1)

Today `extract` inherits the library's `OnError.STOP`: the first rejected or
failed member raises, nothing after it is extracted, and the already-written
members are never reported. A traversal member means zero files extracted
under every `--policy` — `unzip` extracts the safe files and warns, so the
"safer unzip" is currently also the *less useful* unzip on exactly the input
the pitch is about. The CLI already has dead plumbing for the better story
(`rejected:`/`failed:` lines + summary counts, `cli/extract_cmd.py:315-336`).

Sub-decisions:

1. **Semantics**: should the CLI extract with record-and-continue for (a)
   policy rejections only, or (b) rejections *and* per-member read failures
   (digest mismatch, decode error), continuing where the stream allows?
   Recommend (b) — it is `test`'s semantics applied to extraction, and it is
   the VISION #3 claim.
2. **Mechanism**: CLI passes an `OnError`-style argument (library gains/
   exposes it), or the library default changes? Recommend: CLI-side opt-in
   via the existing library knob if it exists/is cheap; the library default
   staying STOP is fine for programmatic callers.
3. **Exit code**: completed-with-rejections = 1, or break out the reserved
   `3` ("refused by safety policy") now? The design already argued 3 is "a
   genuinely useful distinction for the safer-unzip story" and callers were
   told not to assume 1 is exhaustive, so it's compatible. Recommend: 3 for
   "completed but ≥1 member rejected by policy", 1 for other failures.
4. **Flag**: is a `--stop-on-error` opt-back needed at the same time, or
   later on demand? Recommend later.
5. Either way (even if STOP stays): on the stop path, report what *was*
   extracted before the stop (count at minimum). Today even `-v` says
   nothing.

## Q2 — `--json` timing and minimal schema (drives P4)

Design (Open Question 7) deferred `--json` pending a stable member schema —
correctly, for the *full* `ArchiveMember` model. But the CLI needs only the
fields it already prints: name, type, size, mtime, mode, encrypted,
link_target, hashes. Options:

1. Ship minimal `--json` (JSON-lines) on `list` + `info` in 0.2.0 with an
   additive-only promise on those fields.
2. First 0.2.x follow-up (recommended if 0.2.0 is time-boxed — but then say
   so in docs so the scripting audience knows it's coming and doesn't
   scrape).
3. Wait for the `hash` verb / full schema work.

Recommend 1 if any slack exists, else 2. Naming: `--json` (tool-standard) vs
`--porcelain` (git-ism) — recommend `--json`.

## Q3 — No-match filters: warning + which exit code? (drives P2)

Agreed shape (P2): stderr warning per unmatched include pattern; the open
question is the exit code when zero members matched on `extract`/`test`:

1. Warn, exit 0 (gentlest; scripts still blind).
2. Warn, exit 1 (recommended — "the operation did not do what was asked";
   matches `tar`'s nonzero).
3. Warn, dedicated code in the reserved ≥3 space (unzip's 11-style
   "no files matched"); most script-friendly, spends a reserved code.

And for `list` with no matches: silent-0 (grep-like it is not), or the same
warning with exit 0? Recommend warning + exit 0 for `list` (listing nothing
is an answer), warning + nonzero for `extract`/`test`.

Also: adopt the `(did you mean -d out?)` hint when a sole unmatched extract
pattern names an existing directory / ends with `/`?

## Q4 — Control-byte quoting style for member names (drives P3)

Escape in all CLI output (recommended) or only when the stream is a TTY?
Style: backslash-escapes (`\r`, `\x1b` — GNU-ish, lossless) vs U+FFFD
replacement (prettier, lossy)? Is a `--raw` escape hatch needed for the
pipe-to-script case, or does that wait for `--json` (where raw names are
naturally safe)? Recommend: escape everywhere, backslash style, no `--raw`
until someone asks — scripts get exact names via `--json` (Q2).

## Q5 — Should `info` tell the cost/access story? (drives P14)

`info` answers "what is this file" but not the library's signature "what
will it cost" claim. Minimal version: one derived `access:` line (e.g.
`random (indexed central directory)` / `sequential-only (solid; reading one
member decodes its folder prefix)` / `sequential (no gzip index; install
[seekable] for random access)`). Is that in-scope for the CLI, or does it
wait for a `CostReceipt`-shaped public surface in the library? The CLI can
derive today's version from `info.is_solid` + format + accelerator presence
without new public API.

## Q6 — A "what can this install read" view (drives P14)

Design Decision 10 listed `--version` as "version + optional dependency
matrix"; shipped `--version` prints only the version string. The per-failure
messages ("install the 'crypto' extra") are excellent but reactive. Options:
`--version -v`, `archivey info` with no argument, or a future `archivey
formats`/`doctor`. Any preference, or defer entirely? (Cheap and high-demo
value: the maintainer's own "why can't I open this" debugging tool.)

## Q7 — Two library-side message fixes surfaced here (P7, P9) — confirm owners

**Done (library-owned, as recommended):** truncated/corrupt zip prose (no
`BadZipFile` repr); `format=` / registry messages use `display_name`; `info`
uses `file_extension()` labels; STORED zipcrypto + provider-None → "Password
required"; rewind warning stays quiet below the rapidgzip AUTO size gate and
distinguishes install-vs-not-engaged when it does fire.
