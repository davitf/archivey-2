## Context

Today `MemberType` is `{FILE, DIRECTORY, SYMLINK, HARDLINK, OTHER}`. `OTHER` means
device/FIFO/socket and is always rejected by `check_universal` (`SpecialFileError`).
7z anti-items (deletion markers) are planned in `native-7z-reader` as `FILE` +
`is_anti: bool` with empty `open()`/`read()` — which trains callers to treat them as
files. Separately, `open()`/`read()` on directories already diverges by backend
(empty bytes vs `IsADirectoryError` vs `CorruptionError`); specs never blessed the
empty-bytes path.

## Goals / Non-Goals

**Goals:**

- Give deletion markers a first-class `MemberType.ANTI` so `is_file` is false and
  sequential/`if m.is_file` callers skip them naturally.
- Make `open()`/`read()` fail closed for every non-file member, uniformly, with an
  `ArchiveyUsageError`.
- Keep anti extraction semantics (`safe-extraction`) keyed on anti identity, without
  routing through the `OTHER` special-file rejection.
- Align ZIP/TAR/ISO/directory/7z backends to the same non-file open rule.

**Non-Goals:**

- Changing anti *extraction* policy (delete-only-if-written / no-op) — that stays with
  `native-7z-reader` / `safe-extraction`.
- Computing `is_current` for non-7z formats.
- Opt-in `7z x`-style differential restore over pre-existing trees.
- Raising on `stream_members` itself (it already yields `None` for non-files).

## Decisions

### 1. New `MemberType.ANTI` (not `OTHER`, not `FILE`)

- **Choice:** `ANTI = "anti"` on the enum.
- **Why not FILE:** callers filter on `is_file` and would process tombstones as payload.
- **Why not OTHER:** `OTHER` ⇒ always `SpecialFileError` at extract; anti-items are
  extractable markers.
- **Alternatives considered:** `DELETE` / `TOMBSTONE` — clearer English, but `ANTI`
  matches the 7z wire name already used in specs/`is_anti` discussion; keep `ANTI`.

### 2. `is_anti` is a property, not a field

```python
@property
def is_anti(self) -> bool:
    return self.type == MemberType.ANTI
```

Same shape as `is_file` / `is_dir` / `is_other`. Equality is via `type`. Amends the
in-flight `native-7z-reader` plan that added `is_anti: bool = False` as a dataclass
field — migrate that change to set `type=ANTI` instead.

`is_current` remains a separate **field** (derived last-entry-wins); it is orthogonal
(an anti-item that is the final word on a path is `is_current=True`).

### 3. Central gate in `BaseArchiveReader.open` / `read`

After identity/link-follow resolution, if the resolved member is not `MemberType.FILE`,
raise `ArchiveyUsageError` (caller asked for payload that does not exist). Symlinks/
hardlinks that resolve to a file still succeed via existing follow logic; a link that
does not resolve still raises `LinkTargetNotFoundError` as today.

Backends MUST stop synthesizing `BytesIO(b"")` for directories/anti in `_open_member`.
`stream_members` keeps yielding `None` for `not member.is_file` (ANTI included once
`is_file` is false).

**Why `ArchiveyUsageError` (not `UnsupportedOperationError` / `CorruptionError`):**
opening a directory is API misuse, parallel to concurrent-stream misuse — not a
corrupt archive and not a missing backend capability. ISO’s current
`CorruptionError` and the directory reader’s raw `IsADirectoryError` are bugs to fix.

### 4. Extraction interaction

- `check_universal`: reject `OTHER` only; do **not** reject `ANTI`.
- Extractor anti branch: treat `member.is_anti` (⇔ `type == ANTI`) as today planned —
  no content write; delete-only-if-this-extraction-wrote.
- Ordering: anti handling before “write file bytes”; still after non-current skip.

### 5. Ordering vs `native-7z-reader`

This change can land on main before or after #66:

- **If before:** data-model + open gate land; 7z PR must classify anti as `ANTI` and
  drop empty-payload open when it merges.
- **If after:** follow-up PR migrates 7z anti members from `FILE`+field to `ANTI`+property
  and deletes the “empty payload” scenario.

Prefer updating `native-7z-reader` delta specs in the same effort that implements this
change so the two do not fight.

## Risks / Trade-offs

- [Break empty-bytes directory open] → Callers that `ar.read(dir_member)` expecting
  `b""` break; mitigation: clear error message; changelog; tests pin the raise.
- [Overlap with #66] → Spec conflict on anti open semantics; mitigation: amend
  `native-7z-reader` scenarios when implementing; note in tasks.
- [Link members] → Must only raise after follow fails or target is non-file; mitigation:
  gate runs on the *resolved* member inside `_open_with_link_follow`’s final hop.

## Migration Plan

1. Land specs + implementation of `MemberType.ANTI`, `is_anti` property, central open gate.
2. Update all backends / tests that assumed empty directory streams.
3. Amend `native-7z-reader` (or post-merge 7z code) to emit `type=ANTI` and rely on the gate.
4. No rollback flag — behavior is intentional fail-closed.

## Open Questions

- (none blocking) Display name for CLI/`7z l` parity can stay “anti” in docs.
