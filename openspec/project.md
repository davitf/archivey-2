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

## Deferred / out of scope (v1)

- In-place archive modification (append/update).
- Encryption write for 7z/RAR.
- Native sparse-file extraction (detected and flagged, extracted dense).
- NTFS junction recreation on non-Windows.
- Joining multi-volume archives (reported via `is_multivolume`, joining left to caller).
- Async API.
