## ADDED Requirements

### Requirement: Cross-platform name safety is deterministic across policy levels

Extraction SHALL handle destination-name hazards deterministically on every platform,
keyed off `ExtractionPolicy`, so the same archive yields the same logical outcome
(collision events, rejections, normalized spellings) regardless of the runner OS. These
rules compose with — and never bypass — the non-bypassable path-safety constraints.

**Collision determinism (O2).** Under `STRICT` and `STANDARD`, the coordinator SHALL track
a `casefold(NFC(path))` key per written destination and treat a second member resolving to
the same key as an existing destination on **all** platforms, applying `OverwritePolicy`
deliberately, recording `requested_path` on the member's `ExtractionResult`, and emitting an
`EXTRACTION_NAME_COLLISION` diagnostic. `REPLACE` SHALL NOT silently merge distinct members
on case-insensitive filesystems (the diagnostic fires even though the result is `EXTRACTED`).
Under `TRUSTED` the coordinator SHALL key on the exact `Path` and defer to the local OS
(today's behavior), so genuinely distinct files on a case-sensitive filesystem both extract.

`OverwritePolicy` SHALL add a `RENAME` member that extracts a colliding entry under a
deterministic derived name, using the same collision key, for archives with intentional
duplicates. The derived name SHALL insert ` (N)` (`N` = 1, 2, …) **before the final suffix**
(`Path.stem` + `Path.suffix` semantics): `photo.jpg` → `photo (1).jpg`; a name with no
suffix → `photo (1)`; a leading-dot dotfile (`.bashrc`) → `.bashrc (1)` (the leading dot is
not treated as a suffix); a multi-suffix name (`archive.tar.gz`) → `archive.tar (1).gz`
(single final suffix); a directory appends to the whole segment. `N` SHALL increment to the
first name free **both on disk and in the collision map**, in member-processing order.

**Portable-name enforcement (O3/O4).** Windows-reserved device names (`CON`, `PRN`, `AUX`,
`NUL`, `COM1`–`COM9`, `LPT1`–`LPT9`; case-insensitive, with or without extension) and `:`
within a segment are **unsafe** (device capture / NTFS alternate data stream) and SHALL be
rejected under `STRICT` and `STANDARD` on **every** platform. A trailing dot or space is a
legitimate macOS/Linux name that Win32 merely trims; rejecting it would halt a legitimate
archive, so under `STRICT` each path segment's trailing dot/space SHALL be **stripped** to
its portable spelling (`stuff_etc.` → `stuff_etc`) deterministically on every platform,
collision-tracked as above, and surfaced as an `EXTRACTION_NAME_SANITIZED` diagnostic; a
segment that is entirely dots/spaces (e.g. `...`) has no portable spelling and SHALL be
rejected. `STANDARD` and `TRUSTED` SHALL keep the trailing dot/space faithful (written if
the OS allows).

**Portable-name representability (O7).** Under `STRICT` and `STANDARD`, a name carrying
bytes that cannot be represented portably on the destination filesystem SHALL be normalized
to a deterministic, reversible portable spelling — each non-UTF-8 byte (a surrogateescape
char U+DC80–U+DCFF mapping to raw byte 0x80–0xFF) percent-escaped as `%XX` (uppercase hex),
and a literal `%` escaped as `%25` — applied on **every** platform and collision-tracked as
above. The scheme SHALL touch only non-decodable bytes; valid-but-non-portable Unicode
(NFC/NFD forms) SHALL NOT be rewritten (its cross-platform folding is the O2 collision
concern). `TRUSTED` SHALL attempt the faithful bytes and let the OS decide. The reversibility
SHALL be a documented property; a public un-escape API is out of scope. Either way the
outcome SHALL be deterministic and typed (never a bare `OSError`); a name that cannot be
`os.fsencode`d at all remains rejected by the universal check.

`ExtractionResult` SHALL gain a `requested_path: Path | None` field carrying the destination
the coordinator intended before overwrite/rename resolution. A rename SHALL be observable as
`requested_path != path and status == EXTRACTED`; a collision resolved by `SKIP`/`ERROR`
SHALL set `requested_path` with `path=None`. The field defaults to `None` and is appended to
the frozen dataclass (backward-compatible).

#### Scenario: cross-platform name matrix

| Case | `STRICT` / `STANDARD` | `TRUSTED` |
| --- | --- | --- |
| `README` and `readme` in one archive | Second is a collision event on all platforms; `OverwritePolicy` applied; `requested_path` + collision diagnostic recorded | Local OS behavior (both extract on a case-sensitive FS) |
| NFC `café` and NFD `café` | Treated as a collision on all platforms | Local OS behavior |
| Member named `NUL` / `COM1` | Rejected on all platforms (typed error) | Written if the OS allows |
| Trailing dot/space (`foo.`, `foo `) | `STRICT` strips to portable spelling (`foo`), diagnostic; `STANDARD` keeps faithful | Written if the OS allows |
| Segment of only dots/spaces (`.../x`) | Rejected on all platforms (no portable spelling) | Written if the OS allows |
| Name containing `:` (`file:hidden`) | Rejected on all platforms | Local OS behavior (NTFS ADS) |
| Surrogateescape `caf\udce9.txt` | Sanitized to `caf%E9.txt` deterministically on every platform; collision-tracked | Faithful bytes attempted; OS decides |
| `REPLACE` with a casefold collision | Handled per policy, not a silent merge; `EXTRACTION_NAME_COLLISION` diagnostic emitted | Local OS behavior |
| `RENAME` with a collision (case/NFC or exact) | Second entry written as `name (1)` before the suffix, deterministically; `requested_path` = intended name | Same |
