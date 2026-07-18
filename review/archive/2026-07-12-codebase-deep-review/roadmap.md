# Theme 5 — Roadmap: cut, defer, and what's missing

I largely agree with the sequencing in `PLAN.md`/`IDEAS.md` (native-7z/RAR before writing, CLI as
a wedge). This is where I'd push back or add.

## Cut / deprioritize

- **libarchive backend (`IDEAS.md:33`) — cut, or keep permanently experimental.** It directly
  contradicts VISION's load-bearing claim #2 ("parse untrusted archives without native-code parser
  attack surface"). The moment a libarchive backend exists, the differentiator gets muddy: users
  will reach for it for exotic formats and inherit exactly the CVE surface the project exists to
  avoid. If it lands at all, it must be off-by-default, never in `[recommended]`, and documented as
  "trades the memory-safety guarantee for coverage." I'd cut it from the roadmap and leave it as a
  third-party plugin once the backend API is public (`IDEAS.md:219`) — which is the right home for
  it and costs the core nothing.

- **Synthetic single-stream RAR → libarchive (`IDEAS.md:39`) — cut.** Same objection, plus it's a
  clever hack that imports fragility. The native RAR metadata + `unrar` data-pipe plan already
  covers RAR reading without native-parser attack surface. Don't add a second RAR path.

- **`ArchivePath` pathlib-like navigation (`IDEAS.md:72`) — defer past 1.0.** It's ergonomic sugar
  over an API that isn't stable yet. Building it now freezes assumptions about the member model;
  building it after the reader API settles is cheap. Not on the critical path to "reads everything."

- **fsspec integration's write direction (`IDEAS.md:76`) — defer with writing.** The read-side
  fsspec adapter is a good adoption channel and cheap; the write-side is entangled with the whole
  writing phase and shouldn't pull writing forward.

## Promote / do sooner

- **Benchmarks as a CI gate (`IDEAS.md:214`, VISION perf budget) — promote.** VISION commits to a
  ≤1.3× stdlib budget and says "an implementation that re-reads a solid block fails the benchmark
  even if a small test corpus hides it." Right now there is *no* enforcement of that — the 7z solid
  random-`open()` path re-decodes the folder from its start per member (`sevenzip_reader.py:924-963`),
  which is O(n²) for reading every member of a solid folder out of order, and nothing catches it.
  The cost model *documents* it (AccessCost.SOLID), but a benchmark tracking bytes-decompressed is
  the only thing that turns "documented trap" into "regression-gated." I'd stand this up before the
  CLI, because the CLI's `test`/`extract` on real solid archives is exactly where the O(n²) bites.

- **Salvage / best-effort read mode (`IDEAS.md:201`) — promote toward 1.0.** This is *the founding
  use case* (VISION §"founding use case": "a truncated archive should yield every member that is
  recoverable plus an honest error"). Today reads are all-or-error. For a library whose origin
  story is "index decades of messy, truncated backups," shipping 1.0 without a salvage mode ships
  without the reason it was built. It doesn't have to be in 1.0, but it should rank above
  `ArchivePath` and the fsspec write direction.

- **Warnings-as-data / metadata bomb bounds (`IDEAS.md:230`, threat-model O1) — promote.** See
  latent-bugs.md L1: the native 7z parser has an unbounded `num_files` allocation. VISION #2 sells
  memory-safe hostile-input parsing; a pure-Python OOM undercuts that claim even though it's not
  memory *corruption*. Bounding the parser's count fields belongs before the public release and the
  Atheris gate, not after.

## Missing from the roadmap

- **A resource-limits config for *listing*, not just extraction.** `ExtractionLimits` guards
  extraction bombs, but there's no equivalent for the *parse/list* phase (max members, max header
  size, max name length). Threat-model O1 names this; the roadmap doesn't have a concrete change for
  it. It's the natural home for the L1 fix and should be a named OpenSpec change.

- **A documented policy for `member.hashes` population parity across backends.** 7z populates
  crc32; ZIP doesn't (specs-docs.md S1); directory/tar don't (no stored digest to surface, fine).
  "Which backends surface which stored digests, and the recipe for cheap dedupe" deserves to be a
  first-class doc + a conformance-sweep assertion, since it's the founding use case.

- **An explicit free-threading support statement.** The `3.13t` job exists but runs core-only.
  Either commit to "free-threaded support = the core backends, ISO/accelerators GIL-only" as a
  documented matrix, or extend the job. Right now the support boundary is implicit in a CI flag.

## Replies to the maintainer's questions

- **"Should we support free-threading with the external backends?"** Split by backend kind:
  - **ISO / pure-Python optional libs (pycdlib):** very likely already correct under free-threading
    — it's pure Python, and archivey already serializes the shared `_cdfp` via `LockedStream` and
    the handle lock, plus the deque cycle-guard is per-instance. The cheap next step is *empirical*:
    add an `[iso]`-installed leg to the `3.13t` job running `-m concurrent_reader`, and see what
    passes. That's low-cost and would let you make a real support claim instead of a guess. I'd do
    this.
  - **Accelerators (rapidgzip):** these are C/C++ extensions that spawn their own worker threads and
    make GIL assumptions; free-threaded safety is upstream's to guarantee, not something archivey's
    locking can retrofit. archivey serializes them anyway (single-live-stream / handle lock), so the
    realistic position is "accelerators are GIL-only" — test to confirm, don't promise. So: test ISO
    (probably works), keep accelerators out of the free-threaded claim, and document the resulting
    matrix rather than leaving it implicit in a CI flag.
- **"Check if the single-file reader exposes each format's stored hashes."** Checked: it exposes
  decompressed *size* (xz/lzip) but **no stored digest**. gzip and lzip both store a CRC-32 of the
  decompressed content in their trailer, cheaply readable from a seekable/path source — directly
  useful for the dedupe use case. Implementing it well needs a small design (extend `MetadataContext`
  with a trailer peek; handle multi-member gzip, where the trailer CRC covers only the last member,
  the same caveat the gzip truncation backstop already handles). Left as a focused follow-up rather
  than bolted into this PR. **Recommend** adding it as its own change — it's the single-file half of
  the "hashes without decompression" story that the ZIP CRC-32 fix (Q3) started.
