# Documentation Generation

## Purpose

Archivey publishes an API reference generated **from the source itself** — the public
types, their docstrings, and their field/enum-member docstrings — so the documentation
cannot drift from the code. The toolchain is MkDocs + mkdocstrings (Griffe), with a few
small Griffe extensions that make the dataclass- and enum-heavy public API render well.

## Requirements

### Requirement: Generate the API reference from source with MkDocs + mkdocstrings

The system SHALL provide a MkDocs site (`mkdocs.yml` + `docs/`) whose API reference is
produced by the `mkdocstrings` Python handler (Griffe) reading the `src/` package, so the
reference is derived from the code and its docstrings rather than hand-maintained. The
site SHALL be buildable with a single command (`mkdocs build`), using a dedicated `docs`
dependency group so the toolchain is isolated from the default lint/test environment.

#### Scenario: building the site renders the public API

- **WHEN** `mkdocs build` is run with the `docs` dependency group installed
- **THEN** the site builds successfully and the API-reference page documents the public
  symbols re-exported from the top-level `archivey` package (those listed in `__all__`)

### Requirement: Render dataclass fields and enum members from their docstrings

The system SHALL surface the docstrings attached to dataclass fields and enum members in
the generated reference. Dataclass fields SHALL be rendered as a "Fields" table (via
`griffe-fieldz` plus a Griffe extension that titles the section accordingly), and enum
members SHALL be rendered as a Name/Value/Description table via a Griffe extension and a
custom mkdocstrings template (the built-in sections render a "Type" column, which is wrong
for enums). `@property` accessors SHALL be folded into the owning class's field table
rather than each getting a separate section.

#### Scenario: an enum renders its members as a table

- **WHEN** the reference page for an enum such as `ListingCost` is generated
- **THEN** each member appears in a table with its name, value, and docstring (e.g.
  `INDEXED` with "An index / central directory is present; listing is O(1) …")

#### Scenario: a dataclass renders its field docstrings

- **WHEN** the reference page for a dataclass such as `CostReceipt` or `ArchiveMember` is
  generated
- **THEN** each field's docstring is shown in the class's "Fields" table

### Requirement: The documentation build is verified in CI

The system SHALL build the documentation in CI with warnings treated as errors
(`mkdocs build --strict`), so that a broken cross-reference, a removed/renamed public
symbol, or a rendering warning fails the build like any other check.

#### Scenario: a broken reference fails CI

- **WHEN** the docs reference a symbol that no longer exists, or a cross-reference cannot
  be resolved
- **THEN** the strict docs build fails, surfacing the drift in CI
