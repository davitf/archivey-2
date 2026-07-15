## Context

`ExtractionPolicy` (STRICT default / STANDARD / TRUSTED) already gates metadata transforms
(permission normalization, uid/gid). `check_universal` already rejects traversal, non-UTF-8
that can't be `os.fsencode`d, and root-named non-directory members; the extraction
coordinator already *translates* the O7 write-time `EILSEQ` `OSError` into a typed
`ExtractionError`. `OverwritePolicy` (ERROR/REPLACE/SKIP) handles existing destinations but
keys on the exact `Path`, so it misses casefold/normalization collisions (O2). The
in-flight `adversarial-string-corpus-contract` owns bidi-control warnings + NUL link-target
rejection — orthogonal to the filesystem-representability dimension here.

## Goals / Non-Goals

**Goals:**
- Deterministic, cross-platform name handling: the same archive extracts to the same
  logical result (collision events, rejections) regardless of the runner OS.
- Anchor every rule to an existing `ExtractionPolicy` level; STRICT is portable-by-default.
- Settle the O7 normalization scheme (now decided: sanitize — see Decisions).

**Non-Goals:**
- Duplicating the bidi/NUL work in `adversarial-string-corpus-contract`.
- Changing traversal/symlink-escape safety (already non-bypassable).
- A **public un-escape / round-trip API** for the O7 sanitized spelling. Reversibility is a
  guaranteed *property* of the scheme (below); a public `unescape` helper can be added later
  without breaking anything, and is out of scope for v1.

## Decisions (settled)

The rationale for all six is recorded in
[ADR 0013](../../../docs/decisions/0013-cross-platform-name-safety-policies.md); the
condensed statements follow.

- **O2 collision key — per policy level.** The coordinator maintains a map from
  `casefold(NFC(relative_path))` → written path. A second member hitting the same key is a
  **collision event handled by `OverwritePolicy`** exactly as a real existing file would be,
  and recorded (see collision recording below). This applies under **`STRICT` and
  `STANDARD`** on **all** platforms — removing the platform-dependent silent merge under
  `REPLACE`. Under **`TRUSTED`** the coordinator keys on the exact `Path` and defers to the
  local OS (today's behavior), parallel to the O3/O4 `TRUSTED` stance. (On case-sensitive
  filesystems `README`/`readme` are two legitimate files; forcing a collision there is
  lossy, so it is a deliberate `STRICT`/`STANDARD`-only portability constraint, not a
  universal one.)
- **O3/O4 rejection under STRICT.** Reserved device names (`CON`, `PRN`, `AUX`, `NUL`,
  `COM1`–`COM9`, `LPT1`–`LPT9`, case-insensitive, with or without extension), trailing dot/
  space in any path segment, and `:` anywhere in a segment are rejected under `STRICT` on
  **every** platform. `TRUSTED` defers to the local OS. `STANDARD` = reject the
  unambiguously-dangerous set (reserved names, `:`), **allow** trailing dot/space (rare,
  Windows-only mangle, low-risk) — a middle ground consistent with STANDARD's "portable but
  not paranoid" role, and the attack variant (crafted `foo.`/`foo` merge) is still caught by
  `STRICT`.
- **O7 — sanitize (not reject).** Under `STRICT` **and `STANDARD`**, a name carrying bytes
  that are not representable portably is **sanitized to a deterministic, reversible portable
  spelling** rather than rejected; `TRUSTED` attempts the faithful bytes and lets the OS
  decide (today's behavior). Sanitize keeps the founding backup-indexing use case extracting
  everywhere and avoids coupling "extract this oddly-named member" to `TRUSTED` (which is a
  permission/ownership decision). The scheme:
  - **Which bytes:** only the **non-UTF-8 bytes** — the surrogateescape chars U+DC80–U+DCFF
    that `os.fsencode` maps back to raw bytes 0x80–0xFF. Each is emitted as `%XX` (uppercase
    hex of the byte); a literal `%` is escaped as `%25` so the transform is unambiguously
    reversible. `caf\udce9.txt` → `caf%E9.txt`.
  - **Scope:** O7 touches **only** non-decodable bytes. Valid-but-non-portable Unicode
    (NFC/NFD `café`) is representable everywhere and is **not** rewritten — its only
    cross-platform issue is normalization folding, which is O2's collision concern.
  - **Determinism:** applied on **every** OS (including Windows, where surrogatepass would
    otherwise write the raw surrogate) so the logical result is platform-independent.
  - **Collision-tracked:** the sanitized spelling feeds the O2 key like any other name;
    rejection only remains for names that cannot be `os.fsencode`d at all (already handled
    by `check_universal`).
  - **Reversibility:** a *property*, pinned in the spec so the scheme is stable at first
    release; **docs-only for v1** (no public un-escape API — see Non-Goals).

- **`OverwritePolicy.RENAME` lands in this change.** It reuses the O2 collision map, and the
  CLI's `extract` wants `unzip`-parity rename-on-collision; this change lands before the CLI
  phase. (An earlier draft listed a full implementation as a Non-Goal — superseded; the
  proposal, tasks, spec delta, and brief all scope it here.)
- **RENAME spelling.** A colliding entry is written with ` (N)` inserted **before the final
  suffix** (`Path.stem` + `Path.suffix` semantics): `photo.jpg` → `photo (1).jpg`,
  `photo (2).jpg`, … This preserves the extension so the file still type-detects and opens
  (`photo.jpg (1)` would break `.jpg`), matching `unzip` / Explorer / browser conventions.
  Edge cases: no suffix → `photo (1)`; leading-dot dotfile `.bashrc` → `.bashrc (1)` (leading
  dot not split); multi-suffix `archive.tar.gz` → `archive.tar (1).gz` (single final suffix);
  directories append to the whole segment. `N` increments to the first name free **both on
  disk and in the collision map**, in member-processing order.
- **Collision recording.** `ExtractionResult` gains **`requested_path: Path | None`** — the
  destination the coordinator intended before overwrite/rename resolution. A rename is
  exactly `requested_path != path and status == EXTRACTED`; on `SKIP`/`ERROR` collisions
  `requested_path` is set with `path=None`. Inference from `member.name` is rejected (it is
  conflated by the policy transform, O7 sanitize, and user-filter renames), as is a bare
  `collided: bool` (loses the location and the prior path). The security-relevant **audit
  trail** — a casefold/NFC key clash with an earlier written member, including under
  `REPLACE` where the result is a plain `EXTRACTED` — is a **diagnostic**
  (`EXTRACTION_NAME_COLLISION`) emitted into the report/reader aggregate, consistent with
  "`ExtractionResult` has no diagnostics field." Without this, the `REPLACE` merge O2 exists
  to expose would still be silent.

## Risks

- `STRICT`/`STANDARD` now *rewrite* (O7 sanitize) or *reject* (O3/O4) names Linux used to
  write as-is, and treat casefold/NFC clashes as collisions where Linux would keep both
  files → mitigated by policy levels (`TRUSTED` = today's faithful-bytes / local-OS
  behavior) and clear docs. This is the deliberate "STRICT is portable-by-default"
  redefinition, not an accident.
- The O7 escape scheme becomes a compatibility surface (once chosen, it's hard to change) →
  mitigated by choosing the single most standard scheme (percent-encoding of raw bytes) and
  pinning it in the spec now, before the first public release freezes behavior; reversibility
  stays a documented property so a public un-escape API can follow non-breakingly.
