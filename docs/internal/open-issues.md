# Open issues from gotchas triage

> **Not user-facing.** Holding area for items that *look* like user gotchas but are
> candidates to fix (product), sync (docs/specs), or deliberately leave irreducible.
> Companion to [threat-model.md](threat-model.md) (security/compat gap register) and
> root `IDEAS.md` (speculative backlog). User-facing [Gotchas](../gotchas.md) should
> keep the **irreducible** bucket (plus post-v1 “may improve later” notes) —
> everything else either ships as a fix or stays here until it does.
>
> Snapshot: 2026-07-18 against `main` @ `93dc28e`. Merged since the first triage:
> [#127](https://github.com/davitf/archivey-2/pull/127) (crypto F1–F5),
> [#128](https://github.com/davitf/archivey-2/pull/128) (stream-decoder F1–F6),
> [#124](https://github.com/davitf/archivey-2/pull/124) /
> [#130](https://github.com/davitf/archivey-2/pull/130) (PPMd bound decode),
> [#120](https://github.com/davitf/archivey-2/pull/120) (CLI). This triage PR is #129.

## How to use this list

| Bucket | Meaning | Goes to user Gotchas? |
| --- | --- | --- |
| **Product** | Behavior we can change | Only until fixed; then drop or turn into a “we used to…” note in Why |
| **Docs / specs** | Drift or missing user/spec prose for shipped behavior | No — fix the guide/spec |
| **Irreducible** | Format/stdlib/upstream constraint we can only document + warn | Yes |
| **Longer-term** | Real work, but belongs in `IDEAS.md` / OpenSpec changes | Maybe a one-liner pointer |
| **Closed** | Shipped; kept briefly for provenance | No |

When an item ships, move it to **Closed** (or delete) and update user docs in the
same change when relevant.

---

## Product — candidates to fix

### P1. TAR end-of-archive strictness — DECIDED + IMPLEMENTED (Option F)

- **Status:** decided and implemented in OpenSpec change
  [`decide-strict-archive-eof-default`](../../openspec/changes/decide-strict-archive-eof-default/)
  — **Option F**. `config.py` default stays `False`.
- **Decision:** split the EOF diagnostic on `ArchiveEofContext.observed_kind` (the signal the
  check already computes) instead of on one bool. `observed_kind="nonzero"` (a stray non-null
  block where a trailer/header was expected — which a conformant tar never produces) raises
  `CorruptionError` **by default**, catching the *detectable* slice of stdlib's "corrupt
  non-first header = clean EOF." The ambiguous `absent`/`short` residual (complete-trailer-less
  vs. truncated-at-boundary) warns by default and escalates to `TruncatedError` only under
  `strict_archive_eof=True`. Terminal escalation flows through the `partial-members-and-errors`
  report model (#157): `members()` / `scan_members()` complete-or-raise; `members_report()`
  returns the recovered prefix + `error`; `__iter__` yields the prefix then raises. RA
  `extract_all` **fails closed** (extract-prep materializes before any write); streaming writes
  salvageable members then raises.
- **Why Option F:** honors Phase 5 warn-by-default for trailer-less / `cat`-joined tars (those
  are `absent`) while still hard-failing the corruption we *can* detect, without a native TAR
  walker and without breaking the common corpus. The `absent`/`short` residual is the piece
  that is genuinely undecidable until P3.
- **Refs:** change `design.md` (option survey + `observed_kind` analysis);
  `review/deep-unknown-unknowns.md` W1; `config.py`; `format-tar`.

### P2. Multi-volume / split ZIP (`.z01`…`.zip`)

- **Today:** Detected and rejected with `UnsupportedFeatureError` (“rejoin first”).
- **Why fixable:** 7z/RAR already join volumes; ZIP needs disk-aware central-directory
  addressing over an ordered concatenation — natural part of a **native streaming ZIP**
  reader (`IDEAS.md`), not a stdlib `zipfile` wrap.
- **Until then:** user Gotchas / `formats.md` (already noted).
- **Refs:** `IDEAS.md` native ZIP; `format-zip`; `zip_reader.py`.

### P3. Native TAR header walker (replace stdlib silent-EOF leniency)

- **Today:** Option F's EOF backstop raises `CorruptionError` on a rejected (non-null)
  header in random access (including final-block via `_EofProbeStream`) and on mid-archive
  rejected headers in streaming; streaming still cannot see a rejected *final* header
  (tarfile's `_Stream` hides it). The `absent`/`short` residual stays warn-by-default.
- **Why fixable:** Same native-first strategy as 7z/RAR — validate each header at its
  offset, close the streaming final-header gap, and improve salvage/precision on detectable
  cases. The `absent`/`short` residual remains intrinsically ambiguous even with a native
  walker (byte-identical trailer-less-complete vs. truncated-at-boundary).
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
  work: document loudly (Gotchas); optionally refuse accelerator on non-path /
  non-owned sources; hang sandbox for untrusted input (threat-model O5 follow-up).
- **Refs:** `known-issues.md` Bug 3; `costs.md`; Gotchas; threat-model accelerator hang.

### P6. RAR solid demux ↔ `unrar` emission-policy coupling

- **Today:** Solid ALL-pipe demux must match what `unrar` actually emits (RAR5
  symlink targets in header → 0 stdout bytes; RAR3 symlink targets in LZ data →
  also 0 after decode). Easy to desync on new member kinds.
- **Why fixable:** Spec’d hardening / shared emission table; called out in the
  unrar-piping investigation as a future change (same class as mixed-password
  ALL-pipe forbid).
- **Refs:** PR #101 (still open) / `docs/internal/rar-unrar-piping-investigation.md`
  (when merged); `format-rar`.

---

## Docs / specs — drift and missing prose

Code is done unless noted. These should not appear in Gotchas as “broken.”

| Item | Code | Doc / spec action |
| --- | --- | --- |
| Gzip multi-member: omit trailer CRC from `member.hashes` | Done | **Closed** — `formats.md` + Gotchas accurate |
| 7z CRC-less encrypted store → diagnostic | #127 | **Closed** — Gotchas + `formats.md` + `format-7z` (P7) |
| RAR5 HASHMAC / tweaked digests | #127 | **Closed** — noted in `formats.md` RAR section |
| 7z `NumCyclesPower` ≤24 / `0x3F` | #127 | **Closed** — `formats.md` + `format-7z` |
| RAR password via stdin (`-p` + stdin) | #127 | **Closed** — `formats.md` |
| Cross-platform name safety (O2/O3/O4/O7 + RENAME) | #109 / #123 | **Closed** — Gotchas + threat-model marked implemented |
| RAR5 `-ver` history rows in `members()` | Specced + implemented | **Closed** — Gotchas + `formats.md` |
| Duplicate names / `get` last-wins / str vs `ArchiveMember` selectors | Specced | Gotchas done; optional `usage.md` pointer remains nice-to-have |
| Hardlink target = earlier same name by `member_id` | Specced | Gotchas done; optional `usage.md` pointer remains nice-to-have |
| Nested-archive stance + bounded recursion recipe | Behavior OK | Gotchas one-liner done; fuller recipe still nice for usage/O6 |
| Symlink-unsupported FS ≠ `tarfile` copy-through | Specced | Gotchas done; optional line in `safe-extraction.md` |
| Accelerator opt-out for untrusted + latency budget | Mitigations in tree | Gotchas + costs cover it; P5 residual remains |
| Truncated gzip: stdlib engine recovers prefix on large `read(n)` (`gzip-zlib-truncation-recovery`) | Done | **Compose:** rapidgzip empty→stdlib fallback SHOULD use this `DecompressorStream` gzip-window engine (no byte-at-a-time workaround). Until that lands, truncated gzip behavior can still differ between accelerator ON and OFF. |

---

## Irreducible — document forever (user Gotchas)

These are constraints of formats, stdlib, or upstream. Hardenings and diagnostics
help; they do not disappear. Covered in [Gotchas](../gotchas.md).

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
  mitigate, cannot promise 100%. Includes residual `pyppmd` native-abort risk despite
  #124/#130 bounds (see `known-issues.md`), plus the separate open
  **exit-after-green** abort of `tests/test_ppmd_raw_streams.py` (soft-passed in
  required CI; soaked in non-required PPMd stress).
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
| `pyppmd` exit-after-green abort | `test_ppmd_raw_streams` green then teardown SIGSEGV/SIGABRT; see `known-issues.md` + `IDEAS.md` |
| Accelerator hang sandbox | Threat-model O5; fuzz with accelerators off until then |
| OSS-Fuzz + `SECURITY.md` | Before public “safe” marketing |
| Nested-archive helper / bounded recursion | O6 recipe → maybe a small helper later |
| Free-threading support matrix | Document core vs ISO vs accelerators |
| Public backend API / plugins | Home for exotic formats without libarchive-in-core |
| CLI UX polish | CLI shipped (#120); remaining design Qs under `review/archive/2026-07-17-cli/` |

---

## Closed (recent)

| Item | Closed by |
| --- | --- |
| Crypto F1–F5 (HASHMAC, 7z no-anchor diagnostic, NumCycles clamp, unrar stdin password, `compare_digest`) | #127 |
| Stream-decoder F1–F6 (seek-point collision, rapidgzip size/verify, feed budgets, `readall` pending_error, …) | #128 |
| PPMd `max_length` / after-eof / version pin product work | #124 / #130 (residual abort → Irreducible) |
| Gzip multi-member CRC omission from `hashes` | Earlier + tests; docs accurate |
| Cross-platform name safety implementation + threat-model prose sync | #109 / #123 + docs sweep |
| `format-7z` “never silent bytes” vs F2 diagnostic (P7) | docs sweep |
| `formats.md` RAR `-ver` / crypto notes | docs sweep |

---

## Suggested first cuts

1. **TAR EOF strictness (P1):** decided (Option F) in
   `openspec/changes/decide-strict-archive-eof-default/` — apply it; `config.py` default
   stays `False`, do not flip ad hoc.
2. **Why Archivey page** (next narrative doc): hardenings / why not wrap / why “large.”
3. Optional polish: `usage.md` duplicate-name / hardlink pointers; fuller nested-archive
   recipe; one line in `safe-extraction.md` on symlink-hostile FS.
