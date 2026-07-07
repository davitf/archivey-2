# testing-contract delta — retire-dev-oracle

## ADDED Requirements

### Requirement: Corpus conformance sweep

The test suite SHALL include a single parametrized conformance sweep driven by the
declarative archive corpus: every corpus entry whose format is currently implemented
MUST open via `open_archive()`, list members matching the entry's declared expected
contents (names, types, sizes, link targets), and extract cleanly to a temporary
directory under the default safety policy with contents verified — or, for an entry that
declares an expected failure (encrypted without a password, unsupported variant,
adversarial member), raise exactly the documented `ArchiveyError` subclass. Corpus
entries for formats that are not yet implemented (7z/RAR before Phase 7) SHALL be
carried in the corpus but skipped by the sweep via a registry-driven guard, so enabling
a format activates its entries without re-porting. Entries needing an absent optional
dependency SHALL skip, not fail.

The corpus SHALL cover at least the archive shapes present in the DEV declarative corpus
for the implemented formats (multi-member trees, unicode and non-UTF-8 names, symlinks/
hardlinks, duplicate names, empty archives and empty members, per-format metadata
quirks), and the corpus module SHALL record the DEV commit hash the shapes were ported
from.

#### Scenario: corpus archive round-trips through the sweep

- **WHEN** the sweep runs a corpus entry for an implemented format
- **THEN** the archive opens, its member listing matches the declared expectations, and extraction to a temp directory succeeds with verified contents

#### Scenario: corpus entry with a documented failure

- **WHEN** the sweep runs a corpus entry declared to fail (e.g. encrypted, no password)
- **THEN** the documented `ArchiveyError` subclass is raised and the sweep passes

#### Scenario: unimplemented-format entries are skipped, then activate

- **WHEN** the sweep encounters a 7z or RAR corpus entry before the native readers exist
- **THEN** the entry is skipped via the registry-driven guard (and runs once the format's reader registers)

### Requirement: Frozen DEV oracle retired

The frozen DEV oracle tree (`tests/_dev_oracle/`) SHALL NOT exist: its durable assets —
the declarative corpus shapes (ported into the v2 corpus) and the oracle libraries
(py7zr/rarfile and the `7z`/`unrar` CLIs, which remain dev-group cross-validation
oracles per the cross-validation requirement) — are preserved elsewhere, and the dead
v1-API test drivers are deleted rather than maintained. Tooling configuration SHALL
carry no special-case exclusions for the oracle tree.

#### Scenario: no oracle tree or exclusions remain

- **WHEN** the repository is searched for `_dev_oracle`
- **THEN** no test tree and no pytest/ruff/type-checker exclusion entries reference it
