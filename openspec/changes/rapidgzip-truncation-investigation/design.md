# Design notes — rapidgzip truncation characterization

Provenance for when this change is implemented. Debt-ledger **Q4** (2026-07-20)
decided **PAY before 0.2.0** (do not ship the under-characterized ISIZE heuristic
as “done”); the measurement + narrow/extend/remove work lands in a later PR.
This file is the implementer brief: constraints and pointers that were scattered
across the ledger, threat model, and codecs code.

## Decisions already made

| Decision | Status |
| --- | --- |
| Measure first, then choose narrow / extend / remove | settled in proposal |
| Backstop = narrowest check that covers silent cases; never false-positive on valid input | settled in delta spec |
| Block-wise multi-member header scan (no full-file read) | done (#14) |
| **Finish before 0.2.0** (debt-ledger Q4 = PAY) | decided 2026-07-20; implementation deferred to §3 after lock-in |
| Fix priorities: (1) no silent success, (2) recover partial data, (3) seek on good inputs | settled with maintainer |
| DIY reverse deflate-block / trailer seek for gzip | **rejected** |
| Soft EOF / silent empty-short is upstream **design** (not a binding bug) | settled — `UPSTREAM_TRUNCATION_REPORT.md` + `docs/internal/rapidgzip-upstream-report.md` |
| Do not parse rapidgzip stderr; do not trust `block_offsets_complete` / `size` | settled |
| **Composed stack:** empty→stdlib on zero-byte EOF + keep/extend single-member ISIZE (close `<18`) | **locked** 2026-07-20 |
| Multi-member per-member ISIZE sum | **deferred** — member discovery is a forward `1f 8b 08` scan with false-header risk (same class as today’s bailout / rapidgzip speculative decode); keep “any further magic ⇒ do not raise” |
| `tell_compressed()==0` header-only trap | **rejected** — bit offset 160 is fixture-specific, not a general empty-gzip constant |
| File upstream issue for `is_stream_complete()` | **no** — soft EOF is by design; document in `docs/internal/rapidgzip-upstream-report.md` (pyppmd-style), do not file a “bug” |
| `parallelization=0` (all cores) | **keep** — intentional; motivation for rapidgzip + benchmarks |
| §2 lock vs macOS/Windows (1.3) | **lock §2 now**; confirm silent set via CI probe on macOS/Windows before 0.2.0 |

## Deferred / out of scope for §3

- Per-member ISIZE summing for concatenated gzip (see above).
- Changing `parallelization` away from `0`.
- DIY gzip seek indexes / reverse block scans.
- Relying on upstream adding a completeness flag (document only).

Rejected framing: “KEEP the open change past 0.2.0 because accelerators are
opt-in / threat-model-scoped.” Opt-in and non-defended still ship a
self-described heuristic on a supported path; the maintainer wants that
characterized before the release label.

## Two length mechanisms — do not conflate

`internal/streams/codecs.py` has **two** different length guards on accelerated
gzip. The investigation targets only the second; the first must keep working.

| Mechanism | When | Role |
| --- | --- | --- |
| `_wrap_accelerated_length` → `VerifyingStream` | `expected_decompressed_size` set (container-declared) | Hard bound + `TruncatedError` at close |
| `_GzipTruncationCheckStream` | Seekable **path** gzip with readable ISIZE; no container size | Heuristic ISIZE compare at sequential EOF |

AUTO selection (`_rapidgzip_enabled`) requires truncation to be *verifiable*:
either `expected_decompressed_size` **or** `gzip_isize_backstop` (set by
`_config_with_gzip_isize`). `ON` ignores that gate.

**Implementer check after any outcome:** if the ISIZE backstop is removed or
narrowed, re-check bare `.gz` / single-file-compressed paths under AUTO — do not
accidentally disable rapidgzip for every file that only had ISIZE as its
verifiability signal, or leave AUTO selecting an accelerator with no truncation
surface at all.

## Current ISIZE backstop scope (code facts)

From `_GzipTruncationCheckStream` / helpers (see `codecs.py`):

- Used only for seekable **path** sources (independent handle for trailer + scan).
- A caller `seek` that leaves the sequential frontier **disarms** the check
  (partial / random-access totals are meaningless).
- Explicit `read(0)` must not trip EOF verification.
- ISIZE is **mod 2³²**; multi-member trailers are only the *last* member —
  that is why ISIZE is **not** copied into `expected_decompressed_size`.
- Multi-member disambiguation: any further `1f 8b 08` ⇒ treat as multi-member
  and **do not raise** (false-negative only; never false-positive on valid input).
- Header-scan failure / OSError ⇒ assume possible second member (same conservatism).

## Characterization constraints (from threat model / known-issues)

1. **Mutation and Atheris run with accelerators OFF** (C++ hang risk). Do **not**
   expect existing fuzz jobs to exercise rapidgzip truncation. Use a dedicated
   measurement script or pytest module with `use_rapidgzip=ON` (and a wall-clock
   timeout — crafted cuts can busy-loop in C++ threads).
2. Prefer **path** sources in the matrix. Upstream **Bug 3** (`known-issues.md`):
   rapidgzip can `terminate()` the process when a *Python* source object raises
   during decode — a different defect class; do not confuse it with silent
   truncation, and do not make the sweep depend on file-object sources.
3. Platforms: Linux + macOS arm64 (task 1.3) — macOS matters for the
   single-accelerator story even though this change is not about dual-load.
4. Also sweep `rapidgzip.IndexedBzip2File` (task 1.4). Raw deflate / zlib
   accelerated paths have **no** ISIZE-style backstop today
   (`library-analysis.md` — accepted; container CRC covers ZIP/7z members).
   Do not invent a gzip-ISIZE twin for them unless the bzip2/deflate sweep
   shows a silent-truncation set that needs one.

## Adjacent debt (out of scope for this change, but note outcomes)

| Item | Relation |
| --- | --- |
| `VerifyingStream` leftover after fusion (#137) | Debt-ledger structural: wrapper survives mainly for codec length backstops + unit tests (`backlog.md` Topic 6). If the ISIZE stream goes away and only container `VerifyingStream` (or neither) remains, Topic 6 “delete when unused” may unlock — follow-up, not required here. |
| Perf **P8** (AUTO 1 MiB threshold conservative for seek) | Orthogonal tuning. Do not retune the size gate in this change. |
| Accelerator hang sandbox / SECURITY.md wording | Threat-model residual; separate from truncation characterization. |
| CLI misleading “install rapidgzip” when AUTO declined | cli-product polish; unrelated to ISIZE correctness. |

## Upstream research (2026-07-20)

See **`UPSTREAM_TRUNCATION_REPORT.md`** (companion to `FINDINGS.md`). Headline:
rapidgzip’s parallel reader **intentionally** soft-EOFs many truncations
(return empty/short, mark `block_offsets_complete`); stderr Unexpected-end is a
rethrow path near the trailer, not a silent-success channel; `parallelization=0`
means all cores. Upstream evidence supports FINDINGS’ **empty→stdlib + ISIZE**
stack; do not remove ISIZE based on a “header-only only” hypothesis.

## Suggested measurement shape (refines tasks §1)

For each fixture shape (empty payload, &lt;1 block, multi-block, multi-member,
and the suspected ~10-byte header-only case):

- Cut at every byte offset (or a dense stratified sample if full sweep is huge).
- Record: rapidgzip raises (exception type/text) / silent short / silent zero /
  full output / hang-or-timeout.
- Note `parallelization` if the API exposes it.
- Run the same cuts through stdlib `gzip` as the oracle for “should be
  TruncatedError / CorruptionError”.
- Publish a short table in this change (or `docs/internal/`) before picking
  §2 narrow / extend / remove.

## Acceptance when implementing

- Delta spec scenarios hold; valid single- and multi-member files never
  false-flag.
- AUTO + `ON`/`OFF` behavior documented if verifiability signals change.
- `docs/internal/known-issues.md` / `library-analysis.md` truncation notes
  updated to match the chosen backstop.
- `openspec validate --strict rapidgzip-truncation-investigation` green;
  sync delta into main `seekable-decompressor-streams` when landing.

## Linux measurement outcome (2026-07-20)

Full write-up: [`FINDINGS.md`](FINDINGS.md). Raw tables:
`results/linux-x86_64.{md,json}`. Sweep tool:
`scripts/rapidgzip_truncation_sweep.py`.

**Contradiction of the “~10-byte only” hypothesis:** on Linux x86_64 /
rapidgzip 0.16.0, for every complete single-member fixture in the matrix,
cuts from offset **10** through most of the body **silently return `b""`**
while stdlib `gzip` raises `EOFError`. Near the trailer rapidgzip raises;
the full file matches. `parallelization` 0 vs 1 did not change gzip
outcomes. Multi-block/multi-member also show **silent_short**, and in a
few trailer-stripped cuts rapidgzip returns the **full** payload while
stdlib raises.

**Current backstop:** catches most silent∩raise cuts when compressed size
`≥ 18` (ISIZE mismatch). Misses the `< 18` band (including bare header-10)
and multi-member bailouts. So the machinery is load-bearing — but incomplete.

**Recommendation (locked 2026-07-20):** empty→stdlib on zero-byte EOF +
single-member ISIZE (close `<18`); multi-member ISIZE sum deferred; keep
`parallelization=0`; document soft EOF in `docs/internal/rapidgzip-upstream-report.md`.
Details in `FINDINGS.md` / decisions table above.

## Compose with `gzip-zlib-truncation-recovery` (#183)

The empty→stdlib fallback **retargets** the gzip-window `DecompressorStream`
(not `gzip.GzipFile` / a `_STDLIB_READ_SIZE = 1` loop): large bounded `read(n)`
recovers a prefix; `read()` / `readall` raise without returning bytes; content
faults raise from reads, never `close()` (ADR 0014). Switching `_inner` keeps
`tell` / `seek` / `seekable` honest after fallback.
