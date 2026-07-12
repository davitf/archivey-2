## Context

`format-rar` and ADR 0002 already specify native RAR4/RAR5 metadata + RARLAB
`unrar` for data. No RAR backend is registered yet (`core.py` still stubs
multi-volume as “Phase 7”). v2 already has `SolidBlockReader` (explicitly for
7z folders **and** RAR `unrar p` pipes), volume *discovery* in `volumes.py`,
password candidates, and the 7z reader as the solid-streaming template.

Provenance: DEV `docs/rar-native-reader-design.md` + `rar_reader.py`
(`RarStreamReader`); live `unrar` 7.00 probes on DEV fixtures (2026-07);
`BaseArchiveReader` contract (`_iter_members` / `_iter_with_data` /
`_open_member` / `_ensure_link_target`).

## Goals / Non-Goals

**Goals:**
- Zero-dep core RAR **listing**; data via identified RARLAB `unrar` only.
- Solid `stream_members()` = one unnamed `unrar p` pipe + `SolidBlockReader`,
  O(1) decompress passes, payload-only demux.
- Link targets always resolved when possible; hardlink/`FILE_COPY` via ABC follow.
- Full multi-volume join for path and stream sources.
- Oracle parity with `rarfile`/`unrar`; Atheris harness for the parser.

**Non-Goals:**
- Extract-hack / single-member temp RAR (spec already MAY; defer).
- Multi-name `unrar` argv for filtered solid passes (see Decision 3).
- Native RAR decompressor; RAR writing; multi-tool fallback matrix (closes
  threat-model C1 as won’t-do).

## Investigations

### `unrar p` stdout vs header sizes

| Fixture | Header Σ non-dir sizes | `unrar p` stdout | Notes |
| --- | --- | --- | --- |
| `basic_solid__.rar` | 41 | 41 | files only |
| `symlinks_solid__.rar` (RAR5) | 47 | **13** | redir symlinks omitted |
| `symlinks_solid__rar4.rar` | 47 | **13** | packed symlink targets omitted |
| `hardlinks_solid__.rar` | 32 | **16** | hardlink redirs omitted |

RAR4 symlink targets are stored as member data (`compress_size > 0`, method M0)
and `rarfile.read()` returns them, but `unrar p` still emits **0** bytes.
`compress_size == 0` is **not** a reliable “absent from pipe” signal — classify
by type / `file_redir`, not packed size.

`-ol` / `-ola` do not put link targets on `p` stdout.

### Member path args

| Observation | Evidence |
| --- | --- |
| Output order = **archive order**, not arg order | `p large3 large1` emits `large1\|\|large3` |
| Solid LAST-only ≈ nearly ALL CPU; FIRST-only cheaper | timed `large_files_solid` |
| Nonsolid LAST ≈ FIRST | named open skips unrelated members |
| Link/hardlink by name → 0 bytes | all symlink/hardlink fixtures |
| Partial password + unnamed ALL → silent omission → demux desync | `encryption_several_passwords` |
| Named + wrong password → `rc=11`, `out=0` | clean per-member failure |
| `@listfile` works | `unrar p archive @list` |
| Linux `ARG_MAX` ≈ 2 MiB; Windows CreateProcess ≈ 32 KiB | real cross-platform ceiling |

### Reader contract (v2)

- RAR has an upfront header index → `_MEMBER_LIST_UPFRONT = True`,
  `_SUPPORTS_RANDOM_ACCESS = True`, `SUPPORTS_PASSWORD = True`,
  `SUPPORTS_STREAMING_NON_SEEKABLE = False` (seek required).
- Solid backends **MUST override** `_iter_with_data` (not call
  `_get_members_registered()` for the data pass). Match 7z: one decode stream,
  `SolidBlockReader`, non-files yield `None`.
- `stream_members(selector)` applies the selector **outside** `_iter_with_data`
  (close unselected streams). The override **cannot see** the filter set →
  cannot safely build a multi-name argv from the public API without a base-class
  change.
- `_open_with_link_follow` only calls `_open_member` on the resolved **FILE**
  after following HARDLINK/SYMLINK → `_open_member` never needs link names.
- `_get_members_registered` already calls `_ensure_link_target` for every link.

## Decisions

### 1. Module layout
- `internal/backends/rar_parser.py` — RAR4/RAR5 block walk → `RarMemberInfo` list
  (SFX skip ≤2 MiB, solid/encryption flags, `file_redir`, offsets for stored reads).
- `internal/backends/rar_reader.py` — `RarReader` + `RarReadBackend` registration.
- Small private `unrar` helper (locate + identify RARLAB binary, spawn `p`/`x`,
  map exit codes). Reader never imports `rarfile`.

### 2. Solid streaming = unnamed pipe + SolidBlockReader (payload-only)
Override `_iter_with_data` whenever the archive is solid (and use the same path
when a sequential full pass is the natural fit). Spawn:

```text
unrar p -inul -p-|-pPWD <archive>
```

**No member path args.** Demux with `SolidBlockReader` using cumulative sizes of
**payload FILE members only** (exclude directories, symlinks, hardlinks,
`FILE_COPY`). Non-payload members yield `(member, None)`.

Filtered `stream_members(selector)` relies on the base class closing unselected
streams; `SolidBlockReader` lazily skips unread tails on the next
`open_member`.

**Rejected:** demux by Σ of all header `file_size` values (desyncs on any archive
with links). **Rejected:** one `unrar` per member for solid iteration (O(N²)).

### 3. `unrar` argv policy — no multi-name in this change
| Call site | Args after archive |
| --- | --- |
| Solid `_iter_with_data` | *(none)* |
| `_open_member` (FILE after link follow) | exactly one member path |
| Stored M0 unencrypted | do not call `unrar` (direct byte range) |

**Why not “pass selected names, fall back to no-args if argv too long”?**
`_iter_with_data` does not receive the `stream_members` selector, so a correct
multi-name optimization needs a base-contract change or a side channel. Falling
back to unnamed ALL is what we already do. Truncating the name list would be
wrong. `@listfile` works and would beat argv limits later — still blocked on
selector visibility.

**Rejected:** multi-name argv with an ARG_MAX/count cap in v1 (complexity for
no contract-visible win). **Deferred:** `@listfile` / multi-name if a future
change threads the selector into the solid pass.

### 4. Random / nonsolid open
Nonsolid sequential iteration may use the **default** `_iter_with_data` (lazy
`_open_member` per selected file) so unselected members never spawn `unrar`.
Each `_open_member` runs `unrar p -inul … archive name` (one name). Solid random
`open()`: named `unrar p` (still O(prefix) solid cost) **or** one explicit
`unrar x` tempdir cleaned on close (declared strategy; no growing RAM cache).

### 5. Links
- RAR5: type from `file_redir`; target string from header; set `link_target` at
  list time. Symlink/junction → `SYMLINK`; hardlink + `FILE_COPY` → `HARDLINK`
  (file-copy follows like hardlink for open/extract).
- RAR4: Unix mode `0xA000` → `SYMLINK`; target via **stored direct read** when
  method is M0 / readable without `unrar`; otherwise `_ensure_link_target` may
  use a targeted `unrar`/`x` fallback.
- Always resolve when possible during registration / `_ensure_link_target`.

### 6. Passwords and mixed encryption
Solid archives share one password — unnamed ALL pipe is safe when the candidate
set decrypts all encrypted payload members (use RAR5 check values when present).
Mixed-password nonsolid: never demux an unnamed ALL pipe against the full member
list (silent skips desync); use per-member named opens (default `_iter_with_data`).
Wrong password on a named open → `EncryptionError` (`unrar` rc 11 / empty out).

### 7. Multi-volume
Use existing `discover_volume_siblings` (`.partN.rar` and `.rar`+`.rNN`). Path
source: parse headers across volumes; point `unrar` at **volume 1**. Stream /
in-memory sources: materialize ordered volumes to an explicit temp dir for
`unrar`, clean up on close. Incomplete/out-of-order → typed error, not partial
garbled members. Update the stale “Phase 7” multi-volume stub in `core.py`.

### 8. RARLAB-only; close C1
Identify RARLAB `unrar` (version banner / known non-RARLAB rejection). No
fallback matrix. Document threat-model C1 as won’t-do (licensing + behavioral
divergence already observed).

### 9. Extract-hack deferred
Spec’s benchmark-gated small-member optimization stays allowed but **unimplemented**
here. Ship without it.

### 10. Crypto / Blake2sp
RAR5 header encryption and per-file encryption records parsed natively; AES via
`[crypto]`/`[rar]`. Tweaked CRC handling for encrypted RAR5 data checksums (DEV
helpers). Blake2sp verify only with `[rar]`; else diagnostic/warning and return
bytes (packaging contract).

## Risks / Trade-offs

| Risk | Mitigation |
| --- | --- |
| Demux desync on links / wrong password | Payload-only sizes; refuse unnamed ALL for mixed-password nonsolid |
| `unrar-free` on PATH | Identify RARLAB; `PackageNotInstalledError` naming RARLAB `unrar` |
| Windows argv limits | Irrelevant for v1 (≤1 path arg); `@listfile` noted for later |
| Solid random open cost | Named `unrar` or declared `unrar x` tempdir; warn toward `stream_members` |
| RAR 1.5 / 2.x / `unp_ver≤20` | Supported: same RAR3-style header walk; data via `unrar` (do not gate on extract version — false-positives on RAR3 stored members) |
| Exotic / unreadable blocks | Typed `UnsupportedFeatureError` / `CorruptionError` when headers cannot be parsed |

## Open Questions

*(none — argv/multi-name, extract-hack, C1, FILE_COPY, link resolution, and
volume join settled above.)*
