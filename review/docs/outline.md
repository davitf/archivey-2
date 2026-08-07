# Outline of the final user guide

The worklist for phase 3's second change (the page splits) **and** the starting
point for Topic 8 (content). Written between the two, deliberately: outlining
before the moves would have meant outlining a tree about to lose 80% of its files;
outlining after the splits would be too late to move a page boundary cheaply.

Measured against `main` @ `d34489f`, after `docs-ia-unpublish-maintainer-tree`
landed. Every source citation is a live `file:lines` in that tree — so **Sources** rows
name `docs/safe-extraction.md`, which D-d later renamed to `docs/extracting.md`. The
citations are left as measured; renaming them would break the line numbers.

| Field | What it is for |
|---|---|
| **Purpose** | one sentence; if a section does not serve it, it belongs elsewhere |
| **Reader question** | the thing someone types into the search box before they land here |
| **Sections** | in order, with the main points each must make |
| **Not here** | what a reader might expect and will not find, and where it lives instead |
| **Sources** | `file:lines` to move in. Blank means new prose — the Topic 8 worklist |

---

## Proportions, and the denominator that makes them mean anything

The independent pass argued safe extraction should be ~25% of a guide and
access/cost ~20%, against our 6.3% and 10.4% (`brief.md:179-197`). Those targets
were never percentages of *everything published* — its own outline excludes the
generated API reference and has no migration or platform page, because it derived
from `src/` and `tests/` and had no evidence base for either
(`independent/proposed-outline.md:1-6, 136-153`).

So there are two denominators, and only one of them is comparable:

| | Lines | extracting | access-and-cost |
|---|---:|---:|---:|
| **Core teaching pages** — install, opening, reading, gotchas, extracting, access-and-cost, formats, errors, cli | ~1,255 | **22.3%** | **18.3%** |
| All published pages, incl. migrating / support-matrix / philosophy / api / acknowledgements | ~2,085 | 13.4% | 11.0% |

Against the comparable denominator the target shape lands where the independent
pass argued it should: extracting within ~2 points of ~25%, access/cost within
~2 points of ~20% once the config screen is counted — not the ~10-point and
~15-point gaps the raw comparison suggested. **This is not a reason to relax**: the
extracting figure assumes the ~90 lines of new prose in §5 below actually get
written. Merging alone gets it to ~200 lines / 17%, which is
[`page-shape.md`](page-shape.md) §1's own estimate.

Projected sizes, for sequencing rather than as targets:

| Page | Now | Projected | Shape |
|---|---:|---:|---|
| `index.md` | 57 | ~85 | + 30-second recipes |
| `install.md` | — | ~70 | **new**, split from `usage.md` + new matrix |
| `opening-and-listing.md` | — | ~105 | **new**, split from `usage.md` |
| `reading-members.md` | — | ~125 | **new**, split from `usage.md` + ADR 0014 |
| `gotchas.md` | 155 | ~70 | shrink to a digest |
| `extracting.md` | 93 | ~280 | grow ~3× |
| `access-and-cost.md` | 154 | ~230 | rename + absorb + config screen |
| `formats.md` | 185 | ~215 | two fixes + the dedupe recipe |
| `errors-and-diagnostics.md` | — | ~90 | **new** |
| `cli.md` | — | ~70 | **new**, split from `usage.md` |
| `migrating.md` | 174 | 174 | unchanged |
| `support-matrix.md` | 152 | 152 | unchanged |
| `philosophy.md` | 79 | 79 | unchanged |
| `how-it-works.md` | — | ~150 | **new**, all new prose (D2) |
| `api.md` | 90 | 90 | unchanged |
| `acknowledgements.md` | 98 | 98 | unchanged |

---

## 1. `index.md` — Home

**Purpose.** One screen that says what Archivey is, shows it working, and routes.

**Reader question.** "Is this the library I want, and where do I start?"

**Sections.**

1. One-paragraph what-it-is + the six-line list example. Unchanged.
2. **Thirty-second recipes.** Four copy-paste blocks: list members · extract safely ·
   stream one member · read a pipe with `streaming=True`. One screen, each linking to
   the page that owns it. **Keep it tiny** — the moment it grows a fifth block it is
   becoming the next `usage.md`.
3. Highlights — seven bullets. Content unchanged, **three links repointed**:
   `extras → install.md` (`index.md:21`), `access costs → access-and-cost.md`
   (`:27`), `exception hierarchy → errors-and-diagnostics.md` (`:29`).
4. User guide — renumbered for the new page set and order.
5. For contributors — the repo pointer block, already rewritten.

**Not here.** The pitch (that is `README.md`, and `philosophy.md` for the long
form). Anything a reader has to scroll for. Any recipe that needs explaining.

**Sources.** `docs/index.md:1-57`.
**New:** §2 entirely (~30 lines).

> **Why §2 exists.** The independent pass made "30-second recipes" its page 1 after
> install (`independent/proposed-outline.md:23-31`) on the grounds that first-hour
> abandonment happens when people never see the safety defaults and reach for the
> stdlib instead. Today Home shows *listing* and nothing else, so the safe-by-default
> claim appears as a Highlights bullet and never as code. `migrating.md` covers the
> same ground for people arriving from `zipfile`; this covers everyone else.

---

## 2. `install.md` — Install and extras **(new)**

**Purpose.** Make "I `pip install`ed it and RAR didn't work" impossible to reach.

**Reader question.** "What do I have to install for *my* format?"

This is the independent pass's #1 predicted abandonment
(`independent/proposed-outline.md:9-20`), and today install is 16 lines at the top
of a page called "Basic usage" while the format × extra × tool answer is spread
across three other pages.

**Sections.**

1. **The four extras**, with what each buys and why there is no per-format extra —
   member codecs are shared across containers, so a format name would be the wrong
   thing to install.
2. **The format × extra × external-tool matrix.** One table, the single answer to
   the reader question. Built from `formats.md`'s quick matrix, but organised by
   *what you install* rather than by format.
3. **`format_availability()` — ask the installed library.** The function exists and
   no page is built around it. Show its output, and that a format stays *known*
   when its dependency is absent: single-codec formats report `NONE`, multi-codec
   containers report `PARTIAL`, and both carry install hints. (must-explain #15 —
   currently undocumented anywhere.)
4. **RAR needs the RARLAB `unrar` binary**, and no pip extra can supply it.
   `unrar-free` / `unar` / `7z` are not substitutes. Listing works without it;
   reading bytes does not. (must-explain #14.)
5. **Free-threaded builds** — one paragraph and a link; `support-matrix.md` owns
   the detail.

**Not here.** Per-format quirks (`formats.md`). The free-threaded wheel matrix
(`support-matrix.md:60-97`). Why each library was chosen (`how-it-works.md`).

**Sources.** `docs/usage.md:3-18`; `docs/formats.md:6-27`;
`docs/acknowledgements.md:57-73`; `docs/support-matrix.md:60-80`.
**New:** §3 entirely; the matrix is a re-cut, not a move.

> **Sequencing (O-13).** `consolidate-optional-extras` shipped in #212, so the four
> extras above are current. Write against them, not against the older eleven.

---

## 3. `opening-and-listing.md` — Opening and listing **(new)**

**Purpose.** Point Archivey at a thing, get past whatever guards it, and find out
what is inside.

**Reader question.** "What's in this archive?"

**Sections.**

1. **Open and list.** `open_archive`, iteration, `members()`, `get()`,
   `reader.format` / `reader.cost`. Random access is the default.
2. **Sources.** Paths, file objects, directories, byte sequences. Three things a
   signature does not say: a **seekable stream is assumed to start at its current
   `tell()`** (must-explain #12); **only 7z and RAR accept multi-volume
   sequences**, a length-1 sequence is a scalar, and a directory path resolves to the
   directory backend — with a conflicting `format=` *rejected* rather than overruled
   (must-explain #25).
3. **Detection.** `detect_format`, magic-before-extension, confidence and evidence.
   A conflict means **magic wins and `FORMAT_EXTENSION_CONFLICT` fires** — name the
   diagnostic (must-explain #22). `open_stream` vs `open_archive` on the same
   `.gz`, and the inner-TAR upgrade (must-explain #21).
4. **Passwords.** Single, list, `PasswordProvider`. Order matters. A password on a
   format without encryption is accepted and recorded as `PASSWORD_ARGUMENT_UNUSED`
   rather than raising, in every form (must-explain #13); the ZipCrypto STORED trap is
   a `gotchas.md` line pointing at `access-and-cost.md`.
5. **Damaged archives.** `members()` / `scan_members()` complete-or-raise;
   `members_report()` for prefix + error; `__iter__` / `stream_members` yield then
   raise. Not salvage. Carries a "see Errors and diagnostics" callout, since that
   page sits later in the nav than the first failure a reader meets.
6. **Duplicate names and `is_current`.** Last-entry-wins, `SUPERSEDED` on extract,
   the filter one-liners.

**Not here.** Reading bytes out of a member (`reading-members.md`). What a listing
*costs* — `ListingCost`, listing limits (`access-and-cost.md`, `extracting.md`).
Per-format listing quirks (`formats.md`). The dedupe recipe, which moved to
`formats.md` beside the stored-digest matrix it depends on.

**Sources.** `docs/usage.md:20-55` (§1, §5); `:95-100` (§3); `:145-173` (§6);
`:175-183` (§4).
**New:** §2 entirely, the named diagnostic in §3, the §5 callout (~25 lines).

---

## 4. `reading-members.md` — Reading members **(new)**

**Purpose.** The contract for getting bytes out, and what each outcome means.

**Reader question.** "How do I read one file out of this, and can I trust what I
got?"

Carries the most currently-undocumented behaviour of any page: five of the nine
must-explain gaps land here.

**Sections.**

1. **Read a member.** `open()` / `read()`, forward-only and one-live by default.
   `read()` is **all-or-raise and unbounded** — it has no size guard, which is a
   memory-bomb surface on untrusted input (must-explain #24). The chunked-loop
   recipe for recovering a truncated prefix. Carries the second "see Errors and
   diagnostics" callout.
2. **The integrity guarantee.** Content faults raise from `read`, never from
   `close`; the call × failure matrix. The load-bearing asymmetry:
   `read(member.size)` raises on corruption but returns a **short buffer** on
   truncation — known-wrong bytes are withheld, an apparent incomplete prefix is
   delivered. "No exception" does not mean "complete." This contract's only copy
   today lives inside a 615-line ADR (D5).
3. **`stream_members()`.** Lifetime — the yielded stream is valid **only until the
   iterator advances** — and laziness: open, decompress and password errors surface
   on **first read**, not on yield, so "I iterated everything so passwords were
   checked" is false (must-explain #10). Both undocumented today.
4. **Streaming mode.** `streaming=True`, the single forward pass, what raises in
   that mode. Cross-link the cost consequences rather than restating them.
5. **Identity and lifetime.** `member in reader` is **identity, not name** — a
   string raises `TypeError` rather than falling back to iteration, which would
   consume a streaming pass (must-explain #19). A member from another reader raises.
   **Closing the reader does not invalidate already-open streams** (must-explain
   #20). **Non-file members cannot be `open()`ed** (must-explain #26). All four
   undocumented today.
6. **One-shot extract**, in three lines and a link. `archivey.extract(src, dest)`
   is `extracting.md` §1's material — what belongs *here* is why it has no
   `members=` (selecting a subset needs the member list, which would force
   open/list/reopen — must-explain #5) and that it **auto-opens streaming for a
   non-seekable source** while `open_archive` refuses one, the inconsistency users
   hit first (must-explain #4, undocumented).

**Not here.** Enumerating an archive (`opening-and-listing.md`). What an access
pattern *costs* (`access-and-cost.md`). Extraction policy (`extracting.md`).
The exception tree (`errors-and-diagnostics.md`).

**Sources.** `docs/usage.md:57-83` (§1); `:102-111` (§4); `:85-93` (§6, reduced to a
cross-link); `dev-docs/decisions/0014-integrity-verdicts-from-reads-not-close.md:320-375`
(§2).
**New:** §3, §5, and §6's streaming note (~35 lines).

> **Why this is two pages and not one.** An earlier draft had a single
> `reading.md`. Tallied by section it came to **268 lines** — `usage.md`'s own size
> (274), the page being split for being too big. The sections divide 133/135 between
> "what's in this archive" and "give me the bytes" with almost no overlap, which is a
> joint rather than a cut. `usage.md` failed by doing five *jobs*; a single
> `reading.md` would do two. Each of these does one.
>
> Two sections were misfiled regardless of the split and are relocated with it: the
> **dedupe recipe** (31 lines) to `formats.md`, beside the stored-digest matrix it is
> *about*; and **one-shot extract** (9 lines), whose code block duplicates
> `extracting.md` §1 and whose unique content is three lines of rationale.
>
> Three seams rub, and each resolves the same way the `access-and-cost.md` boundary
> already did — contract here, consequences there:
>
> | Friction | Resolution |
> |---|---|
> | `stream_members()` yields `(member, stream)` — both halves | **Reading**: its hard parts are stream lifetime and laziness, which are stream contract, not enumeration |
> | `streaming=True` affects listing and reading alike | Contract on **reading**; `access-and-cost.md` owns the consequences |
> | "Open and list" straddles by name | Split by reader question, not by call name: opening is how you *get* a listing |
>
> **Consequence for the splits delta:** `documentation/spec.md:86-87` requires Gotchas
> to sit immediately after "basic usage". With two pages the delta must name which —
> **`reading-members.md`**, since that is where the traps the digest indexes actually
> live.

## 5. `extracting.md` — Extracting

**Purpose.** The page without which the library cannot be used safely on untrusted
input. Becomes the guide's largest page.

**Reader question.** "What does 'safe by default' actually block, and what does it
not?"

`VISION.md:26` makes this claim #1; `openspec/specs/safe-extraction/spec.md` is 809
lines, the largest in the tree; the page is 93 lines and its deepest sentence about
trust boundaries is a link *out of the guide*.

**Sections.**

1. **One-shot**, and the defaults spelled out.
2. **Trust boundaries.** What is trusted (the destination path you pass) versus what
   is not (every byte of the archive). Currently only written in an unpublished
   maintainer page. **This is where D3's remaining repo link gets dropped.**
3. **What is enforced.** The existing bullet list, plus the depth from the threat
   model: the **three-layer symlink defence** (lexical check, parent resolution,
   post-create re-resolution), extraction-root overwrite rejection, permission
   hygiene, and the atomic temp + `os.replace` write semantics.
4. **Policies.** `STRICT` / `STANDARD` / `TRUSTED`, and what each does *not* relax —
   `TRUSTED` still runs every universal path, symlink and special-file check
   (must-explain #17).
5. **`OnError` is about failures, not blocks.** A policy `BLOCKED` is always
   recorded and always continues, under `STOP` and `CONTINUE` alike. The single most
   misread knob on the page (must-explain #6).
6. **Names change on disk.** `STRICT` strips trailing dots and spaces and
   percent-escapes non-UTF-8 bytes; reserved names and `:` are rejected on **every**
   OS; case and NFC/NFD collisions are deliberate on every OS. `requested_path` and
   `EXTRACTION_NAME_SANITIZED` are how you see it.
7. **Overwrite.** `ERROR` / `REPLACE` / `SKIP` / `RENAME`. `REPLACE` unlinks then
   creates — it **never writes through** a pre-existing symlink.
8. **Limits and bombs.** `ExtractionLimits` vs `ListingLimits`, the actual defaults
   (2 GiB, ratio 1000 after 5 MiB, ~1M entries), that bomb trips **halt the run even
   under `CONTINUE`** (must-explain #7), and that `stream_members()` is unguarded by
   design. Also: a looser `config=` passed to `extract_all` **cannot** raise a
   listing ceiling set at open (must-explain #8, undocumented).
9. **Hardlinks and filters.** Excluding a hardlink's source orphans the link;
   seekable sources recover it in a second pass and write the content at the *link*
   path, forward-only sources cannot (must-explain #18).
10. **Nested archives.** Recursion is caller-driven and the bomb tracker is **not
    nesting-aware** — it checks expansion for individual archives. Bound depth and
    total size yourself; a worked recipe (threat-model O6).
11. **Hardening notes for callers.** Accelerators are not the defended fuzz surface;
    `unrar` is inside your trust boundary; extract to a scratch directory and promote.
    Currently in `SECURITY.md`, which GitHub renders for vulnerability reporters
    (O-7).
12. **What is out of scope.** Concurrent hostile modification of the destination
    during extraction; metadata fidelity (xattrs / ACLs / forks).

**Not here.** The extraction *report* API shape (`api.md`). Diagnostics as a
mechanism (`errors-and-diagnostics.md`).

**Sources.** `docs/safe-extraction.md:1-93`; `docs/gotchas.md:103-126` and
`91-102`; `dev-docs/threat-model.md:9-58` (§2, §3) and `:186-193` (§10);
`SECURITY.md:68-89` (§11).
**New:** the §10 bounded-recursion recipe, the "what `TRUSTED` does not relax"
framing in §4, and the §8 config-ceiling rule. ~90 lines — this is the gap the
merge cannot close, and the reason the 23.8% above is a plan rather than a fact.

---

## 6. `access-and-cost.md` — Access costs and pitfalls

**Purpose.** What each access pattern costs, and which knob to reach for.

**Reader question.** "Why is this slow, and what do I pass to make it not slow?"

**Sections.**

1. **Read `reader.cost`.** The four fields; cost describes what you *pay*, never
   what is *legal*.
2. **Access modes.** `streaming=False` vs `True`, what each refuses. `streaming` and
   `concurrent_members` are mutually exclusive (must-explain #3). Random-access open
   on a pipe fails rather than buffering.
3. **Solid archives and open order.** One forward pass; named `open()` may restart a
   block. `concurrent_members=True` makes overlapping streams *correct* and does
   **not** remove solid open-order cost (must-explain #11).
4. **Seeking inside compressed members.** Why seek is off by default; native indexes
   for XZ/lzip; rapidgzip for gzip/zlib/deflate/bzip2; the backward-seek
   re-decompress and its diagnostic.
5. **Accelerators.** `AUTO` falls back **silently**; `ON` raises
   `PackageNotInstalledError` — the loud/quiet split is undocumented today
   (must-explain #16). The 1 MiB AUTO threshold and why it exists.
6. **Concurrent member streams.** Materialize once, then fan out. Reader-wide passes
   stay single-owner.
7. **Streaming mode is one pass.** Including after an early `break`; `scan_members()`
   to drain.
8. **Passwords and confirmation cost.** The ZipCrypto STORED niche; 7z key
   derivation per candidate.
9. **Measurement.** `io_stats()` returns `None` unless the archive was opened inside
   `enable_measurement()` — opt-in *and* open-scoped, which is why counters read
   zero (must-explain #28, undocumented).
10. **Config at a glance.** Every `ArchiveyConfig` / `ExtractionLimits` /
    `ListingLimits` field, its default, and one line on when to change it — with each
    row linking to the page that *teaches* it rather than restating the teaching.
    Covers the knobs that currently have no reference home: `use_rapidgzip` /
    `AcceleratorMode`, `strict_archive_eof`, `zip_unflagged_fallback_encoding` and
    `encoding=`, `listing_limits`, diagnostic policy and retention.
11. **Wall-time bands.** Aspirational, with the measured column. **Fix the
    `davitf/archivey-2` link** (O-4).
11. **Checklist.** The situation → knob table. Unchanged; it is the best thing on
    the page.

**Not here.** Accelerator *process* risk (`gotchas.md`, one line, linking to
`known-issues.md`). The stream contract itself (`reading-members.md`).

**Sources.** `docs/costs.md:1-154` (renamed); `docs/gotchas.md:13-25` and `27-37`
(the cost half, absorbed as the digest shrinks).
**New:** §5's ON-vs-AUTO split, §9, §10 (~35 lines).

> **The weakest placement call in this outline.** The knobs do not belong to one page:
> cost knobs are here, limits and policies are `extracting.md`, `strict_archive_eof`
> and encoding are `formats.md`, diagnostic policy is `errors-and-diagnostics.md`. A
> single dense screen is a *reference* artifact, and this page is the one whose subject
> is "what you pay and what you pass", which is the closest fit — but the independent
> pass wanted a standalone configuration page at ~4%
> (`independent/proposed-outline.md:115-122`), and that is a defensible alternative.
> Flagged for the maintainer rather than settled here; the section's *content* is the
> same either way.

---

## 7. `gotchas.md` — Gotchas

**Purpose.** The "read this next" digest. One line per trap plus a link to the page
that owns it. Not a third copy of anything.

**Reader question.** "What is going to bite me that I would not think to ask?"

**The inclusion rule is normative for this page (D4):** a topic belongs here only if
(a) a caller choice is likely to cause a mistake or a footgun, or (b) Archivey
cannot fulfil its intention of failing loudly and verifying. Format encyclopaedia,
unsupported-feature lists, full policy tables and "plan around this limitation" rows
belong to the owning page.

**Sections — two, and only two.**

1. **What you should / shouldn't do.** Seek/redecompress · solid open order ·
   streaming is one pass · `get()` last-wins and `extract_all(members=["x"])`
   matching every `x` · STRICT rewrites names and collides case-insensitively (one
   bullet) · don't close a source under a live accelerator stream · accelerators off
   for untrusted input under a latency budget.
2. **What you should be aware of.** 7z AES store/copy with no integrity anchor ·
   7z header-encryption residual (garbage that parses as a plausible non-empty header
   can still slip — O8) · TAR residuals (trailer-less warns; streaming final header) ·
   bare gzip/zlib + rapidgzip best-effort truncation · `.Z` zero-leftover silent cuts ·
   `import archivey` patches pycdlib process-globally · a short "we differ from stdlib
   on corruption handling" orientation.

**The four threat-model residuals D8 routes here** (`DECISIONS.md` D8 §2), each one
line linking to the page that owns the depth:

| Residual | Section | Line |
|---|---|---|
| **O6 nested archives** | should/shouldn't | The bomb tracker checks expansion for *individual* archives and is **not nesting-aware**. Recursion is caller-driven — bound depth and size yourself. → `extracting.md` §10 |
| **O1 unguarded paths** | should/shouldn't | `stream_members()` is not covered by `ListingLimits`, and `read()` / `open()` stream sizes are unbounded — chunk untrusted payloads. → `extracting.md` §8, `reading-members.md` §1 |
| **O8 7z header encryption** | be aware of | Above. |
| **O2 name collisions** | should/shouldn't | Already covered by the STRICT-rewrite bullet; collision behaviour is OS-dependent by design (ADR 0013). |

Accelerator hang is already carried by the "accelerators off under a latency budget"
bullet in §1.

**Explicitly out** (D4 triage, decided): ZIP/ISO needing seek · multi-volume ZIP ·
ZIP UTF-8 bit-11 · the format-limitations table · the full policy table · listing
completeness vs `members_report` · the "what we can only warn about" meta section.

**Not here.** Everything above, each on its owning page.

**Sources.** `docs/gotchas.md`, reduced from 155 lines to ~65.
**Rewrite required:** the accelerator bullet. `_TrappingSource` contains Bug 3 — the
fault becomes a benign EOF toward rapidgzip and archivey re-raises. The page must
not say "the process dies" (D9 / O-15).

> **Spec conflict, still open.** `openspec/specs/documentation/spec.md:178-193`
> requires Gotchas to cover multi-volume ZIP, ZIP/ISO seek, UTF-8 bit-11 and TAR
> silent-shorten. D4 puts that quartet out of Gotchas. **The splits change must
> rewrite or drop that requirement** — until it does, the page and the spec
> disagree, and "`formats.md` covers it" is not a reading the spec supports.

---

## 8. `formats.md` — Formats and extras

**Purpose.** Per-format capability, quirks, and what each needs.

**Reader question.** "Why did this format do that?"

**Sections.** Quick matrix · ZIP · TAR · 7z · RAR · ISO · Directory · single-file
compressors · stored digests **+ the cheap-dedupe recipe** · detection. Otherwise
unchanged; it is a good page.

One addition and two fixes.

**Addition — the dedupe recipe joins the stored-digest matrix.** The recipe keys on
`member.hashes` and falls back to computing a digest while reading; the matrix that
says which formats populate `member.hashes` at all is the section directly above it.
They were on different pages, which is why a reader could follow the recipe without
ever learning that TAR and bzip2 return nothing from it.

Two fixes:

- **O-2 — still open.** `formats.md:137` says the rapidgzip truncation backstop
  covers a **path** `.gz`. `openspec/specs/seekable-decompressor-streams/spec.md:122-124`
  says **any declared-seekable source** — "a path or a caller-owned `BinaryIO` alike —
  not only path sources". `gotchas.md:87` already states it correctly, so the same
  fact is written two ways on the published site today. The prose is behind the spec,
  not in conflict with it; no decision to make. (`dev-docs/open-issues.md:133` carries
  the same stale wording, now unpublished and lower priority.)
- **ISO:** state the pycdlib process-global deque patch here, where a reader looking
  at ISO will find it, rather than only as a `gotchas.md` line (must-explain #29).

**O-14 is already fixed** — verified, not assumed. All three copies that tied
BLAKE2sp to an extra now say it needs no package: `formats.md:16`, `formats.md:105`,
`acknowledgements.md:73`. `consolidate-optional-extras` (#212) fixed the published
pages alongside the `pyproject.toml` comment, which is what O-14 asked for.

**Not here.** Cost consequences (`access-and-cost.md`). What to install
(`install.md`, which owns the matrix by extra).

**Sources.** `docs/formats.md:1-185`; `docs/usage.md:113-143` (the dedupe recipe);
plus the quartet D4 moves out of Gotchas (`docs/gotchas.md:71-89`) folded into the
per-format sections that own each row.

---

## 9. `errors-and-diagnostics.md` — Errors and diagnostics **(new)**

**Purpose.** What gets raised, what gets recorded, and how to tell them apart.

**Reader question.** "What do I catch, and where did that warning go?"

Diagnostics have a 181-line spec and, on the site, two lines at the bottom of
`extracting.md` plus a bare symbol list in `api.md`.

**Sections.**

1. **Two roots, deliberately.** `ArchiveyError` for the archive and its
   environment; `ArchiveyUsageError` for bugs in your code, **outside** that tree so
   a blanket `except ArchiveyError` never swallows one. `UnsupportedOperationError`
   is the boundary case: an archive that genuinely cannot do the thing is an
   `ArchiveyError` (must-explain #1).
2. **The exception table.** Existing; unchanged.
3. **Translation.** Third-party and stdlib failures arrive as archivey types; you
   never catch a `zlib.error` or a `pycdlib` exception.
4. **Diagnostics are data, not logs.** `reader.diagnostics`, the extraction report,
   retention, and why `DiagnosticCode` is queryable rather than a log string.
5. **The codes worth knowing** — the ones a user should act on:
   `FORMAT_EXTENSION_CONFLICT`, `STREAM_REWIND_REDECOMPRESSES`,
   `ARCHIVE_EOF_MARKER_MISSING`, `DIGEST_UNVERIFIABLE`,
   `EXTRACTION_NAME_SANITIZED`, `member_name_encoding_inferred`. Not the full
   catalogue — `api.md` has that.
6. **Policy.** `IGNORE` / `COLLECT` / `RAISE`, and `DiagnosticRaisedError` as the
   always-stop path.
7. **Limits vs filters.** `ResourceLimitError` (a bomb or a cap) versus
   `FilterRejectionError` (a member refused) — different causes, different fixes.

**Not here.** The generated symbol list (`api.md`). What extraction policy blocks
(`extracting.md`).

**Sources.** `docs/usage.md:185-217`; `docs/safe-extraction.md:90-93`.
**New:** §3, §4, §5, §6, §7 — roughly 55 of the page's 90 lines.

---

## 10. `cli.md` — Command line **(new)**

**Purpose.** The `archivey` command as a tool in its own right.

**Reader question.** "Can I just unzip this from the shell safely?"

`VISION.md:123` calls the CLI "a wedge and a dev tool… the safer `unzip`/`tar` that
demos the library in ten seconds". It has a 271-line spec and an archived product
review, and today it is 48 lines at the bottom of a page called "Basic usage" with
no nav entry — a reader looking for a command-line tool has no reason to open it.

**Sections.**

1. **Verbs**, with the aliases. Bare words, not dash-prefixed.
2. **Safe extract**, and the smart destination: no `-d` on a multi-entry archive
   lands in `./<stem>/` rather than splattering the working directory.
3. **CLI defaults differ from the library, on purpose** — overwrite is **rename**
   here and **error** there. Call it out as its own block: it is what breaks scripts
   ported from one to the other (must-explain #23).
4. **Filters.** Positionals include, `--exclude` subtracts, unmatched-pattern
   behaviour per verb.
5. **Exit codes**, especially `3` — completed with policy blocks and no member
   failure. The one an automation author must handle.
6. **Passwords on argv are visible to `ps`.** Say it here.
7. **Reserved:** `--salvage`, stdin `-`, `hash` / `create` / `convert`.

**Not here.** Library equivalents (`opening-and-listing.md`, `reading-members.md`,
`extracting.md`).

**Sources.** `docs/usage.md:219-266`.
**New:** §3 as its own block, §6.

---

## 11–16. The pages that do not change shape

| Page | Purpose | Change |
|---|---|---|
| `migrating.md` | zipfile / tarfile / shutil / patool / py7zr recipes | None structurally. Re-point three links: `usage.md#read-a-member` → `reading-members.md`, `usage.md#duplicate-names-and-is_current` → `opening-and-listing.md`, `costs.md` → `access-and-cost.md`. |
| `support-matrix.md` | What CI proves, and what it deliberately does not claim | None. The most honest page on the site — every claim is scoped to the job that proves it. |
| `philosophy.md` | Why Archivey exists, end-user framing | None. Moves down the nav: a reader who has not installed it does not need the manifesto before the install page. |
| `api.md` | Generated reference | None structurally. `ArchiveMember` is **mutable** for late-bound backend fields and callers should treat it as read-only and use `.replace()`; `modified` may be naive or aware, so compare via `modified_utc()` (must-explain #24) — docstring work, not page work. |
| `acknowledgements.md` | Credits: deps, oracles, design references | None. The BLAKE2sp attribution (O-14) was fixed in #212. |
| `how-it-works.md` | **New (D2).** Curated behind-the-scenes + a decisions summary | Entirely new prose. Six sections per `DECISIONS.md` D2: native-first parsing · the uniform stream layer · where the cost model comes from · backends and the registry · what is *not* ours · the decisions summary. A paragraph each, then a link out for depth. **Not** a mirror of the ADR index. |

---

## Coverage check — the 29 must-explain behaviours

`independent/must-explain.md` lists behaviours a competent user will hit that a
type signature does not reveal. Mapping each to its owning page is how this outline
proves it is complete rather than merely tidy.

| # | Behaviour | Owner | Today |
|---:|---|---|---|
| 1 | Two exception roots | errors | usage.md ✓ |
| 2 | One live forward-only stream by default | reading-members | usage.md ✓ |
| 3 | `streaming` + `concurrent_members` exclusive | access-and-cost | costs.md ✓ |
| 4 | `extract()` auto-streams a pipe; `open_archive` refuses one | reading-members | **gap** |
| 5 | `extract()` has no `members=` | reading-members | usage.md ✓ |
| 6 | `OnError.STOP` continues past blocks | extracting | ✓ |
| 7 | Bomb limits halt under `CONTINUE` | extracting | gotchas ✓ |
| 8 | `extract_all(config=)` cannot raise the open-time listing ceiling | extracting | **gap** |
| 9 | Duplicate names: last wins | opening-and-listing | usage.md ✓ |
| 10 | `stream_members` lifetime + laziness | reading-members | **gap** |
| 11 | Solid cost is orthogonal to concurrency | access-and-cost | costs.md ✓ |
| 12 | Mid-stream seekable sources start at `tell()` | opening-and-listing | **gap** |
| 13 | Password shapes, order, ZipCrypto STORED | opening-and-listing + gotchas | partial |
| 14 | RAR data needs RARLAB `unrar` | install + formats | ✓ |
| 15 | `PARTIAL` / `NONE`, not a vanished format | install | **gap** |
| 16 | `AUTO` falls back silently; `ON` raises | access-and-cost | partial |
| 17 | `TRUSTED` still runs the universal checks | extracting | gotchas ✓ |
| 18 | Hardlink orphans and the seekable second pass | extracting | thin |
| 19 | `member in reader` is identity | reading-members | **gap** |
| 20 | Close does not invalidate open streams | reading-members | support-matrix ✓ |
| 21 | `open_stream` vs `open_archive`; inner-TAR upgrade | opening-and-listing + formats | partial |
| 22 | Magic wins over extension, with a diagnostic | opening-and-listing + errors | partial |
| 23 | CLI defaults diverge from the library | cli | partial |
| 24 | `read()` unbounded; `ArchiveMember` mutable | reading-members + api | **gap** |
| 25 | Multi-volume and directory overrides | opening-and-listing + formats | **gap** |
| 26 | Non-file members cannot be opened | reading-members | **gap** |
| 27 | `strict_archive_eof` defaults to warn | formats + gotchas | ✓ |
| 28 | Measurement is opt-in and open-scoped | access-and-cost | **gap** |
| 29 | ISO patches pycdlib process-wide | formats + gotchas | ✓ |

**Nine outright gaps, five partials.** Eight of the nine land on the three pages that
do not exist yet — `install.md`, `opening-and-listing.md`, `reading-members.md` —
which is the outline's own argument for splitting `usage.md` rather than polishing it.

Verify each against the code before writing: the independent pass could not see
intent and over-reports. Its own worked example is #29, which it flagged as
"surprising if documented nowhere" while the module docstring documents it
thoroughly — what was missing was only the *user-facing* surfacing
(`brief.md:207-212`; the module docstring is at `iso_reader.py:22-30`).

---

## Register: who the guide is written for

Added 2026-08-04 after the maintainer read the migrated pages. Recorded here because
it applies to every page below, not to one of them.

**The reader is a working developer who is not an archive-format specialist.** They
know Python and streams; they do not know what a solid folder, an ISIZE trailer or a
check value is unless the page tells them. Prose moved from `dev-docs/` and the specs
carries the wrong register by default — accurate, and written for a reviewer rather
than a user.

Rules for the rewrite, with the worked example, are `observations.md` **O-17**. The
short version: gloss or drop the jargon, lead with what the reader does rather than the
mechanism, cut the arguing-with-a-reviewer voice, and expect most sections to lose
20–30% without losing substance. Plainer is not vaguer — "we can't tell which bytes are
good" is both plainer and more precise than "the prefix is best-effort salvageable".

## What merging cannot supply

The splits are moves. These are the writing tasks that remain, in priority order:

| Where | What | Est. |
|---|---|---:|
| `extracting.md` | Bounded-recursion recipe (O6), "what `TRUSTED` does not relax", the config-ceiling rule | ~90 |
| `errors-and-diagnostics.md` | Translation, diagnostics-as-data, the codes worth knowing, policy, limits vs filters | ~55 |
| `how-it-works.md` | All six sections (D2) | ~150 |
| `install.md` | `format_availability()` section; re-cutting the matrix by extra | ~45 |
| `opening-and-listing.md` | Sources, the named detection diagnostic, the errors callout | ~25 |
| `reading-members.md` | `stream_members` lifetime, identity and lifetime, the `extract()` pipe note | ~35 |
| `access-and-cost.md` | ON-vs-AUTO, measurement, the config-at-a-glance screen | ~55 |
| ~~`gotchas.md`~~ | ~~Accelerator bullet + the four D8 residuals~~ — **shipped** in `docs-ia-split-user-guide` | — |
| ~~`index.md`~~ | ~~The four 30-second recipes~~ — **shipped**, see D-a | — |

~455 lines of new prose still outstanding (~495 identified, ~40 shipped with the
splits change). That is Topic 8's floor, before the accuracy pass it was commissioned
for.

## Review disposition (PR #223)

An automated structure/flow review raised nine findings. Four are accepted and folded
in above; the rest are recorded here so the reasoning survives.

**Accepted:**

| # | Finding | Where it landed |
|---|---|---|
| 2 | No 30-second recipes — the independent pass's page 1 | `index.md` §2 |
| 4 | No configuration reference home | `access-and-cost.md` §10 |
| 5 | D8's O6 nested-archives Gotchas line dropped | `gotchas.md` §6, **plus three more** — the review found O6; D8 §2 also requires O1, O8 and O2 one-liners, and two of those were missing too |
| 6 | Home's Highlights links need repointing, not just nav renumbering | `index.md` §3 |

**Declined, with reasons:**

- **"Errors sits too late in the nav" (finding 7).** Reordering trades one reader's
  problem for another's; the callouts in `opening-and-listing.md` §Damaged archives and
  `reading-members.md` §Read a member are the cheaper half of the reviewer's own
  suggested fix. *(Written here before they existed — the round-2 re-review caught
  that, and they are now in the pages.)*
- **"Add a nav stub for `how-it-works.md` in the splits change" (decision 5).** This
  re-raises what
  `openspec/changes/archive/2026-08-03-docs-ia-unpublish-maintainer-tree/design.md`
  Decision 4 already argued and the maintainer merged: an empty published page breaks
  the invariant the migration exists to establish, on day one, with the exception
  being the page whose job is to demonstrate the rule. It remains a live question
  (below) — but it is not reopened by restating D2, which Decision 4 already quoted.

**Two the reviewer raised that needed a maintainer call — both now decided below.**

## Review disposition, round 2 (PR #223, post-implementation)

A re-review after the splits landed raised six findings. Four were consistency debt
this change itself created, and all four are fixed; two are correctly Topic 8's.

| # | Finding | Outcome |
|---|---|---|
| 1 | **D-a overclaimed.** It justified the nav order with a recipes block "now on the first screen" — written in the present tense for prose that was never written, because the splits change is move-only. | **Fixed by writing the recipes** (~30 lines, `index.md` §Thirty seconds). A fourth exception to design.md Decision 1, and the strongest one: the other three keep a page from being *broken*, this one keeps a shipped decision from resting on something imaginary. Softening D-a to future tense was the alternative; making the claim true is better. |
| 2 | **Same-PR contradiction.** `gotchas.md` said the accelerator fault is contained and re-raised; `access-and-cost.md` §Accelerators still said it "can abort the process" — and the Gotchas line linked there. | **Fixed.** The section is rewritten and renamed: archivey traps the caller-source case (`tests/test_accelerator_bug3_trap.py` is the authority), and the genuinely uncontained residual — path-source finalization aborts — is named as such. |
| 3 | **A link with no landing.** The O6 nesting line pointed at `extracting.md#limits`, which never mentioned nesting. | **Fixed.** One paragraph under Limits: the tracker is per-archive, so bound recursion yourself. The worked recipe stays Topic 8. |
| 4 | **A claim written before it was true.** The round-1 disposition declined the Errors nav reorder on the grounds that callouts "are in" — they were not. | **Fixed.** Both callouts written, and the disposition entry now says it was written ahead of the work. |
| 5 | Outline worklist rows stale for work that shipped. | **Fixed** above. |
| 6 | O-2 (`formats.md` "path `.gz`" vs the spec's any-declared-seekable) still open. | **Agreed, Topic 8.** It is an accuracy fix, not consistency debt from this change. |

**The pattern worth naming.** Three of the four were the *same* mistake: recording a
decision in the present tense before doing the thing it depends on. A worklist that
says what will happen and a record that says what did are different documents, and
writing them in one pass invites exactly this. Findings 1 and 4 are that error twice.

## Decided (2026-08-03)

> *"let's do it according to your recommendations. we can always reorder after we see
> the written results"*

Taken as written, with one caveat on what "reorder later" actually costs (below).

### D-a — Nav order stands: `… Reading members → Gotchas → Extracting → Access and cost …`

Extracting stays at position 5, and `documentation/spec.md:86-87`'s
Gotchas-immediately-after-basic-usage rule is honoured rather than rewritten.

The recipes block (`index.md` §2) is what makes this defensible: "extract safely" is
a copy-paste block on the **first screen**, linking straight to the page. It shipped
with `docs-ia-split-user-guide` rather than waiting for Topic 8, precisely because a
nav decision must not rest on a mitigation that does not exist — see §Review
disposition, round 2. The
independent pass's objection was never really about ordinal position — it was that a
reader could finish their first hour without meeting the safety defaults. A recipe on
Home answers that more directly than a nav swap would.

**Note for whoever writes the splits delta:** the review's stated reason for keeping
this order — avoiding a spec change — is wrong, and should not be recycled as
justification. The delta must rewrite that requirement anyway, because the
enumeration at `:85-86` names "basic usage", which stops being a page. The order
stands on the recipes argument, not on cost.

### D-b — `reading.md` splits in two. **Reversed 2026-08-03.**

> *Originally decided: stays one page, on the argument that `usage.md` failed by
> doing five jobs rather than by having thirteen sections, and that `reading.md`
> does one. Reversed after the maintainer asked for the section-by-section tally.*

**What changed: the arithmetic.** The one-page decision rested on a ~220-line
estimate. Tallied section by section it is **268** — `usage.md`'s own size (274),
the page being split for being too big. The "does one job" claim did not survive
the tally either: it does two, enumerate and read, and the sections divide
**133/135** between them with almost no overlap.

An even split that also follows a reader question is a joint, not a cut. So:

| Page | Lines | Reader question |
|---|---:|---|
| `opening-and-listing.md` | ~105 | "What's in this archive?" |
| `reading-members.md` | ~125 | "How do I read one file out, and can I trust it?" |

**The original argument still stands — it just points the other way.** `usage.md`
failed by doing five jobs. A single `reading.md` would do two. Each of these does
one, which is the same test applied to a number I had got wrong.

**Two relocations that were right regardless of the split**, and are carried with it:

- **The dedupe recipe** (31 lines) → `formats.md`, beside the stored-digest matrix it
  is *about*. This was already named as the size lever; it is a filing fix on its own
  merits, since the recipe and the matrix that tells you which formats populate
  `member.hashes` were on different pages.
- **One-shot extract** (9 lines) → three lines and a cross-link. Its code block
  duplicates `extracting.md` §1; only the no-`members=` rationale and the
  auto-streaming note are unique.

**Cost, paid:** one more nav entry (16), and the splits delta must name which of the
two pages Gotchas sits immediately after — `reading-members.md`, where the traps the
digest indexes actually live.

### D-c — The config screen is a section on `access-and-cost.md`, not its own page

As written in §6 §10. The nav goes to 16 entries with the D-b split, not 17.

### Reversal cost, stated honestly

"We can reorder after we see the written results" is true, but not equally true of
all three:

| Decision | Cost to reverse after the splits land |
|---|---|
| **D-a** nav order | **Free.** One `mkdocs.yml` line, plus wording in a `documentation` delta that is being written regardless. |
| **D-c** config screen placement | **Cheap.** Moving a self-contained section to a new page; a nav entry and one link repoint. |
| **D-b** the opening/reading split | **Paid now, which was the point.** Re-merging or re-cutting after prose lands means moving *written* text, not blocks, and after the `0.2.0` tag it also costs a redirect. Deciding it before a word is written is the cheap moment, and this is that moment. |

So D-a and D-c are genuinely reversible on sight of the result. D-b was the one that
was not, which is why it was re-examined before the splits change rather than after —
and why the tally that reversed it was worth asking for.

### D-d — `safe-extraction.md` → `extracting.md`. **2026-08-04.**

Consistency with `opening-and-listing` / `reading-members` (the sibling form is
verb-ing) is the surface reason. The stronger one is that `philosophy.md` says
*"safety is a contract, not a marketing flag"* — and a page asserting "safe" in its
own filename is the flag. The page demonstrates it; it need not claim it. Nav label:
**Extracting**. If adoption work later wants the word in the menu, the label can carry
it without the filename doing so.

### D-e — The damage contract moves to `errors-and-diagnostics.md`. **2026-08-04.**

The integrity guarantee, the call × failure matrix and the `members_report()` recipe
leave the two flow pages for `errors-and-diagnostics.md`, under **"When an archive is
damaged"**. What stays in the flow is the one-line honesty promise plus a link.

**This reverses an argument I made, and the correction is the point.** I opposed
exactly this on the grounds that damage handling is "a VISION founding use case", so
segregating it would demote something core. Checked: VISION's two load-bearing claims
are safe-by-default and memory-safe parsing of hostile input. The founding use case is
*indexing and deduplicating messy backups*; "damaged input is a first-class citizen" is
one of five priorities that origin story implies, and that bullet is specifically about
not failing at **open** — the listing side — not the read contract.

The maintainer's split is the accurate one, and it is a **depth** split rather than a
mood split, which is why it does not scatter:

| | Where |
|---|---|
| "No silent errors" — reads raise rather than returning short data quietly | Footgun property. One line, in the flow. |
| The contract — matrix, prefix semantics, `members_report()` | Recovery depth. Advanced; most readers never need it. |

One exception, called by the maintainer: the `read(member.size)` asymmetry — raises on
corruption, returns short on truncation — **stays in the flow**, because that one is a
footgun, not depth.

Sizes: `reading-members` 129 → 84, `opening-and-listing` 90 → 76,
`errors-and-diagnostics` 43 → 140. The third was the thinnest new page and its target
was ~90; it is now filled with material that belongs on it rather than padding.

## Still open

1. ~~**The `documentation` spec's Gotchas requirement**~~ — **done.**
   `docs-ia-split-user-guide` encodes it as `REMOVED` plus a replacement requirement
   that states what Gotchas *is*, with a migration note recording where each displaced
   fact survives. It reaches `openspec/specs/` when the change is archived after
   merge.
2. **How much of `how-it-works.md` belongs in phase 3 at all.** It is the only page on
   this list that is 100% new prose, which makes it Topic 8 work sitting inside a
   splits change. Current position, carried from the merged change's `design.md`
   Decision 4: no stub, the page and its nav slot are created by whichever change
   writes its content. Reversing that is one commit.
