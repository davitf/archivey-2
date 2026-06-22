# Single-File Compressor Format Behavior — delta (Phase 3)

This change fixes the *structure* of the single-file support: it is one multi-format
backend, not a backend class per compressor. The behavioral requirements already in
the capability spec (member naming, per-format size rules, gzip `raw_filename`, cost)
are unchanged and realized by this backend.

## ADDED Requirements

### Requirement: A single multi-format backend serves every single-file compressor

The system SHALL implement single-file compressor reading as **one** `ReadBackend`
(`SingleFileBackend`) whose `FORMATS` tuple lists every standalone-stream codec, not a
separate backend class per format. The backend is codec-agnostic: it infers the member
name and metadata shell, then delegates decompression to the `compressed-streams`
codec layer resolved from the member's stream codec. This keeps the per-format logic to
a small set of **per-codec metadata hooks** rather than parallel reader classes, and
means a newly added standalone codec becomes readable by registering the codec, adding
its `ArchiveFormat`/`StreamFormat` enum value, and adding its detection entry — with no
new backend code.

- The per-codec metadata hooks SHALL be a dispatch table keyed by codec, not an
  `if format == …` chain. Each hook fills the format-specific fields the capability
  already specifies: gzip's `FNAME` → `raw_filename` (and optional mtime); xz/zst
  header size; lz4 frame size; lzip trailer size; and the size-availability rules
  (`gz` always `None`; `bz2`/`zlib`/`br`/`Z` `None` until full decompression). A codec
  with no extra metadata simply registers no hook.
- The decodability of any single-file format follows from its **codec backend's**
  availability (per `backend-registry`'s compositional support), not from a
  per-format backend's presence: the `SingleFileBackend` itself is always registered;
  a format whose sole codec backend is missing is reported as support `NONE`.

#### Scenario: one backend reads multiple compressors

- **WHEN** `.gz`, `.bz2`, and `.xz` sources are opened
- **THEN** each is served by the same `SingleFileBackend` instance class (its `FORMATS` includes GZIP, BZIP2, and XZ), each yielding exactly one `FILE` member with the correct per-codec metadata

#### Scenario: a new standalone codec needs no new backend

- **WHEN** a new standalone codec is added to the `compressed-streams` registry with a matching `ArchiveFormat`/`StreamFormat` value and a detection entry
- **THEN** that format is readable as a single-file archive through the existing `SingleFileBackend` without adding a new `ReadBackend` subclass
- **AND** its availability is reported by `format_availability()` from the new codec backend's presence
