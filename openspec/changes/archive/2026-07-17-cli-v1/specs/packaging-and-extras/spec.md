## ADDED Requirements

### Requirement: archivey console entry points ship with the base package

The system SHALL install an `archivey` console script and support
`python -m archivey` from a base (no-extra) install. The `[cli]` extra SHALL
continue to pull `tqdm` for progress output only; absence of `[cli]` MUST NOT
remove the command entry points.

#### Scenario: entry points vs progress extra

| Case | Expected |
| --- | --- |
| `pip install archivey` then `archivey --version` / `python -m archivey --version` | Command runs; version prints |
| `[cli]` / `tqdm` not installed | Command runs; progress bars suppressed |
| `pip install archivey[cli]` | Progress available when the CLI would show a bar |
