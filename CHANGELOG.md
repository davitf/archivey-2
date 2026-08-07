# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

How to update this file for a release: see
[`dev-docs/release-checklist.md`](dev-docs/release-checklist.md)
(commit walk since the previous tag + performance numbers vs that release).

## [Unreleased]

First public release will be **0.2.0**. Until then, notable work accumulates here;
at cut time the checklist moves this section under a dated `## [0.2.0]` heading.

This repository is the **v2** rewrite. The earlier v1 / alpha line (previously
published from what is now
[`davitf/archivey-old`](https://github.com/davitf/archivey-old)) is a separate
codebase — not a SemVer predecessor of this tree. There is no compatibility
promise with that line; treat `0.2.0` as the first release of this library.

### Added

- Unified archive reading for ZIP, TAR, RAR, 7z, ISO, directory trees, and
  single-file compressed streams (gzip / bzip2 / xz / lzip / zstd / lz4 / compress).
- Safe extraction defaults (`archivey.extract`) with policy-driven path and
  overwrite controls; CLI (`archivey list|test|extract`) as a safer unzip demo.
- Native 7z and RAR metadata readers (stdlib codecs for common 7z filters;
  external `unrar` for RAR member data).
- Four optional extras — `[recommended]` (every format and codec that installs
  everywhere), `[seekable]` (rapidgzip), `[free-threaded]` (the measured GIL-safe
  subset), and `[all]`. There is deliberately no extra per format: member codecs are
  shared across containers. See `docs/formats.md`.
- Declarative corpus + mutation / Hypothesis / Atheris testing contract;
  three-configuration CI (`[all]`, `[all-lowest]`, `[core-only]`).
- Benchmark harness: PR structural gate + change-guarded nightly wall-ratio drift.
- `FormatAvailability.required_source` — the weakest source shape a format can be read
  from, so "can I read this straight from a pipe?" is a query instead of a
  `StreamNotSeekableError` to catch. `StreamCapability` is now ordered
  (`FORWARD_ONLY < SEEKABLE`), so the test is
  `availability.required_source <= reader.cost.stream_capability`.

### Changed

- **`password=` no longer raises on a format with no encryption.** All three forms — a
  single value, a list of candidates, a `PasswordProvider` — are now accepted, never
  consulted, and recorded as a `PASSWORD_ARGUMENT_UNUSED` diagnostic. Previously a
  static value or a list raised `UnsupportedOperationError` while a provider callable
  opened fine; the permissive behaviour already existed and was reachable only by
  wrapping your password list in a lambda. `password=` is a keyring offered, not an
  assertion that this archive is encrypted, and a batch caller passing one keyring
  across mixed input should not fail on the one plain `.tar`. A *wrong* password on an
  *encrypted* archive still raises `EncryptionError`.
- **`STREAM_REWIND_REDECOMPRESSES` is now cost-based.** It used to fire on the codec's
  *identity*, decided once at open, so xz / lzip / unix-compress never emitted — even
  though a single-block `.xz` (what `lzma.compress` and un-threaded `xz` produce) has one
  seek point at the origin and rewinds exactly like a codec with no index. Measured while
  fixing this, `rapidgzip`'s index has the same property: three block offsets across a
  5 MB stream, so a backward seek can discard megabytes with the accelerator engaged and
  the old rule said nothing there either. The predicate is now the decoded progress the
  rewind discards, against an absolute 1 MiB threshold, uniformly across codecs — so
  small rewinds that used to warn are now quiet, and large ones that used to be silent
  now report. The diagnostic is still **recorded** once per stream, but a `RAISE` policy
  is now evaluated on **every** qualifying seek: a tripwire that disarms after firing once
  is not a tripwire.
- **`strict_archive_eof=True` now asserts what it documents.** It used to check only
  that the two-block TAR trailer was present, so 4 KiB of arbitrary appended bytes passed
  silently under the flag you set for "a provably complete listing". Every byte from the
  trailer to EOF must now be zero; the first non-zero one emits the new
  `ARCHIVE_TRAILING_DATA` diagnostic and raises `CorruptionError`. Zero padding still
  passes (`tar` writes 10 KiB records), and concatenated archives now fail — deliberately,
  since they are two archives and only the first was listed. **The flag is now
  O(tail length)** rather than O(512 bytes), and on a compressed tar the tail is
  decompressed to inspect it; `strict_archive_eof=False` is unchanged, including the cost.
- Six new diagnostic codes (simplicity & consistency review): `EMPTY_ARCHIVE`,
  `EXTENSION_FORMAT_UNCONFIRMED`, `EXPLICIT_FORMAT_LISTED_EMPTY`,
  `PASSWORD_ARGUMENT_UNUSED`, `ENCODING_ARGUMENT_UNUSED`, and
  `MEMBER_NAME_BIDI_CONTROL` — which promotes the library's last log-only advisory to
  queryable, escalatable data.
- `encoding=` passed to a backend that decodes names another way (7z, RAR, ISO,
  directory, single-file) is still accepted, but the discard is now recorded rather
  than silent.
- Four refusals that crossed the API untyped or mistyped now match the spelling the
  rest of the library already uses (simplicity & consistency review, F3/F4/F11):
  `open_archive([])` and an empty volume-path sequence raise `ArchiveyUsageError`
  instead of a bare `ValueError`; a non-seekable volume in a sequence raises
  `StreamNotSeekableError`, matching the single-source refusal; closing the handle
  under a live ZIP reader raises `ArchiveyUsageError` instead of `CorruptionError`
  (a lifecycle fault, not archive damage); and `open_stream()` on a directory says so
  instead of `FileNotFoundError: Compressed stream not found`.
- `member.compressed_size` on a single-file compressed archive is filled from any
  **seekable** source, not only from a `Path` — the same rule the trailer/CRC probes
  beside it already used. A non-seekable source still reports `None`.
- `seekable_members` no longer changes member **metadata** (review F1). The `.xz` stream
  index and the `.lz` trailer are read from any seekable source, so `member.size` and
  `member.hashes` are the same with and without the flag — `.lz` now reports its
  whole-member CRC-32 on a plain `open_archive()`, which is what the dedupe use case
  wanted. A pipe still reports `size=None` and no digest; nothing forces a decode pass.
- Performance claims are **aspirational peer-ratio bands** with a published
  measured table in `docs/access-and-cost.md` / `VISION.md` (nightly realistic ratios;
  refresh at release time per the checklist).
- GitHub repository renamed from `archivey-2` → `archivey` (canonical name);
  the prior v1 repo was renamed to `archivey-old`.

### Security

- **Bidi override filenames are refused during extraction.** A member name or link
  target containing a Unicode bidi override or isolate (U+202A–202E, U+2066–2069) is
  rejected with the new `DeceptiveNameError` — those characters reorder surrounding
  text, which is how `evil‮gnp.exe` displays as a `.png`. The three *directional marks*
  (U+061C, U+200E, U+200F) are deliberately **not** rejected: they reorder nothing and
  appear in legitimate Arabic and Hebrew filenames. Listing and reading still present
  every name exactly as stored, with `MEMBER_NAME_BIDI_CONTROL`.
- Threat model and open residuals: `dev-docs/threat-model.md`.
- Root [`SECURITY.md`](SECURITY.md) — private vulnerability reporting via
  [GitHub Security Advisories](https://github.com/davitf/archivey/security/advisories/new),
  scope, and guidance that optional `[seekable]` accelerators are not part of
  the defended fuzz surface for hard-latency untrusted input.

<!--
After 0.2.0 is tagged, add:

## [0.2.0] - YYYY-MM-DD

…and link compare URLs at the bottom, e.g.:

[Unreleased]: https://github.com/davitf/archivey/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/davitf/archivey/releases/tag/v0.2.0
-->
