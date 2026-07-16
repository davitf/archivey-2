# Open issues from gotchas triage

> **Not user-facing.** Holding area for items that *look* like user gotchas but are
> candidates to fix (product), sync (docs/specs), or deliberately leave irreducible.
> Companion to [threat-model.md](threat-model.md) (security/compat gap register) and
> root `IDEAS.md` (speculative backlog). User-facing Gotchas / “why Archivey” pages
> (when written) should only keep the **irreducible** bucket — everything else either
> ships as a fix or stays here until it does.
>
> Snapshot date: 2026-07-16. Assumes open review-fix PRs land:
> [#127](https://github.com/davitf/archivey-2/pull/127) (crypto F1–F5),
> [#128](https://github.com/davitf/archivey-2/pull/128) (stream-decoder F1–F6),
> [#124](https://github.com/davitf/archivey-2/pull/124) (PPMd bound decode).

## How to use this list

| Bucket | Meaning | Goes to user Gotchas? |
| --- | --- | --- |
| **Product** | Behavior we can change | Only until fixed; then drop or turn into a “we used to…” note in Why |
| **Docs / specs** | Drift or missing user/spec prose for shipped behavior | No — fix the guide/spec |
| **Irreducible** | Format/stdlib/upstream constraint we can only document + warn | Yes |
| **Longer-term** | Real work, but belongs in `IDEAS.md` / OpenSpec changes | Maybe a one-liner pointer |

When an item ships, strike it here (or move to a “Closed” section) and update the
user docs in the same change when relevant.

---

## Product — candidates to fix

### P1. Default `strict_archive_eof` to True (at least for random-access)

- **Status:** parked in OpenSpec change
  [`decide-strict-archive-eof-default`](../../openspec/changes/decide-strict-archive-eof-default/)
  (options A–E + provisional Option D). Do not flip the default ad hoc.
- **Today:** `ArchiveyConfig.strict_archive_eof=False` → mid-archive TAR corruption
  that stdlib treats as clean EOF surfaces only as `ARCHIVE_EOF_MARKER_MISSING`
  (WARNING). Inventory/dedupe sweeps can get a silently shortened listing.
- **Why not trivial:** Phase 5 defaulted False for trailer-less / `cat`-joined tars
  (GNU-tar-like); a raise at end-of-pass is awkward after successful extract. Same
  knob cannot yet distinguish missing trailer vs corrupt-shortened listing.
- **Refs:** change `design.md`; `review/deep-unknown-unknowns.md` W1; `config.py`;
  `format-tar`.

### P2. Multi-volume / split ZIP (`.z01`…`.zip`)

- **Today:** Detected and rejected with `UnsupportedFeatureError` (“rejoin first”).
- **Why fixable:** 7z/RAR already join volumes; ZIP needs disk-aware central-directory
  addressing over an ordered concatenation — natural part of a **native streaming ZIP**
  reader (`IDEAS.md`), not a stdlib `zipfile` wrap.
- **Until then:** user Gotcha / `formats.md` (already noted).
- **Refs:** `IDEAS.md` native ZIP; `format-zip`; `zip_reader.py`.

### P3. Native TAR header walker (replace stdlib silent-EOF leniency)

- **Today:** Archivey’s EOF-marker backstop is a warning/escalation on top of
  `tarfile`’s “corrupt non-first header = end of archive.”
- **Why fixable:** Same native-first strategy as 7z/RAR — make corrupt mid-archive
  headers archivey’s own `CorruptionError` / salvage decision.
- **Larger than P1;** P1 is the cheap honesty upgrade, this is the structural one.
- **Refs:** `known-issues.md`; `IDEAS.md` (implied by native-first); W1 longer-term.

### P4. ZIP UTF-8 general-purpose bit 11 “lie”

- **Today:** Stdlib `zipfile` strictly UTF-8-decodes flagged names → one bad name can
  make the **whole archive** unlistable (`CorruptionError`).
- **Why fixable:** Native ZIP parser can fall back like unflagged names + diagnostic
  (same story as multi-volume / streaming ZIP).
- **Refs:** `IDEAS.md` native ZIP; adversarial string corpus.

### P5. Rapidgzip / accelerator process abort when caller closes the source

- **Today:** Upstream rapidgzip 0.16 can `terminate()` if the Python source raises
  under a live accelerator stream. Archivey avoids closing *its* SharedSource under
  the stream; **caller-owned** sources remain exposed.
- **Why partially fixable:** Keep mitigating in-tree; full fix is upstream. Product
  work: document loudly; optionally refuse accelerator on non-path / non-owned
  sources; hang sandbox for untrusted input (threat-model O5 follow-up).
- **Refs:** `known-issues.md` Bug 3; `costs.md`; threat-model accelerator hang.

### P6. RAR solid demux ↔ `unrar` emission-policy coupling

- **Today:** Solid ALL-pipe demux must match what `unrar` actually emits (RAR5
  symlink targets in header → 0 stdout bytes; RAR3 symlink targets in LZ data →
  also 0 after decode). Easy to desync on new member kinds.
- **Why fixable:** Spec’d hardening / shared emission table; called out in the
  unrar-piping investigation as a future change (same class as mixed-password
  ALL-pipe forbid).
- **Refs:** PR #101 / `docs/internal/rar-unrar-piping-investigation.md` (when merged);
  `format-rar`.

### P7. Spec vs behavior: encrypted 7z with no integrity anchor

- **Today (after #127):** Best-effort accept (matches 7-Zip) + `DIGEST_UNVERIFIABLE`
  (`reason="no_integrity_anchor"`).
- **Still open:** `openspec/specs/format-7z` still says wrong password → “never
  silent bytes” / no incorrect data. Align the requirement with the maintainer
  decision (diagnostic, not hard-error) so Gotchas and the spec agree.
- **Refs:** `review/next/02-crypto` F2 / Q2; PR #127.

---

## Docs / specs — drift and missing prose

These are **already implemented** (or will be once the review-fix PRs merge). They
should not stay in user Gotchas as “broken” — update the guides instead.

| Item | Status in code | Doc / spec action |
| --- | --- | --- |
| Gzip multi-member: omit trailer CRC from `member.hashes` | Done (`gzip_has_additional_member`; `test_multi_member_gzip_omits_crc32`) | Confirm `formats.md` + stored-digest matrix stay accurate; don’t list as open bug |
| 7z CRC-less encrypted store → diagnostic | #127 | User Gotchas + `formats.md` 7z; fix `format-7z` wording (P7) |
| RAR5 HASHMAC / tweaked digests | #127 | `formats.md` RAR integrity notes |
| 7z `NumCyclesPower` ≤24 / `0x3F` | #127 | `formats.md` / packaging notes if useful |
| RAR password via stdin (`-p` + stdin) | #127 | Drop any “password in argv” gotcha |
| Cross-platform name safety (O2/O3/O4/O7 + RENAME) | #109 / #123 | **threat-model.md still says O2/O7 “awaiting”** — mark implemented; teach STRICT sanitization in user Gotchas / safe-extraction |
| RAR5 `-ver` history rows in `members()` | Specced + implemented | **Missing from `formats.md`** — high user surprise |
| Duplicate names / `get` last-wins / str vs `ArchiveMember` selectors | Specced | Almost absent from `usage.md` / Gotchas |
| Hardlink target = earlier same name by `member_id` | Specced | Same |
| Nested-archive stance + bounded recursion recipe | Behavior OK | Threat-model O6; add recipe to user guide |
| Symlink-unsupported FS ≠ `tarfile` copy-through | Specced | Worth a line in safe-extraction / Why |
| Accelerator opt-out for untrusted + latency budget | Documented internally | Promote from threat-model into user Gotchas / costs |

---

## Irreducible — document forever (user Gotchas)

These are constraints of formats, stdlib, or upstream. Hardenings and diagnostics
help; they do not disappear.

- **Solid archives:** out-of-order `open()` can re-decode; prefer `stream_members()`.
- **Seek without index:** backward seek may re-decompress (`STREAM_REWIND_REDECOMPRESSES`).
- **Streaming mode is one pass** (including after early `break`); `scan_members()` to drain.
- **ZIP / ISO need seek** — no pure-pipe path even with `streaming=True`; no silent buffer.
- **ZipCrypto multi-password + STORED** confirmation cost (~1/256 false open → CRC scan).
- **7z AES has no password check value** — without CRC/folder digest, wrong password can
  yield garbage (we warn; 7-Zip does the same).
- **RARLAB `unrar` only** for member data; listing works without it.
- **BCJ2 unsupported** — rejected, not garbage.
- **Native optional wheels / accelerators** may crash or hang on hostile input; we
  mitigate, cannot promise 100%.
- **ISO import patches pycdlib’s collections** (cycle guard) — visible if the process
  also uses pycdlib directly.
- **`.Z` truncation:** only nonzero leftover bits are loud.
- **Metadata fidelity** (xattrs/ACLs/forks) not claimed on extract.
- **Concurrent hostile modification** of the destination during extract — out of scope.

---

## Longer-term (point at `IDEAS.md` / OpenSpec; don’t park design here)

| Theme | Notes |
| --- | --- |
| Native streaming ZIP | Pipes, truncated/no-EOCD, multi-volume (P2), UTF-8 flag lie (P4) |
| Salvage / best-effort read mode | Founding use case; all-or-error today |
| Accelerator hang sandbox | Threat-model O5; fuzz with accelerators off until then |
| OSS-Fuzz + `SECURITY.md` | Before public “safe” marketing |
| Nested-archive helper / bounded recursion | O6 recipe → maybe a small helper later |
| Free-threading support matrix | Document core vs ISO vs accelerators |
| Public backend API / plugins | Home for exotic formats without libarchive-in-core |

---

## Suggested first cuts

1. **Docs sweep (cheap):** threat-model O2/O7 status; `formats.md` RAR `-ver`;
   duplicate-name / hardlink notes in usage or Gotchas; `format-7z` vs F2 diagnostic.
2. **`strict_archive_eof` (P1):** locked only via
   `openspec/changes/decide-strict-archive-eof-default/` — do not flip ad hoc.
3. **User Gotchas + Why pages:** write from the **Irreducible** bucket + post-v1
   “may improve later” items + the hardenings narrative; link Product items as
   “not yet” only when still open.
