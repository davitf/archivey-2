# Archivey — Project Context

> Cross-cutting context shared by all capability specs in `openspec/specs/`.
> Source of truth for the prose design is `SPEC.md`, `ARCHITECTURE.md`, and
> `COMPARISON.md` at the repo root; these specs reorganize that material into
> OpenSpec capabilities and will be refined over time.

## What this is

Archivey is a Python library for reading, streaming, and safely extracting
archives through a single, uniformly-typed interface. It presents ZIP, TAR (all
variants), RAR, 7z, ISO 9660, plain directories, and single-file compressed
streams (GZ/BZ2/XZ/ZST) as first-class, interchangeable objects.

## Design authority

When a format quirk cannot be cleanly mapped to the unified model, the library
surfaces the inconsistency as an explicit, documented field value (`None` or an
`Unknown` sentinel) — never as a silent guess, default, or exception.

## Target environment

| Item | Constraint |
|------|------------|
| Python version | 3.11+ |
| Core dependencies | None (stdlib only) |
| Optional extras | `[7z]` (py7zr), `[rar]` (rarfile + system `unrar`), `[iso]` (pycdlib), `[zstd]`, `[lz4]`, `[all]` |
| OS support | Linux, macOS, Windows |
| Thread safety | Readers and writers are not thread-safe — one per thread. |
| Concurrency model | Synchronous API only for v1 (async is a deferred follow-on). |

## Capability map

| Capability | Concern |
|------------|---------|
| `archive-reading` | `open_archive()`, the `ArchiveReader` surface, iteration, random/sequential access, link following |
| `archive-writing` | `create()`, the `ArchiveWriter` surface, streaming conversion |
| `archive-data-model` | `Member`, `ArchiveInfo`, `ArchiveFormat`, `MemberType`, compression types |
| `access-intent-and-cost` | `Intent` enum and the `CostReceipt` cost surface |
| `safe-extraction` | `extract()`, extraction policies, the non-bypassable filter contract, decompression-bomb limits, and extraction progress/result reporting |
| `format-detection` | `detect_format()`, magic table, non-seekable peek/replay |
| `backend-registry` | Backend registration, selection, the `Backend` ABC, optional deps |
| `error-handling` | The `ArchiveyError` hierarchy and error-translation contract |
| `logging` | The `archivey` logger hierarchy (cross-cutting; library never configures handlers) |
| `format-zip` / `format-tar` / `format-single-file-compressors` / `format-7z` / `format-rar` / `format-iso` / `format-directory` | Per-format behavioral contracts |
| `compressed-streams` | Uniform pull-based codec decompressor layer (single-file + 7z/ZIP container codecs + AES stage); format parsers compose it instead of calling codec libs |
| `seekable-decompressor-streams` | Random access inside single compressed streams (builds on `compressed-streams`) |
| `testing-contract` | Equivalence matrix, adversarial corpus, round-trip and non-seekable coverage |
| `cli` | The `archivey` command-line interface |
| `packaging-and-extras` | Install-time contract: zero-dep core (incl. native 7z read + RAR metadata), extras→capability mapping, Python/OS matrix, `__version__` |

**7z/RAR strategy (native-first):** 7z and RAR are read with **native** parsers,
not `py7zr`/`rarfile`. 7z reading decodes common codecs through stdlib
`lzma`/`bz2`/`zlib` (zero runtime deps); PPMd/Deflate64 come from the `[7z]` extra,
AES decryption from `[crypto]`, and only BCJ2 is detect-and-rejected. RAR metadata
is parsed natively (encrypted RAR5 headers decrypted via `[crypto]`) while the
external `unrar` binary does the proprietary data decompression. `py7zr` is kept
only for 7z *writing* (`[7z-write]`); `py7zr` and `rarfile` otherwise serve only as
`dev`-group test oracles (see `testing-contract`). Provenance: the `archivey-dev`
`sevenzip-native-reader` / `rar-native-metadata-reader` explorations (clone per
`CLAUDE.md`).

## Implementation order

The build sequence is a **selective rewrite** starting from the existing
`archivey-dev` codebase — see `PLAN.md` (repo root) for the detailed,
phase-by-phase task list and acceptance criteria. The plan is organized as a
DEV-migration sequence rather than a build-each-capability sequence, so a phase
typically advances several capabilities at once. Specs themselves remain
order-free; this table is the association between the two.

| Phase | Theme | Primary capabilities advanced |
|-------|-------|-------------------------------|
| 1 | Project scaffold + verbatim port from DEV | `packaging-and-extras` (pyproject, extras, env matrix); ports `format-*` backends and `format-detection`. 7z/RAR are native-first (see note), so porting DEV's `py7zr`/`rarfile` read backends is interim-only or deferred. Tooling/migration mechanics are not specced. |
| 2 | Stream layer reorganization | `compressed-streams`, `seekable-decompressor-streams`, `archive-reading` *(internal streams)*. 7z/ZIP container codecs (`pyppmd`/`inflate64`/AES stage) added to `compressed-streams` in Phase 8. |
| 3 | Base reader interface cleanup | `archive-reading`, `backend-registry` |
| 4 | `ExtractionHelper` → `ExtractionCoordinator` rewrite | `safe-extraction` (incl. decompression-bomb limits and progress/result reporting) |
| 5 | Public API alignment to SPEC.md | `archive-data-model`, `access-intent-and-cost`, `error-handling`, `archive-reading` |
| 6 | Test infrastructure overhaul | `testing-contract` |
| 7 | Writing support | `archive-writing` (+ `format-zip` / `format-tar` writers) |
| 8 | Native 7z reader + native RAR metadata parser | `format-7z`, `format-rar` (native-first: drop `py7zr`/`rarfile` from the read path; `unrar` binary stays for RAR data; `py7zr` for 7z write only) |
| 9 | Zstandard + extended compression | `format-single-file-compressors`, `format-tar`, `format-detection` |
| 10 | Polish, documentation, packaging | `cli`, `packaging-and-extras` (`__version__`, `list_formats()`) (+ cross-cutting: README, CI, coverage) |

`logging` is cross-cutting and not owned by a single phase — the named-logger
hierarchy is established in Phase 1 and used by every phase thereafter.

> **Note:** decompression-bomb limits and extraction progress/result reporting
> were previously separate `bomb-protection` and `progress-and-logging` specs.
> They have been folded into `safe-extraction` (both are extraction-time
> guarantees, now scheduled under Phase 4); the cross-cutting logging concern was
> split out into the standalone `logging` spec.

> **Open sequencing question (7z/RAR):** because the read path is native-first,
> Phase 1 must decide whether to (a) port DEV's `py7zr`/`rarfile` read backends as
> an interim baseline (full 7z/RAR support early, thrown away at Phase 8) or
> (b) skip them in the baseline and mark 7z/RAR tests `xfail` until the native
> readers land in Phase 8. To resolve when drafting the Phase 8 change.

## Deferred / out of scope (v1)

- In-place archive modification (append/update).
- Encryption write for 7z/RAR.
- Native sparse-file extraction (detected and flagged, extracted dense).
- NTFS junction recreation on non-Windows.
- Joining multi-volume archives (reported via `is_multivolume`, joining left to caller).
- Async API.
