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
| `archive-reading` | `open()`, the `ArchiveReader` surface, iteration, random/sequential access, link following |
| `archive-writing` | `create()`, the `ArchiveWriter` surface, streaming conversion |
| `archive-data-model` | `Member`, `ArchiveInfo`, `ArchiveFormat`, `MemberType`, compression types |
| `access-intent-and-cost` | `Intent` enum and the `CostReceipt` cost surface |
| `safe-extraction` | `extract()`, extraction policies, the non-bypassable filter contract |
| `bomb-protection` | Decompression-bomb byte/ratio limits |
| `format-detection` | `detect_format()`, magic table, non-seekable peek/replay |
| `backend-registry` | Backend registration, selection, the `Backend` ABC, optional deps |
| `error-handling` | The `ArchiveyError` hierarchy and error-translation contract |
| `progress-and-logging` | Extraction progress/result reporting and logging namespaces |
| `format-zip` / `format-tar` / `format-single-file-compressors` / `format-7z` / `format-rar` / `format-iso` / `format-directory` | Per-format behavioral contracts |
| `seekable-decompressor-streams` | Random access inside single compressed streams |
| `testing-contract` | Equivalence matrix, adversarial corpus, round-trip and non-seekable coverage |
| `cli` | The `archivey` command-line interface |

## Implementation order

The build sequence is a **selective rewrite** starting from the existing
`archivey-dev` codebase — see `PLAN.md` (repo root) for the detailed,
phase-by-phase task list and acceptance criteria. The plan is organized as a
DEV-migration sequence rather than a build-each-capability sequence, so a phase
typically advances several capabilities at once. Specs themselves remain
order-free; this table is the association between the two.

| Phase | Theme | Primary capabilities advanced |
|-------|-------|-------------------------------|
| 1 | Project scaffold + verbatim port from DEV | *(infra)* — ports all `format-*` backends and `format-detection` |
| 2 | Stream layer reorganization | `seekable-decompressor-streams`, `archive-reading` *(internal streams)* |
| 3 | Base reader interface cleanup | `archive-reading`, `backend-registry`, `format-7z`, `format-rar` |
| 4 | `ExtractionHelper` → `ExtractionCoordinator` rewrite | `safe-extraction` |
| 5 | Public API alignment to SPEC.md | `archive-data-model`, `access-intent-and-cost`, `error-handling`, `archive-reading` |
| 6 | Test infrastructure overhaul | `testing-contract` |
| 7 | Writing support | `archive-writing` (+ `format-zip` / `format-tar` writers) |
| 8 | 7z & RAR streaming improvements | `format-7z`, `format-rar` |
| 9 | Zstandard + extended compression | `format-single-file-compressors`, `format-tar`, `format-detection` |
| 10 | Polish, documentation, packaging | `cli` (+ cross-cutting: README, CI, coverage) |

**Gaps where the plan and specs do not yet line up** (to resolve when refining):

- `bomb-protection` is scheduled by no phase. It is a clean-slate safety
  addition DEV never had; it naturally attaches to Phase 4 (extraction) or
  warrants a phase of its own.
- `progress-and-logging` appears only implicitly — DEV already has logging and a
  tqdm-based progress bar, but the `on_progress` callback and `ExtractionResult`
  are net-new and would ride along in Phase 4/5.

## Deferred / out of scope (v1)

- In-place archive modification (append/update).
- Encryption write for 7z/RAR.
- Native sparse-file extraction (detected and flagged, extracted dense).
- NTFS junction recreation on non-Windows.
- Joining multi-volume archives (reported via `is_multivolume`, joining left to caller).
- Async API.
