# Archivey — Vision

> What this project is trying to become, why it should exist, and the priorities that
> follow. `openspec/specs/` defines *what the library does* (historical prose in
> `docs/grab-bag/`); this document defines *what it is for* and how to make trade-offs
> when they conflict. End-user distill: `docs/philosophy.md`. (Recorded 2026-07 from
> maintainer + review discussions.)

## The one-sentence pitch

**The default Python library for dealing with archives** — the thing every project
reaches for when it needs to read, inspect, stream, or safely extract any archive
format, the way `requests` became the default for HTTP.

This library *should* already exist — arguably in the stdlib — but doesn't: stdlib
`zipfile`/`tarfile` are two inconsistent APIs with decades of known gotchas
(`tarfile` path traversal took 15 years to get filters, PEP 706), `shutil.unpack_archive`
is unsafe and shallow, and the third-party format libraries (`py7zr`, `rarfile`,
libarchive bindings) each have their own APIs, error types, quirks, and performance
traps. Realistically archivey will never *be* stdlib (stdlib is where libraries
calcify — see the `compression.zstd` timeline); the goal is to be **so standard it
feels like stdlib**.

## The two load-bearing claims

1. **Safe by default.** Extraction cannot be zip-slipped, symlink-escaped, or
   decompression-bombed unless the caller explicitly opts out. Safety is a *contract*
   (specced, tested, threat-modeled — see `docs/internal/threat-model.md`), not a feature flag.
2. **Memory-safe parsing of hostile input.** The native-first strategy for 7z/RAR (and
   eventually ZIP) is not purity for its own sake: pure-Python parsers can be *wrong*
   but they cannot be *corrupted*. C archive parsers (libarchive et al.) have a long
   CVE history of memory-safety bugs triggered by crafted archives. "Parse untrusted
   archives without native-code parser attack surface" is a differentiator no
   mainstream alternative offers.

Both claims must be *earned in public*: a written threat model, an adversarial corpus,
coverage-guided fuzzing of every native parser, and a disclosure process — before the
words "safe" or "secure" appear in marketing.

## The founding use case (and what it implies)

The project started as: **index and deduplicate decades of messy backups** — old
downloads with wrong extensions, truncated/corrupted files, archives produced by buggy
tools — where the job is *iterate members and hash contents*, and where `rarfile`/
`py7zr` re-decompressing a solid block once per member made the job intractable.

That origin story encodes priorities that remain core:

- **Content-first, not extraction-first.** Reading, streaming, and metadata are the
  primary API; extraction is the second; writing is a natural extension but explicitly
  the lowest priority (may land after 1.0 — see roadmap).
- **Identification must be evidence-based.** Wrong extensions are normal; magic-first
  detection with honest confidence reporting is a feature, not plumbing.
- **Never decompress the same byte twice** (without saying so). Solid blocks are read
  once per pass; the access-mode/cost model exists so O(n²) traps are impossible to
  hit *silently*.
- **Damaged input is a first-class citizen** — the founding corpus is full of it. A
  truncated archive should yield every member that *is* recoverable plus an honest
  error, not a bare exception at open. (Gap today: reads are all-or-error; a
  "salvage" read mode is on the backlog — see `IDEAS.md`.)
- **Hashes without decompression where possible.** Formats already store CRC32/BLAKE2
  digests; a dedupe pass should be able to use `member.hashes` without reading data.

## What "no surprises" means concretely

- Behavior differences between formats are surfaced as **data** (explicit fields,
  `None`, documented sentinels) — never silent guesses. This is the standing design
  authority (`openspec/project.md`).
- Anything the library can only *warn* about should ideally also be **queryable as
  data** — a logging warning most applications never see is a surprise deferred, not
  avoided. (Backlog: the warnings-as-data sweep, `IDEAS.md`.)
- Contracts hold under adversarial input, not just well-formed archives.

## Performance budget

- Target: **≤ 1.3×** stdlib wall-time for the common paths (open/list/read/extract on
  ZIP and TAR); up to ~2× acceptable where a safety or correctness feature justifies it.
- The bottleneck in real workloads is data movement and *re*-decompression, not header
  parsing. So the benchmark suite must track **bytes decompressed and seek patterns**,
  not just wall time — an implementation that re-reads a solid block fails the
  benchmark even if a small test corpus hides it.
- Benchmarks become a CI gate like the type checkers (backlog until stood up).

## Quality scaffolding over promises

No "bug-free" promises. Instead, machinery that catches bugs before release:

- The spec corpus (`openspec/specs/`) with scenario-driven tests — every behavior
  claim has a test.
- The **declarative archive corpus + cross-format conformance sweep** (the
  `retire-dev-oracle` change): every corpus archive must open/list/extract or raise
  its documented error, across every implemented backend — the regression net that
  catches "backend X broke shape Y" without a hand-written test per pair.
- **Fuzzing**, staged: property-based tests (Hypothesis) for the pure logic now;
  mutation fuzzing (bit-flips/truncation over the corpus, asserting
  never-crash/never-hang/always-`ArchiveyError`) now; coverage-guided fuzzing
  (Atheris) as an entry gate for the native 7z/RAR parsers; OSS-Fuzz onboarding at
  public release.
- Three-configuration CI (current / lowest / zero-dep) — already standing.

## Adoption strategy (when the pieces are in place)

- **Release when reading is complete** — ZIP/TAR/single-file/ISO/directory *plus native
  7z/RAR* — since "reads everything" is the reason to switch. Writing is not a 1.0
  requirement.
- **The CLI is a wedge and a dev tool**, not the main act: `archivey list|test|extract`
  is the safer `unzip`/`tar` that demos the library in ten seconds, and it doubles as
  the maintainer's own inspection tool during development (moved earlier in the
  roadmap accordingly).
- Meet users where they are: a migration guide from `zipfile`/`tarfile`/
  `shutil.unpack_archive`/`patool`; an fsspec filesystem adapter as an integration
  channel; recipes for the data-pipeline crowd (who currently hand-roll unsafe
  `extractall` on downloaded datasets).
- A **public backend API** (the registry ABC, stabilized) turns "maximum format
  compatibility" from a solo treadmill into an ecosystem: rare formats (CAB, CPIO,
  SquashFS, WIM…) can live as third-party plugins. Pre-1.0 decision.

## Non-goals

- **Not an everything-tool**: no in-place archive modification, no encryption-write for
  7z/RAR, no async API in v1 (decided deferrals — `openspec/project.md`).
- **Not a backup engine** — but see the open metadata-fidelity decision (`IDEAS.md`):
  whether xattrs/ACLs/owners round-trip determines whether backup tools can *build on*
  archivey. Read-side fidelity is cheap to add later (fields are additive); the
  decision truly binds only when writing lands.
- **No compatibility shims for other libraries' APIs.** One clean API; migration
  guides rather than emulation layers.
- **No quirk-driven architecture.** Third-party format libraries whose behavioral
  quirks would leak into the core contracts (py7zr/rarfile as read backends) are kept
  out even at the cost of a longer road — the DEV codebase demonstrated where that
  leads. Wrapping is acceptable only where the wrapped library is well-behaved under
  our contract (`pycdlib`) or delegated cleanly at a process boundary (`unrar` as a
  data decompressor).

## Maintenance reality

Developed for fun, released when ready, maintained by one person plus AI agents. The
consequences are deliberate: a small dependency surface, heavy investment in
self-checking scaffolding (specs, corpus sweep, fuzzing, CI matrix) over manual
vigilance, a conservative public-API surface (easy to keep stable), and no promised
support matrix beyond what CI actually exercises.
