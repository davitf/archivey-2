## Context

Native RAR listing currently skips RAR3 `FILE_VERSION` and RAR5 extra `0x04`
rows (same as `rarfile`). Those rows are real FILE payloads WinRAR keeps under
`-ver`. `safe-extraction` already skips `is_current=False` on extract; 7z already
uses that flag for last-entry-wins. Main `format-rar` does not yet normatively
require omit or expose — behavior is implementation-only.

## Goals / Non-Goals

**Goals:**
- Expose RAR file-version history in the member list as first-class
  `ArchiveMember`s.
- Keep default extract safe/quiet via `is_current=False` (no new extract flag).
- Make `open`/`read` of a history row return that revision’s bytes.
- Align presented names with `unrar`/`WinRAR` (`path;n`) so tooling and mental
  models match.

**Non-Goals:**
- Extract-all opt-in to write every revision to disk (callers use `open`/`read`).
- Changing TAR duplicate `is_current` semantics in this change.
- Native decompression of versioned members (still `unrar` when not direct-readable).
- Surfacing version history through a separate API type or config gate.

## Investigations

### Format / `unrar` behavior (RAR 6.24 write + UNRAR 7.00)

Built a three-revision `-ver` RAR5 archive (`file.txt` ← v1, v2, v3):

| Probe | Result |
| --- | --- |
| `unrar lt` | Lists `file.txt;1`, `file.txt;2`, `file.txt` with `File version: N` on history |
| Header name in extra | Path stays `file.txt`; version vint in FHEXTRA_VERSION; `unrar` appends `;n` when `n!=0` |
| `unrar p -inul archive` (no member) | Prints **only** current (`v3`) |
| `unrar p -inul -ver archive` | Prints **all** revisions in archive order (`v1`, `v2`, `v3`) |
| `unrar p -inul archive 'file.txt;1'` | Prints `v1` (exact name; no `-ver` needed) |
| `unrar p -inul archive file.txt` | Prints current `v3` |

`cmddata.cpp`: `-ver` → `VersionControl=1`; `-verN` → `N+1`. `extract.cpp`: when
`VersionControl==0`, versioned heads are skipped unless the requested name equals
the versioned name (`EqualNames`).

### Archivey solid demux implication

Solid `_iter_with_data` uses bare `unrar p` (ALL pipe, no member). Without `-ver`,
history payloads are absent from the pipe — demux would desync if history rows
were treated as payload files. Named per-member `unrar p … path;n` works for
random `open`/`read`.

### Oracle

`rarfile` still skips versioned entries (`pass  # skip old versions`). List
equality vs rarfile MUST carve out `-ver` archives (or compare only current
paths).

## Decisions

### 1. Expose history rows; mark non-current
Include RAR3/RAR5 file-version FILE blocks in the member table with
`is_current=False`. Live revision (no version extra / version 0) keeps the plain
path and `is_current=True`.
**Rejected:** Keep omitting (hides recoverable bytes). **Rejected:** Same plain
path + `is_current=False` only (ambiguous for `get`/`unrar` naming).

### 2. Presented name is `path;n` (WinRAR / `unrar` shape)
Parser stores archive path + version; reader presents `f"{path};{n}"` when
`n != 0`, and sets `extra["rar.file_version"] = n`. `raw_name` reflects the
archive-stored path bytes (without forcing `;n` into raw header bytes).
**Rejected:** Keep plain path and put version only in `extra` (breaks
`unrar p` exact-name and user expectations from `unrar lt`).

### 3. Data path: exact member name; `-ver` only for solid ALL demux
- Random `open`/`read`: pass presented `path;n` into `unrar p … <member>` (no
  `-ver`). Direct M0 nonsolid reads remain allowed when `_can_direct_read`.
- Solid ALL-pipe demux: if the member list contains any versioned payload
  FILE, spawn `unrar p -ver` so the pipe includes history bytes in order;
  otherwise keep today’s bare `unrar p`.
**Rejected:** Always pass `-ver` (changes ALL-pipe size map for archives
without versions only cosmetically, but prefer minimal flag use).
**Rejected:** Drop history from solid streams and only allow named open
(inconsistent with “expose in iteration”).

### 4. Default extract stays skip-via-`is_current`
Rely on existing `safe-extraction` non-current skip. No new
`include_file_versions=` extract knob in this change.
**Rejected:** Config gate that hides history from `members()` (conflicts with
“expose features as data”; extract already hides writes).

### 5. Listing limits and parser ceilings count history rows
Versioned FILE blocks consume `ListingLimits` / RAR parser member ceilings like
any other member.
**Rejected:** Exclude history from caps (would under-count hostile `-ver`
bombs).

### 6. Oracle / fixtures
Add a small RAR5 `-ver` fixture via `scripts/gen_rar_fixtures.py`. Prefer RAR5;
add RAR4/`FILE_VERSION` only if generation stays cheap. Document rarfile list
mismatch on versioned archives in testing-contract.

## Risks / Trade-offs

| Risk | Mitigation |
| --- | --- |
| **BREAKING** list length / names vs prior omit + rarfile | Document in proposal; carve oracle tests; names match `unrar lt` |
| Solid demux forgets `-ver` | Gate ALL-pipe on “any versioned payload”; regression test |
| Path that literally ends with `;n` | Rare; WinRAR already warns. Exact-name still works; document collision |
| `get("file.txt")` ambiguity | Returns live member only (plain name); history is `file.txt;n` |

## Open Questions

None — `-ver` / exact-name behavior verified against UNRAR 7.00 for this design.
