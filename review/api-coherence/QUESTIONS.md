# QUESTIONS — maintainer decisions

> **Status (2026-07-19):** **Q1–Q7 decided and implemented** (#153–#157), except
> **Q5** (verify primitive — deferred past 0.2.0, parked in `IDEAS.md`) and
> digest *filling* (OpenSpec `surface-stored-stream-digests`). This review is
> ready to archive once those two are parked. Triage: `../STATUS.md`.

Per the pause-and-ask rule (`CLAUDE.md`, `CONTRIBUTING.md`): discrepancies and
judgement calls surfaced, not silently resolved. Ordered by weight.

## Q1 — Duplicate-name members: unify `is_current`, and what do specs mean? (P1)

**Decision: (a) — unify.** Compute last-entry-wins `is_current` in all random-access
materializations; route exact-same-name duplicates through the non-current skip.
Update specs so `safe-extraction` and `archive-data-model` agree; make the
conformance sweep assert the uniform contract (drop the
`REPLACE if has_duplicates` dodge). Streaming-mode caveat stays documented (forward
pass cannot know supersession mid-yield).

### Already done? (checked 2026-07-19)

**Yes — implemented in #154.** Shared `_apply_last_entry_wins_is_current` in
`base_reader.py` runs for all random-access materializations (ZIP/TAR included);
`archive-data-model` / `safe-extraction` specs agree; corpus sweep asserts the
uniform contract. Regression: `tests/test_duplicates_is_current.py`.

*(Historical note from decision-recording day: before #154, only 7z/RAR set
`is_current`; ZIP/TAR defaulted to `True` and failed default extraction under
`OverwritePolicy.ERROR`.)*

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
| **`ExtractionStatus.SKIPPED` split (E3)** | **Split into distinct statuses** (not a `reason` field). **Implemented (#154 / #156):** non-current → `SUPERSEDED`; overwrite-skip → `NOT_OVERWRITTEN` (renamed from `SKIPPED`); safety-filter → `BLOCKED` (was rejected diagnostic). |
| **`hashes` value convention** | **Type cleanup (immediate review fix):** all values `bytes`; keys become `HashAlgorithm` (`CRC32` / `BLAKE2SP` / `ADLER32` stub OK). Today: `Mapping[str, int \| bytes]`. Target: `Mapping[HashAlgorithm, bytes]` (crc32 as 4-byte digest). Prefer `HashAlgorithm(str, Enum)`. Endianness of 4-byte digests: fix at implement (big-endian usual). **Filling missing digests (zlib Adler-32, lzip multi-member combine, …):** **out of this review’s code follow-ups** — tracked as OpenSpec change `surface-stored-stream-digests` (depends on the typing fix). |

### Q6 hashes — what formats store today / Adler-32 parity

**Currently surfaced** (only these two algorithms):

| Algorithm | Where |
|---|---|
| `crc32` | ZIP (CD), 7z (when present), RAR5 (when present), single-file `.gz` (single-member trailer), `.lz` (seekable trailer, single-member only today) |
| `blake2sp` | RAR5 only (HASH extra) |

Nothing else is exposed. Docs/specs explicitly say `.bz2` / `.xz` / **zlib** / brotli / `.Z` have no cheap whole-member digest — that line is **wrong for zlib**: RFC 1950 puts a 4-byte **Adler-32** of the uncompressed data at the end of every zlib stream (not CRC-32). Gzip uses CRC-32 in its trailer; raw ZIP deflate has neither (ZIP’s CRC lives in the directory).

**Can we fill `adler32` / multi-member lzip?** Yes — see OpenSpec change
**`surface-stored-stream-digests`** (separate from the type migration). Short
version: zlib peek last 4 bytes; lzip index already has per-member CRC+size and
can `crc32_combine`; gzip/xz multi-unit deferred.

### Q6 hashes — multi-member streams: single-only vs combine math

Full investigation lives in `openspec/changes/surface-stored-stream-digests/design.md`
(and was drafted here during api-coherence). **CRC-32 and Adler-32 are
combinable** given `(d1, d2, len2)`; SHA-256 is not. Best immediate win: **lzip
multi-member**. gzip mid-trailer acquisition and xz CRC64/SHA-256 remain the
hard parts — deferred in that change.

| **`ArchiveFormat` display name (S2)** | **Add a `display_name` property** (not a method). CLI stops parsing `repr()`. |

---

## Q7 — Partial members + honest error accessor (later-surfaced) — DONE (#157)

> **Surfaced later** (2026-07-18), during review of #149 (`decide-strict-archive-eof-default`
> Option F) — not part of the original api-coherence finding set in #133. Adjacent to
> **E2 / Q5** and to salvage in `IDEAS.md` / `../backlog.md`, but not the same question.

**Decision: implement via OpenSpec change `partial-members-and-errors`.** Dual listing
surface: `members()` / `scan_members()` stay complete-or-raise; `members_report()` →
`MemberListReport` always returns prefix + `error`; RA iteration aligns with streaming
(yield-then-raise). Single stored report model (completeness is `error is None`);
`get_members_if_available` renamed to `members_report_if_available`. Exception-carried
prefix rejected. Salvage / soft-extract / verify remain separate.

**Implemented in #157** (change archived as
`openspec/changes/archive/2026-07-18-partial-members-and-errors/`). CLI `list` uses
`members_report()` so recovered members print with an honest error.

---

## Decision → implementation map

| Decision | Follow-up | Status |
|---|---|---|
| Q1 (a) | Shared last-entry-wins on ZIP/TAR RA; spec delta; sweep asserts | **done #154** |
| Q2 | Docs / recipes | **done #154** |
| Q3 | Fix `cost.py` docstring + receipt test + formats/costs prose | **done #154** |
| Q4 | Surface PR (demote/export/docs) | **done #154** |
| Q5 | `IDEAS.md` park only | **parked** |
| Q6 WriteError / `[7z-write]` | Demote exception; remove extra | **done #154** |
| Q6 SKIPPED split | `SUPERSEDED` + later `NOT_OVERWRITTEN` / `BLOCKED` | **done #154/#156** |
| Q6 hashes typing | `HashAlgorithm` + `Mapping[..., bytes]` | **done #154** |
| Q6 hashes fill (zlib/lzip) | OpenSpec `surface-stored-stream-digests` | **open change** |
| Q6 `display_name` | Property on `ArchiveFormat` + CLI | **done #154** |
| Q7 | OpenSpec `partial-members-and-errors` | **done #157** |
