## Context

#66 shipped native 7z with `is_anti: bool` / `is_current: bool` on `ArchiveMember` and
empty `open()` for anti/`FILE`. Main specs were then compacted to the `library` schema;
anti/current extraction detail mostly dropped from main specs while the code remains.
Directory `open`/`read` still differs by backend.

## Goals / Non-Goals

**Goals:**
- First-class `MemberType.ANTI` so `is_file` is false.
- Uniform non-file `open`/`read` → `ArchiveyUsageError`.
- Compact re-pin of anti extraction + `is_current` skip in `safe-extraction`.

**Non-Goals:**
- Changing delete-only-if-written anti policy.
- `is_current` for non-7z formats.
- Opt-in differential restore over pre-existing trees.

## Investigations

| Backend | `open`/`read` on DIRECTORY today |
| --- | --- |
| ZIP / TAR / 7z | Empty `b""` |
| Directory reader | Raw `IsADirectoryError` |
| ISO | `CorruptionError` (pycdlib rejects dir path) |
| `stream_members` (all) | Already yields `None` for `not is_file` |

| Anti today (#66) | Behavior |
| --- | --- |
| Type | `MemberType.FILE` + `is_anti=True` |
| `open`/`read` | `b""` |
| `stream_members` | Empty `ArchiveStream` (because `is_file`) |
| Extract | Special-cased on `is_anti` before file write |

## Decisions

### 1. `MemberType.ANTI`, not `OTHER` / not `FILE`
Callers filter on `is_file`; `OTHER` always raises `SpecialFileError` at extract.
**Rejected:** flag-only `is_anti` on `FILE`; naming `DELETE`/`TOMBSTONE` (keep 7z `ANTI`).

### 2. `is_anti` is a property
`return self.type == MemberType.ANTI` — same shape as `is_file`/`is_dir`/`is_other`.
Remove the dataclass field. **Rejected:** keeping both field and type (drift risk).

### 3. Central gate after link follow
In `BaseArchiveReader`, once the resolved member is not `FILE`, raise
`ArchiveyUsageError`. Symlinks that resolve to a file still succeed.
**Rejected:** `UnsupportedOperationError` / `CorruptionError` (misuse, not capability
or corrupt archive).

### 4. Extraction
`check_universal` rejects only `OTHER`. Anti branch keys off `is_anti` (⇔ `ANTI`).
Non-current members stay `SKIPPED` by default.

## Risks / Trade-offs

- [Empty-bytes callers break] → Changelog + cross-format tests; clear error text.
- [Spec/code drift after compaction] → This change re-pins anti/current in main deltas.

## Open Questions

(none)
