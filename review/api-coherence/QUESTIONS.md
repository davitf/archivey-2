# QUESTIONS — maintainer decisions

> **Status (2026-07-18):** **Q1–Q6 decided** (recorded below). **Q7** deferred to a
> next review round (not this freeze pass). Triage / follow-up work: `../STATUS.md`.

Per the pause-and-ask rule (`CLAUDE.md`, `CONTRIBUTING.md`): discrepancies and
judgement calls surfaced, not silently resolved. Ordered by weight.

## Q1 — Duplicate-name members: unify `is_current`, and what do specs mean? (P1)

**Decision: (a) — unify.** Compute last-entry-wins `is_current` in all random-access
materializations; route exact-same-name duplicates through the non-current skip.
Update specs so `safe-extraction` and `archive-data-model` agree; make the
conformance sweep assert the uniform contract (drop the
`REPLACE if has_duplicates` dodge). Streaming-mode caveat stays documented (forward
pass cannot know supersession mid-yield).

### Already done? (checked 2026-07-18)

**No — not implemented for ZIP/TAR.** Current code:

| Backend | `is_current` |
|---|---|
| 7z | `compute_is_current(...)` in `sevenzip_reader` / `sevenzip_parser` |
| RAR | history rows get distinct `path;n` names + `is_current=False` |
| ZIP / TAR | never set — field defaults to `True` (`types.py`) |
| `base_reader` | no shared last-entry-wins pass |

So a duplicate-name ZIP/TAR still fails default extraction with `ExtractionError`
(O2 / `OverwritePolicy.ERROR`). The impression that this was already done is
understandable (7z has the helper; the specs already describe the skip) — but the
ZIP/TAR materialization path never grew the equivalent. Tracking fix: P1 in
`SUMMARY.md` / `parity.md`.

---

## Q2 — `members()` scope: include non-current by default? (maintainer's added question)

**Decision: yes — keep "everything" as the only listing behavior.** No
include/exclude argument. Invest in Q1 + docs + predicate recipes
(`m.is_current`, and `m.is_current and not m.is_anti` for extractable payload).
Visibility table in `safe-extraction` is settled on this reading.

Full analysis unchanged in `members-scope.md`.

---

## Q3 — RAR `listing_cost`: `INDEXED` or `REQUIRES_SCANNING`? (P2)

**Decision: keep `INDEXED`; fix the docstring / grab-bag prose that claim
otherwise.** Axis for the receipt: **what the caller pays after `open_archive`
returns**. Open always materializes the full member table today, so `INDEXED` is
the honest post-open receipt. Document the *actual* open-time walk (and that QO
is unused) in format/cost docs — do not invent a `REQUIRES_SCANNING` value the
caller never observes on `reader.cost`.

### Investigation (2026-07-18)

**How common is “no quick-open”?**

- **RAR 1.5 / 3 / 4:** QO does not exist → 100% of those archives are
  header-to-header.
- **RAR5:** QO is optional. WinRAR’s default (`-qo` / bare default) stores QO
  mainly for *relatively large* files and may omit small-file headers; `-qo+`
  stores all; `-qo-` stores none (RARLAB technote / 7-Zip FR #1537).
- **This repo’s fixtures:** 0/15 RAR5 fixtures contain a `QO` service name
  marker (all small / `-m0` / solid test archives from `scripts/gen_rar_fixtures.py`
  with no `-qo*` flag). 11 RAR3/4 fixtures never can. So the corpus is entirely
  “no usable QO,” which matches typical small-archive and non-WinRAR-default
  producers.

**What does the reader do today?**

- `parse_rar_archive` / `_parse_rar5` / `_parse_rar3` always walk
  header→packed-skip→header to EOF at open. Service blocks named `QO` are
  skipped (only `CMT` is special-cased). There is **no locator/QO fast path**.
- `listing_cost=INDEXED` is unconditional (`rar_reader.py`);
  `format-rar/spec.md` already describes an indexed backend that builds the
  member table up front.
- Member **data** (non-stored) shells out to a fresh `unrar p -n./member …`
  per open. That process re-parses the archive on the unrar side; native listing
  work is not reused by unrar.

**How hard would TAR-like lazy scan be?**

Non-trivial and low leverage for v0.2.0:

1. **API shape:** today’s RAR open fails closed on non-seekable sources and
   publishes `member_count` / full `_members` immediately. A TAR-style
   `REQUIRES_SCANNING` lazy iterator would need deferred materialization,
   streaming-mode semantics, and answers for `get()` / extract-prep /
   solid demux / multi-volume merge / `path;n` history — a real backend redesign,
   not a receipt tweak.
2. **QO fast path** (true layout-`INDEXED` without a full walk): parse main-header
   locator → seek to QO → decode cache structures, with mandatory
   list/extract same-path discipline (RARLAB security note). Default QO is often
   *partial*, so you still need a fallback header walk for omitted members.
   Parser work is real; security footgun if list and extract diverge.
3. **Payoff vs unrar:** list+stored-hash (founding dedupe path) *would* benefit
   from cheaper open on huge RAR5-with-QO archives. Extract/open-member workloads
   remain dominated by `unrar`’s own scan/decompress. Lazy native listing without
   QO is mostly “pay the same header walk later,” not “avoid it.”

**Conclusion:** keep always-upfront materialization for now → **`INDEXED` is
correct**. Fix `cost.py`’s `REQUIRES_SCANNING` docstring (drop the “RAR with no
quick-open record” example), align `docs/grab-bag/SPEC.md` if still cited, add a
RAR row to `test_cost_receipt.py`, and document in `docs/formats.md` /
`docs/costs.md`: open always walks headers; QO unused; unrar re-parses on data
open. Revisit QO-accelerated open only if huge-archive list latency shows up as
a real workload.

---

## Q4 — Approve the surface changes (S1/S3)

**Decision: blanket approve** as proposed in `surface.md`:

- Demote the 13 `*Context` classes + `RAPIDGZIP_AUTO_MIN_COMPRESSED_SIZE` from
  package `__all__` (remain importable from `archivey.diagnostics` / `config`).
- Export `PasswordInput`; export `OnDiagnostic` (symmetry with other public
  callback/param types).
- Collapse `MemberSelectorArg` into public `MemberSelector`.
- Drop `source_name` from `core.__all__`.
- Fill `api.md` gaps (`open_stream` at minimum; other listed gaps).

---

## Q5 — A `verify` primitive (E2): now or post-0.2.0?

**Decision: defer past 0.2.0.** Additive either way; not worth freezing a shape
before we know whether callers verify without extracting often enough to justify
a first-class API (CLI `test` can keep its hand-rolled loop for now). Park in
`IDEAS.md` / `../STATUS.md` future list; do not block the freeze.

---

## Q6 — Small freeze-list confirmations

| Item | Decision |
|---|---|
| **`WriteError`** | **Defer / remove from the read-only 0.2.0 surface.** v0.2.0 is read-only; writing is a later major release. Do not ship writing leftovers — demote/unexport `WriteError` for now. Same spirit: drop or stop advertising the `[7z-write]` extra/dep group until writing is real (py7zr stays a *dev* oracle as needed). |
| **`ExtractionStatus.SKIPPED` split (E3)** | **Split into distinct statuses** (not a `reason` field). Overwrite-skip and non-current-skip are different caller concerns: most tools ignore superseded members but care that an expected extract hit a pre-existing path. Name at implement (`SUPERSEDED` / `NON_CURRENT` / …) — prefer a clear verb/noun over overloading `SKIPPED`. |
| **`hashes` value convention** | **Convert `crc32` from `int` to `str`** (hex encoding; exact width/case fixed at implement — recommend lowercase, zero-padded 8 hex digits, no `0x`). No-surprises move; cheap pre-freeze. **Implementer note:** `blake2sp` is already `bytes` — converting only crc32 still leaves a mixed value type. Prefer aligning `blake2sp` to hex `str` in the same change so `hashes: Mapping[str, str]`, unless a follow-up objection lands. |
| **`ArchiveFormat` display name (S2)** | **Add a `display_name` property** (not a method). CLI stops parsing `repr()`. |

---

## Q7 — Partial members + honest error accessor (later-surfaced)

> **Surfaced later** (2026-07-18), during review of #149 (`decide-strict-archive-eof-default`
> Option F) — not part of the original api-coherence finding set in #133. Adjacent to
> **E2 / Q5** and to salvage in `IDEAS.md` / `../backlog.md`, but not the same question.

**Decision: leave for a next review round.** Do not explore or redesign in this
freeze pass. Option F’s interim contract (RA fail-closed / streaming
salvage-then-raise) stands; ownership of the VISION claim-(3) gap moves to the
next round / backlog (cross-link from EOF design remains fine).

---

## Decision → implementation map

| Decision | Follow-up (code/docs; not this PR unless noted) |
|---|---|
| Q1 (a) | Shared last-entry-wins on ZIP/TAR RA materialization; spec delta; sweep asserts |
| Q2 | Docs / recipes only once Q1 lands |
| Q3 | Fix `cost.py` docstring + receipt test + formats/costs prose |
| Q4 | Surface PR (demote/export/docs) |
| Q5 | `IDEAS.md` park only |
| Q6 WriteError / `[7z-write]` | Demote exception; remove or un-advertise extra |
| Q6 SKIPPED split | New `ExtractionStatus` value + CLI/report call sites |
| Q6 hashes → str | Type + backends + docs/formats matrix + CLI formatter |
| Q6 `display_name` | Property on `ArchiveFormat` + CLI |
| Q7 | Next round — see `../backlog.md` / STATUS future list |
