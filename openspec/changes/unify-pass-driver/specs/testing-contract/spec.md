## ADDED Requirements

### Requirement: Mutation harness covers solid demux paths

The corpus mutation harness SHALL exercise at least one **solid** RAR4 and one
**solid** RAR5 archive under the same typed-error-or-success invariant as
declarative-corpus mutations (truncate, bitflip, zero, junk). Static curated
fixtures MAY supply those archives when the declarative builder does not emit
solid RAR. When `unrar` is absent, solid-RAR mutation cases SHALL skip cleanly
rather than fail.

#### Scenario: solid RAR mutation matrix

| Case | Expected |
| --- | --- |
| Solid RAR5 fixture × each mutation kind | Success or typed `ArchiveyError`; never raw third-party exception / hang |
| Solid RAR4 fixture × each mutation kind | Same invariant |
| `unrar` not on PATH | Solid-RAR mutation cases skipped |
| Declarative nonsolid CORPUS RAR | Unchanged existing coverage |
