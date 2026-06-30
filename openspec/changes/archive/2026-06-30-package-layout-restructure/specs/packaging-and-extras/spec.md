# Packaging and Extras — delta (package-layout-restructure)

## ADDED Requirements

### Requirement: Source package layout separates public API from implementation

The installable `archivey` package SHALL organize modules so that:

1. **Public API modules** live at the package root and are the only modules whose symbols
   appear in `archivey.__all__`: `core.py` (entry points and registry queries),
   `types.py` (data model), `exceptions.py` (error hierarchy), `cost.py` (`CostReceipt`
   and related enums), and `reader.py` (the public `ArchiveReader` ABC).
2. **`archivey.__init__.py`** SHALL re-export the public API and SHALL NOT require callers
   to import from `archivey.internal.*` for supported usage.
3. **Implementation code** SHALL live under `archivey.internal.*`, which is not a supported
   import surface for external callers and carries no backwards-compatibility guarantee.
4. **Format backends** SHALL live under `archivey.internal.backends.*` (not at the package
   root). Backend modules register with the registry at import time; importing the top-level
   `archivey` package SHALL still register all bundled backends.
5. **The codec/stream layer** SHALL remain under `archivey.internal.streams.*`.

Phase 4 modules (`internal/extraction.py`, `internal/filters.py`) follow the same rule:
implementation under `internal/`, public extraction types and `extract()` on `core.py` /
`types.py`.

#### Scenario: supported usage imports only the top-level package

- **WHEN** application code uses the documented API (`open_archive`, `ArchiveMember`, …)
- **THEN** `import archivey` (or explicit imports from `archivey` re-exports) suffices
- **AND** no import from `archivey.internal` is required

#### Scenario: backends are not a public subpackage

- **WHEN** a caller attempts `import archivey.internal.backends.zip` (or the old
  `archivey.formats.zip_reader`)
- **THEN** that path is not documented, not in `__all__`, and not a stability promise
  (it may work for debugging but is not part of the public contract)

#### Scenario: import archivey registers bundled backends

- **WHEN** `import archivey` is executed in a core-only environment
- **THEN** `list_supported_formats()` returns the bundled format set without an prior
  `open_archive()` call
