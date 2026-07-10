# Diagnostics: warnings as queryable data

## Why

Archivey's headline promise is **no surprises**. Today that promise quietly degrades to
"no surprises — but logged", because every advisory the library produces is a
`logging.warning` and nothing else. There are ~17 such sites: a member name was
normalized (its meaning changed vs `raw_name`), format detection's magic disagreed with
the extension, a backward seek forced an O(n) rewind, an extracted member was skipped, an
NTFS/DOS timestamp was invalid, an expected digest could not be checked, a directory
vanished mid-scan — and, incoming, "the password for this member was **guessed**"
(`zip-multipassword-disambiguation`). Most applications never configure the `archivey`
logger, so all of this is invisible: the data that would let a caller *act* (re-name the
file, reject the archive, warn the user, record provenance) is thrown away after
formatting a log string.

The threat model records this as gap **C2 ("warnings that should be data")** and
`IDEAS.md` sketches the sweep: each `logger.warning` should *also* be queryable as data
on the natural result object (`ArchiveMember`, `FormatInfo`/`ArchiveInfo`, `CostReceipt`,
`ExtractionResult`). But a field-per-surface sweep alone is fragmented and under-answers
the real question the maintainer raised: **how should these be exposed — and does the
right exposure depend on the context?** It does. A CLI wants to print them at the end; a
long streaming pass wants them as they happen; a batch indexer wants them recorded beside
each member and never wants a crash; a reproducible-build tool wants *some* of them to be
hard **errors**. One delivery shape cannot serve all four.

This change designs a single **diagnostic** primitive with **context-appropriate
exposure**: attached to the most specific natural surface, aggregated into a queryable
per-operation collection, delivered eagerly via an optional callback, and escalable to a
typed error by policy — with `logging` unchanged as the zero-config default so nothing
breaks. **Specs only — no code lands here.**

## What Changes

- **New capability `diagnostics`.** A stable `Diagnostic` record — a machine-stable
  `code`, a `severity`, a human `message`, and typed `context` (references to the member /
  archive / offset it concerns, and the before/after values where relevant, e.g.
  `raw_name` → `name`). Codes are enumerated and stable so callers can branch on them.

- **Context-appropriate exposure (the crux).** Every diagnostic is delivered through as
  many of these as fit its context, driven by config, not hard-coded:
  1. **Attached to its natural surface** — normalization → a field on `ArchiveMember`;
     detection conflict → `FormatInfo`/`ArchiveInfo`; rewind → `CostReceipt`;
     skip/guessed-password → `ExtractionResult`.
  2. **Aggregated** into a per-operation collection queryable from the reader
     (`reader.diagnostics`) and the operation result (`extract()`), so diagnostics with no
     natural per-object home (a directory vanished mid-scan) are still reachable, and so a
     caller can ask "did anything happen?" once.
  3. **Pushed eagerly** via an optional `ArchiveyConfig.on_diagnostic` callback, for
     streaming/long operations where waiting for a result object is wrong.
  4. **Escalated to a typed error** for a caller-selected set of codes/severities
     (`WarningPolicy`), so strict consumers can make "a name was normalized" fatal.
  5. **Logged** exactly as today — the existing `logging` behaviour is retained as the
     default channel, so this change is **additive and non-breaking**.

- **`logging` delta.** Clarify that each logged advisory is the log projection of a
  `Diagnostic`; the logger hierarchy and "no handlers by default" rule are unchanged.

## Impact

- New spec: `diagnostics`. Touched specs (light deltas, when scheduled): `logging`
  (relationship), and the surfaces that grow a diagnostics accessor — `archive-reading`
  (`reader.diagnostics`, `on_diagnostic`), `archive-data-model` (`ArchiveMember`
  normalization/diagnostic field), `format-detection` (`FormatInfo`/`ArchiveInfo`),
  `access-mode-and-cost` (`CostReceipt`), `safe-extraction` (`ExtractionResult`,
  `extract()` collection). To keep this proposal reviewable those attachment points are
  enumerated in the `diagnostics` spec and realized as tasks, not rewritten across five
  specs here.
- Consumers: the `zip-multipassword-disambiguation` residual ("guessed password") and the
  extraction collision work (O2) both want this surface; this is the shared dependency
  called out in that proposal.
- Backwards compatible: default behaviour still just logs; the data/callback/escalation
  channels are opt-in.
- Open decisions for maintainer review are collected in `design.md` (naming, whether
  escalation lives here or in `error-handling`, dedup/aggregation shape, `logging` vs the
  stdlib `warnings` module).
