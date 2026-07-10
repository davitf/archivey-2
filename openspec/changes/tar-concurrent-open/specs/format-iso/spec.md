# Format ISO — delta (tar-concurrent-open)

## ADDED Requirements

### Requirement: ISO concurrent member open via locked pycdlib streams

The system SHALL support interleaved concurrent member data streams from one ISO reader
when the caller has opted in to multiple open streams (`allow_multiple_open_streams`, per
`concurrent-open-opt-in`). Without the opt-in, a second overlapping open raises uniformly.
The reader MUST continue to obtain file member payloads through `pycdlib` (e.g.
`open_file_from_iso`) and MUST wrap each returned member stream so that every data-path
read holds a **per-archive lock** for the duration of the library `read`, serializing
pycdlib's seek-before-read on the shared image handle.

#### Scenario: interleaved opens on ISO

- **WHEN** `allow_multiple_open_streams` is enabled and two file members of an ISO image
  are opened and read interleaved
- **THEN** each stream yields that member's exact bytes in order
