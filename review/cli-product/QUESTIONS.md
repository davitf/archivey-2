# Maintainer decisions — CLI product review

Decisions this review surfaced that are not plain fixes. Q1–Q3 shape 0.2.0
user-visible behavior; Q4–Q6 can land after. #131's D1–D8 are settled and not
reopened.

## Q1 — Extraction continuation: reject-and-continue for the CLI? (drives P1)

**Decided (2026-07-19):**

| # | Decision |
|---|----------|
| 1 Semantics | Record-and-continue for **policy rejections and per-member read failures** (digest/decode), continuing where the stream allows — same shape as `test` / VISION #3. |
| 2 Mechanism | CLI passes `OnError.CONTINUE` by default; **library default stays `STOP`**. |
| 3 Exit code | **`3`** when the run completed with ≥1 policy `BLOCKED` and no `FAILED`; **`1`** when any member `FAILED` (or hoist/always-stop); **`0`** when clean. |
| 4 Flag | **`--stop-on-error` now** — restores library `STOP` for that invocation (shell scripts cannot switch to the library API). |
| 5 Stop-path reporting | Always report what was written before an early stop (count at minimum). |

Rationale: without CONTINUE, `x evil.tar` extracts nothing while `unzip` delivers
the safe members — the safer-unzip demo loses. Exit `3` was reserved in cli-v1
Decision 12 for exactly this distinction. `--stop-on-error` keeps all-or-nothing
available at the shell.

Context that drove the call: today `extract` inherits `OnError.STOP`; the CLI
already has dead plumbing for `blocked:`/`failed:` lines + summary counts that
only fire under CONTINUE.

## Q2 — `--json` timing and minimal schema (drives P4)

**Decided (2026-07-19):** option **3** — wait for the `hash` verb / a designed
member schema. Do **not** ship a minimal `--json` in 0.2.0 or as a quick
0.2.x follow-up. Scripting remains human-column only until that design lands;
prefer designing the stable contract once over an additive-but-provisional
JSON-lines surface. Flag name when it lands: **`--json`** (not `--porcelain`).

Context (options considered): (1) minimal JSON-lines on `list`/`info` in
0.2.0; (2) first 0.2.x with a "coming" note in docs; (3) wait for full schema.
Rationale: lower priority than Q1/Q3 product traps; worth designing right.

## Q3 — No-match filters: warning + which exit code? (drives P2)

**Decided (2026-07-19):**

| Piece | Decision |
|-------|----------|
| Warning | stderr per unmatched include pattern: `warning: pattern matched no members: '…'` |
| `extract` / `test` zero matches | warn + exit **1** (not a dedicated ≥4 code — exit 11 is unzip/PKWARE-only, not a broader convention) |
| `list` zero matches | warn + exit **0** (listing nothing is a valid answer) |
| Dest hint | When a sole unmatched extract pattern names an existing directory or ends with `/`, add `(did you mean -d PATTERN?)` |

Rationale: silence-as-success on `x a.zip out` / `x a.zip project` is the muscle-memory trap; exit 1 matches `tar`'s nonzero. Spending another reserved code on unzip's 11 would be ZIP-specific mimicry next to our `0/1/2/3` map.

## Q4 — Control-byte quoting style for member names (drives P3)

Escape in all CLI output (recommended) or only when the stream is a TTY?
Style: backslash-escapes (`\r`, `\x1b` — GNU-ish, lossless) vs U+FFFD
replacement (prettier, lossy)? Is a `--raw` escape hatch needed for the
pipe-to-script case, or does that wait for `--json` (where raw names are
naturally safe)? Recommend: escape everywhere, backslash style, no `--raw`
until someone asks — scripts get exact names via `--json` (Q2).

**Partial lean (P3 landed):** escape everywhere, backslash style, no `--raw`
yet — matching the recommendation. With Q2 deferred (no `--json` until
`hash`/schema), Q4 remains open only if we later want TTY-only or a `--raw`
hatch for scripts that need exact names before machine output exists.

## Q5 — Should `info` tell the cost/access story? (drives P14)

**Decided (2026-07-19):** ship now. `archivey info` prints an `access:` line
derived from the existing `ArchiveInfo.cost` / `CostReceipt` (no new library
API). With `-v`, also print the raw axes (`listing`, `access_cost`, `stream`,
`solid_blocks`). Accelerator install/AUTO-gate state stays out of this line
(not on the frozen receipt; can extend later).

## Q6 — A "what can this install read" view (drives P14)

**Decided (2026-07-19):** **`--version -v`** (not bare `info`). Prints
`archivey <version>` then a `formats:` matrix from `list_known_formats()` /
`format_availability()` (support level + missing install hints). Plain
`--version` stays one line.

## Q7 — Two library-side message fixes surfaced here (P7, P9) — confirm owners

**Done (library-owned, as recommended):** truncated/corrupt zip prose (no
`BadZipFile` repr); `format=` / registry messages use `display_name`; `info`
uses `file_extension()` labels; STORED zipcrypto + provider-None → "Password
required"; rewind warning stays quiet below the rapidgzip AUTO size gate and
distinguishes install-vs-not-engaged when it does fire.

## Q8 — `--stop-on-error` + policy block: exit `3` or `1`? (PR #163 review)

**Open.** Q1 decided exit `3` when extract *completed* under CONTINUE with
≥1 `BLOCKED` and no `FAILED`. The STOP path currently also returns `3` for a
`FilterRejectionError` (first blocked member aborts). Spec only requires STOP
→ “exit nonzero”; tests lock in `3`.

| Case | Mode | Outcome on disk | Current exit | Suggested (option A) | Suggested (option B: keep) |
|------|------|-----------------|--------------|----------------------|----------------------------|
| Clean extract | CONTINUE (default) | all OK | `0` | `0` | `0` |
| ≥1 `BLOCKED`, no `FAILED` | CONTINUE | safe members extracted; blocked reported | `3` | `3` | `3` |
| ≥1 `FAILED` (and any blocks) | CONTINUE | recoverable extracted; failures reported | `1` | `1` | `1` |
| First member policy-blocked | `--stop-on-error` | nothing after the block; 0+ earlier OK | `3` | **`1`** | `3` |
| First member read/failed | `--stop-on-error` | nothing after the failure | `1` | `1` | `1` |
| Policy block after N OK | `--stop-on-error` | N written; remainder skipped | `3` | **`1`** | `3` |
| Always-stop (bomb / diagnostic raise) | either | partial; hard stop | `1` | `1` | `1` |
| Hoist collision failure | CONTINUE | partial layout | `1` | `1` | `1` |
| Unmatched includes / nothing selected | — | nothing extracted | `1` | `1` | `1` |
| Usage / argparse | — | — | `2` | `2` | `2` |

**Option A (review lean):** reserve `3` for “finished with policy refusals”
(CONTINUE completed). STOP is always `1` — scripts can treat `3` as partial
success with blocks, and `1` as aborted/failed.

**Option B (status quo):** `3` means “a policy refusal was involved” whether
or not the run completed; STOP+policy stays `3`. Simpler message (“policy
touched the run”) but `3` no longer implies remaining members were extracted.
