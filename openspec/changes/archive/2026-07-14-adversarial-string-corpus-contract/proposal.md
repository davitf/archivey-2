## Why

The Unicode-bomb row in `testing-contract` needs construction-level evidence that each
archive actually carries the advertised bytes and flags, while the repository's current
generated-fixture policy conflicts with older prose that describes committed adversarial
binaries. NUL-bearing link targets also need a defined fail-safe outcome before filesystem
path APIs receive them.

## What Changes

- Generate clean adversarial ZIP/TAR bases deterministically in memory and commit no
  regenerable binary output.
- Mutate ZIP names, comments, and STORED symlink data at their actual fields, repairing
  CRCs and asserting local/central UTF-8 flags independently.
- Require one warning when an `ArchiveMember` name contains a bidirectional formatting
  control, regardless of which backend produced the member.
- Reject NUL-bearing link targets as `SymlinkEscapeError` before resolving or creating the
  link.
- Reconcile `ARCHITECTURE.md` with the generated-on-demand fixture policy.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `testing-contract`: Clarify that regenerable adversarial archives are generated on
  demand and that RTL-warning coverage applies uniformly to presented members.
- `safe-extraction`: Define the typed rejection for a NUL byte in a link target.

## Impact

- Test corpus generation and assertions under `tests/create_adversarial.py` and
  `tests/test_adversarial_corpus.py`.
- Central member registration in `BaseArchiveReader`; no public API or dependency change.
- Universal extraction filtering for malformed link targets.
- Test-layout prose in `ARCHITECTURE.md`.
