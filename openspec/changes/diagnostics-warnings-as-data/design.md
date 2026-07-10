# Design — diagnostics: warnings as queryable data

This proposal is as much about *how to expose* advisories as *what to record*. The
maintainer's steer — "how they can be exposed, maybe it will depend on the context" — is
the organizing principle: there is no single right delivery, so the mechanism offers a few
and lets the caller's context choose.

## 1. The `Diagnostic` record

```python
@dataclass(frozen=True)
class Diagnostic:
    code: DiagnosticCode        # stable, machine-branchable (enum or str-enum)
    severity: DiagnosticSeverity  # INFO | WARNING (errors stay exceptions)
    message: str                # human-readable, formatted once
    # Typed, optional context — populated per code:
    member_name: str | None = None       # the member it concerns
    raw_name: bytes | None = None         # before/after for normalization
    normalized_name: str | None = None
    offset: int | None = None             # byte offset, for structural advisories
    detail: Mapping[str, object] = field(default_factory=dict)  # code-specific extras
```

The load-bearing field is **`code`**: a small, stable, enumerated set
(`NAME_NORMALIZED`, `DETECTION_CONFLICT`, `REWIND_COST`, `MEMBER_SKIPPED`,
`PASSWORD_GUESSED`, `INVALID_TIMESTAMP`, `DIGEST_UNVERIFIABLE`, `SCAN_ENTRY_VANISHED`, …).
Callers branch on `code`; `message` is for humans only. Stability of `code` is the API
contract, so codes get the same care as exception classes.

Naming is open: `Diagnostic` (compiler idiom) vs the maintainer's `Occurrence` vs
`ArchiveWarning`. "Diagnostic" reads well with a severity axis and avoids clashing with
Python's `warnings`. Flagged as an open question below.

## 2. Exposure is context-dependent — four channels

The same diagnostic can reach the caller four ways. Which one(s) fire is the crux, and it
depends on *where* the diagnostic arises and *who* is consuming.

### (a) Attached to the most specific natural surface
Where a diagnostic is *about* one object the caller already holds, it belongs on that
object — no lookup, no correlation. This is the C2/IDEAS sweep:

| Current `logger.warning` | Diagnostic code | Natural surface |
|---|---|---|
| name normalized (`naming.py`) | `NAME_NORMALIZED` | `ArchiveMember` |
| detection magic vs extension (`detection.py`) | `DETECTION_CONFLICT` | `FormatInfo` / `ArchiveInfo` |
| backward-seek rewind (`decompressor_stream.py`) | `REWIND_COST` | `CostReceipt` |
| member/hardlink skipped (`extraction.py`) | `MEMBER_SKIPPED` | `ExtractionResult` |
| invalid NTFS/DOS timestamp (`zip_reader.py`) | `INVALID_TIMESTAMP` | `ArchiveMember` |
| digest not checkable (`verify.py`) | `DIGEST_UNVERIFIABLE` | `ArchiveMember` / read result |
| guessed password (incoming) | `PASSWORD_GUESSED` | `ExtractionResult` / member |

### (b) Aggregated into a per-operation collection
A field-per-surface alone is not enough: some diagnostics have **no** per-object home
("a directory vanished during scan", "trailing bytes after EOF"), and callers often want
one question — *did anything noteworthy happen?* — answered without walking every member.
So the reader exposes `reader.diagnostics` (everything emitted during this open/read), and
`extract()` returns its diagnostics alongside the per-member `ExtractionResult`s. The
per-surface field and the collection are the **same** `Diagnostic` objects, referenced
twice, not duplicated data.

### (c) Pushed eagerly via a callback
For a long streaming pass or a large extraction, waiting until the end to inspect a
collection is wrong — the caller may want to react (or print progress) as diagnostics
occur. `ArchiveyConfig.on_diagnostic: Callable[[Diagnostic], None] | None` delivers each
one as it happens. This is also the natural bridge to a caller's own logging/telemetry.

### (d) Escalated to a typed error by policy
Some contexts want a diagnostic to be **fatal**. A reproducible-build tool wants "a name
was normalized" or "detection conflicted" to stop the build; a security-sensitive caller
may want `PASSWORD_GUESSED` to raise rather than proceed on an unconfirmed password. A
`WarningPolicy` maps codes/severities to a disposition — `IGNORE | COLLECT | RAISE` —
default `COLLECT` (+ log). `RAISE` turns the diagnostic into a typed exception at the
point it arises (see §5 for which exception).

### (e) …and logging stays
Every diagnostic is still logged on its existing `archivey.*` logger, so applications that
do nothing keep exactly today's behaviour. Logging is the projection, not the source of
truth.

## 3. Consumer archetypes (why one shape can't win)

- **CLI / interactive** — wants a human summary at the end (channel b), or streaming
  notices (c); verbosity is the user's.
- **Batch indexer** (the founding "index my backups" use case) — wants diagnostics
  recorded *beside each member* as data (a), tolerant, never a crash (policy `COLLECT`).
- **Reproducible-build / verifier** — wants selected codes to be hard errors (d), so a
  normalized name or a detection conflict fails CI (policy `RAISE` for a chosen set).
- **Library embedder / telemetry** — wants the eager callback (c) to forward into its own
  metrics/log pipeline.

The policy + callback + collection triple lets each pick without the others paying.

## 4. Configuration

```python
class ArchiveyConfig:
    on_diagnostic: Callable[[Diagnostic], None] | None = None
    warning_policy: WarningPolicy = WarningPolicy()   # per-code / per-severity disposition
```

`WarningPolicy` needs: a default disposition, and per-`code` overrides (e.g.
`{NAME_NORMALIZED: RAISE}`). Whether it also keys on `severity` or on logger name is an
open question. Default keeps today's behaviour (COLLECT + log; nothing raises that did not
already raise).

## 5. Relationship to errors — and the honesty rule

Diagnostics are **non-fatal advisories**; genuine failures remain exceptions (the
`error-handling` contract is untouched). Escalation (§2d) is the one bridge: a `RAISE`
disposition converts a diagnostic into an exception *at emission*. Open question: does it
raise the existing typed exception family (e.g. a normalization escalation → a
`FilterRejectionError`-like type) or a single new `DiagnosticRaisedError(code=…)`? A single
wrapper is simpler and keeps the code queryable; per-family raises integrate better with
existing `except` blocks. Leaning: a single `DiagnosticRaisedError` carrying the
`Diagnostic`, because the set of escalable codes is open-ended.

This also cleanly answers the `zip-multipassword-disambiguation` residual: "guessed
password" is a `PASSWORD_GUESSED` diagnostic (default COLLECT + log), and a
security-sensitive caller sets it to `RAISE` — no bespoke flag needed.

## 6. Aggregation, dedup, volume

"Occurrences" implies counting. A crafted or merely large archive can emit thousands of
`NAME_NORMALIZED` diagnostics — the collection must not become its own memory bomb (cf.
threat-model O1). Options: (i) cap the retained collection and expose per-code **counts**
beyond the cap; (ii) always keep full detail but document the cost; (iii) rely on the
callback for the unbounded case and keep the collection bounded. Leaning: a bounded
collection with exact per-code counts (so "127 names normalized, first N retained"), the
callback being the unbounded firehose. This interacts with the O1 listing-guard work and
should share its bound.

## 7. Open questions (for maintainer review)

1. **Naming**: `Diagnostic` vs `Occurrence` vs `ArchiveWarning`; `diagnostics` capability
   name.
2. **Escalation home**: a single `DiagnosticRaisedError` (leaning) vs per-family raises;
   and whether escalation belongs in this spec or `error-handling`.
3. **Aggregation bound**: retained-count cap + per-code counts (leaning) vs unbounded;
   shared bound with O1.
4. **`logging` vs stdlib `warnings`**: keep `logging` as the projection (leaning) or also
   emit `warnings.warn` for a subset? (`warnings` gives `-W error` interop but is noisy.)
5. **Scope of the first cut**: land the primitive + collection + callback + the
   normalization/detection/skip/guessed-password codes, and migrate the rest of the ~17
   sites incrementally — vs sweep all sites at once.
6. **Per-member field shape**: a list of `Diagnostic` on `ArchiveMember`, or a couple of
   promoted booleans/fields (`name_normalized: bool`) *plus* the list? Promoted fields are
   ergonomic for the common check; the list is complete.
