# Brief — Public API & member-model coherence / ergonomics

Read `review/README.md` (conventions, VISION tie-breakers, deliverable shape). This
is a **non-security** review: correctness/hostile-input is covered by the archived
security round. The lens here is *design*, not bugs.

## Why now

VISION's central promise is **"read every format behind one uniform interface."**
`0.2.0` is the first public release, and it **freezes the public API for real
users** — after it, every name and shape carries a compatibility cost. Two things
make this the moment:

1. Reading is release-complete, so the surface is finally stable enough to judge as
   a whole rather than a moving target.
2. The CLI (#120) is the **first real second consumer** of the library API. Where
   the CLI had to reach past the public surface, add a helper, or work around an
   awkward shape, that is direct evidence of an API gap — mine the CLI diff for it.

The public surface is currently **~85 exported names** (`src/archivey/__init__.py`
`__all__`). "Is that the right size to commit to?" is the headline question, not a
rhetorical one.

## Scope

The public spine and its data model:
- `core.py` — `open_archive` / `open_stream` / `extract` + detection entry points.
- `reader.py` — `ArchiveReader` ABC (the read surface) + `MemberSelector`/`MemberFilter`.
- `types.py` — `ArchiveMember`, `ArchiveInfo`, `ArchiveFormat`/`ContainerFormat`/
  `StreamFormat`, `MemberType`, `MemberStreams`, `StreamCapability`,
  `CompressionAlgorithm`/`CompressionMethod`, `CreateSystem`.
- `config.py` — `ArchiveyConfig`, `ExtractionLimits`, `ListingLimits`,
  `AcceleratorMode`, password/policy types.
- `cost.py` — `CostReceipt`, `ListingCost`, `AccessCost`.
- `diagnostics.py` — the `Diagnostic*` value types + the ~13 `*Context` classes.
- `exceptions.py` — the `ArchiveyError` tree (+ `ArchiveyUsageError` outside it).
- `__init__.py` — what's re-exported (and what leaks that shouldn't be).

Cross-reference the specs: `archive-reading`, `archive-data-model`,
`access-mode-and-cost`, `error-handling`, `diagnostics`, `format-detection`.

## What to evaluate (ranked by cost-of-getting-it-wrong at a public freeze)

### A. Is the interface actually uniform across backends? (the load-bearing claim)
For each observable on `ArchiveMember` / `ArchiveReader`, does **every** backend
(ZIP, TAR, 7z, RAR, ISO, single-file, directory) populate it with the same meaning,
or are there silent per-format divergences a caller would trip on?
- `member.hashes` — which backends surface which stored digests, and is the
  emptiness contract documented? (Old finding #3/#10 parity; #104 added 7z/RAR
  digests — is ZIP/tar/dir now consistent and *documented as such*?)
- `CostReceipt` (`ListingCost` / `AccessCost`) — does each backend report honest,
  comparable values? The old review flagged directory `INDEXED` overclaiming
  (finding #4/#9) and the 7z solid `SOLID` cost — are the axes now consistent and do
  they mean the same thing across formats?
- `MemberStreams` / `StreamCapability` — is the declared-capability set uniform, and
  does a caller branching on it get the same answer shape everywhere?
- timestamps, mode, link targets, `MemberType` (incl. `ANTI`) — same field, same
  meaning, or format-specific surprises?
This is the **cross-backend parity** audit; the brief owns it (not spun out). A
conformance-sweep assertion (`tests/`) is the right home for anything found — note
where the sweep doesn't currently assert parity.

### B. Surface size & public/internal boundary
- ~85 names is a lot to freeze. Which are genuinely part of the "iterate members,
  hash, extract safely" contract, and which are implementation detail that leaked
  (e.g. does every `*Context` type need to be top-level public, or should they live
  under a `diagnostics` namespace)?
- Is `ArchiveStream` a public type users construct/depend on, or an internal that
  escaped? Is `RAPIDGZIP_AUTO_MIN_COMPRESSED_SIZE` really public API or a tuning
  constant?
- Naming coherence: `open_archive`/`open_stream`/`extract`; `OnError` vs
  `ExtractionPolicy` vs `OverwritePolicy` vs `DiagnosticPolicy` — is the vocabulary
  consistent (Policy/Mode/Status suffixes used the same way)? Enums vs literals vs
  flags — one convention or several?

### C. The member model & ergonomics of the core loops
Walk the three canonical jobs and judge friction, not correctness:
- **"list + hash for dedupe"** (the founding use case): how many lines, how many
  imports, how obvious? Is getting a stored digest without decompressing discoverable
  from the type, or does it require lore?
- **"safe extract with a policy"**: is `extract` + `ExtractionPolicy`/`OverwritePolicy`
  + `ExtractionReport`/`ExtractionResult`/`ExtractionStatus`/`ExtractionProgress` a
  coherent cluster, or overlapping concepts? (Five extraction-result-ish names is a
  smell worth checking.)
- **"open one member and stream it"**: the `open()` → stream contract, non-file
  members (`open`/`read` raises), the seekability/`try_get_size` story.
- Mutability of `ArchiveMember` (fields stamped during materialization) — is the
  public contract about what's set when, and after close, clear?

### D. Config & error ergonomics
- `ArchiveyConfig` / `DEFAULT_ARCHIVEY_CONFIG` — is the default safe-by-default and
  are the knobs (limits, accelerator mode, password provider) discoverable and
  orthogonal?
- The `ArchiveyError` tree — is it the right shape for callers to catch at the
  granularity they need (e.g. distinguish wrong-password / truncation / corruption /
  unsupported), and is `ArchiveyUsageError`-outside-the-tree documented and defensible?

## Non-goals
- Not a bug hunt (security round covered it). A correctness bug found in passing is
  still worth noting, but the deliverable is API judgement.
- Don't propose the writing API (Phase 9) or re-open settled naming already in the
  specs without a concrete ergonomic reason.
- `ArchivePath`/fsspec sugar is deferred past 1.0 (`review/archive/.../roadmap.md`) —
  out of scope.

## Deliverable
Per README. Suggested theme files: `parity.md` (the cross-backend audit — likely the
headline), `surface.md` (public/internal boundary + naming), `ergonomics.md` (the
three canonical loops). Where you recommend a rename/removal, state the migration
cost (it's pre-release, so most are free — say so). A concrete "here's the smallest
public surface that still serves the three use cases" proposal is worth more than a
list of nitpicks.
