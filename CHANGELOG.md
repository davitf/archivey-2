# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

How to update this file for a release: see
[`docs/internal/release-checklist.md`](docs/internal/release-checklist.md)
(commit walk since the previous tag + performance numbers vs that release).

## [Unreleased]

First public release will be **0.2.0**. Until then, notable work accumulates here;
at cut time the checklist moves this section under a dated `## [0.2.0]` heading.

### Added

- Unified archive reading for ZIP, TAR, RAR, 7z, ISO, directory trees, and
  single-file compressed streams (gzip / bzip2 / xz / lzip / zstd / lz4 / compress).
- Safe extraction defaults (`archivey.extract`) with policy-driven path and
  overwrite controls; CLI (`archivey list|test|extract`) as a safer unzip demo.
- Native 7z and RAR metadata readers (stdlib codecs for common 7z filters;
  external `unrar` for RAR member data).
- Optional extras: `[seekable]` (rapidgzip), `[crypto]`, `[7z]` (PPMd / Deflate64),
  and related packaging matrix — see `docs/formats.md`.
- Declarative corpus + mutation / Hypothesis / Atheris testing contract;
  three-configuration CI (`[all]`, `[all-lowest]`, `[core-only]`).
- Benchmark harness: PR structural gate + change-guarded nightly wall-ratio drift.

### Changed

- Performance claims are **aspirational peer-ratio bands** with a published
  measured table in `docs/costs.md` / `VISION.md` (nightly realistic ratios;
  refresh at release time per the checklist).

### Security

- Threat model and open residuals: `docs/internal/threat-model.md`.
- Disclosure process: add `SECURITY.md` before the public “safe” claim
  (debt-ledger D2 — still open at changelog creation).

<!--
After 0.2.0 is tagged, add:

## [0.2.0] - YYYY-MM-DD

…and link compare URLs at the bottom, e.g.:

[Unreleased]: https://github.com/davitf/archivey/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/davitf/archivey/releases/tag/v0.2.0
-->
