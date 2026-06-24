# Compressed Streams — delta (codec-descriptor refactor)

## ADDED Requirements

### Requirement: A codec is described by one StreamCodec descriptor

The system SHALL represent each single-stream codec as a single descriptor object that
carries its open function, exception translator, magic signatures (including the `weak`
flag), whether it is recognized by a content probe, its standalone file extensions, an
optional metadata extractor that fills `ArchiveMember` fields, and its optional-dependency
requirement (package / extra / external tool + install hint + unlocked capability). A new
standalone codec SHALL become fully readable and detectable by registering one descriptor,
without edits to the detector, the single-file reader, or the registry's availability code.
The descriptor registry MUST NOT eagerly import optional codec libraries, so the zero-dep
core stays importable with no third-party packages.

#### Scenario: adding a standalone codec is a one-descriptor change

- **WHEN** a new single-stream codec descriptor is registered (open fn, translator, magic/probe, extension, requirement)
- **THEN** `detect_format()` recognizes it, `SingleFileBackend` reads it as a one-member archive, and `format_availability()` reports its support — with no other code changes

#### Scenario: descriptors do not pull in optional libraries at import

- **WHEN** `archivey` is imported in a core-only environment (no optional codec packages)
- **THEN** building the descriptor registry raises no `ImportError` and imports no third-party codec package
