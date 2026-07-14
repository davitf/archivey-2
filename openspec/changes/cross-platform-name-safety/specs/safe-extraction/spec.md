## ADDED Requirements

### Requirement: Cross-platform name safety is deterministic across policy levels

Extraction SHALL handle destination-name hazards deterministically on every platform,
keyed off `ExtractionPolicy`, so the same archive yields the same logical outcome
(collision events, rejections, normalized spellings) regardless of the runner OS. These
rules compose with — and never bypass — the non-bypassable path-safety constraints.

**Collision determinism (O2).** The coordinator SHALL track a `casefold(NFC(path))` key per
written destination and treat a second member resolving to the same key as an existing
destination on **all** platforms, applying `OverwritePolicy` deliberately and recording the
collision on the member's `ExtractionResult`. `REPLACE` SHALL NOT silently merge distinct
members on case-insensitive filesystems. `OverwritePolicy.RENAME` (extract as `name (1)`)
is reserved for intentional-duplicate archives.

**Portable-name enforcement (O3/O4).** Under `STRICT`, Windows-reserved device names
(`CON`, `PRN`, `AUX`, `NUL`, `COM1`–`COM9`, `LPT1`–`LPT9`; case-insensitive, with or
without extension), a trailing dot or space in any path segment, and `:` within a segment
SHALL be rejected on **every** platform. `TRUSTED` SHALL defer to the local OS. `STANDARD`
SHALL reject the unambiguously-dangerous set (reserved names, `:`) and MAY allow trailing
dot/space.

**Portable-name representability (O7).** Under `STRICT`, a name that cannot be represented
portably on the destination filesystem SHALL be handled by the scheme chosen in this
change's design (reject, or normalize to a deterministic reversible portable spelling that
is collision-tracked as above); `TRUSTED` SHALL attempt the faithful bytes and let the OS
decide. Either way the outcome SHALL be deterministic and typed (never a bare `OSError`).

#### Scenario: cross-platform name matrix

| Case | `STRICT` | `TRUSTED` |
| --- | --- | --- |
| `README` and `readme` in one archive | Second is a collision event on all platforms; `OverwritePolicy` applied; recorded on result | Local OS behavior |
| NFC `café` and NFD `café` | Treated as a collision on all platforms | Local OS behavior |
| Member named `NUL` / `COM1` | Rejected on all platforms (typed error) | Written if the OS allows |
| Trailing dot/space (`foo.`, `foo `) | Rejected on all platforms | Written if the OS allows |
| Name containing `:` (`file:hidden`) | Rejected on all platforms | Local OS behavior (NTFS ADS) |
| Surrogateescape `caf\udce9.txt` on APFS | Deterministic typed outcome per the chosen O7 scheme | Faithful bytes attempted; OS decides |
| `REPLACE` with a casefold collision | Collision handled per policy, not a silent merge | Local OS behavior |
