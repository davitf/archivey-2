# Backend Registry — delta (codec-descriptor refactor)

## MODIFIED Requirements

### Requirement: Codec availability and install hints come from the descriptor

A format's compositional support and its missing-component install hints SHALL be derived
from the codec descriptors' `requirement` fields, so a codec's package / extra / install
hint / unlocked capability is declared in exactly one place. The separate
`_CODEC_REQUIREMENT` table SHALL be removed, and the tri-state FULL / PARTIAL / NONE results
for every format MUST be unchanged from before the refactor.

#### Scenario: a codec's install hint is declared once

- **WHEN** a single-codec format's sole codec backend is missing
- **THEN** `format_availability()` reports `NONE` with the install hint taken from that codec's descriptor `requirement`, not from a duplicate registry table

#### Scenario: multi-codec container support is unchanged

- **WHEN** `format_availability()` is queried for ZIP / 7z / a `tar.<codec>` on a system missing some optional member codecs
- **THEN** the support level and missing-component list are identical to before, computed from the same codec descriptors
