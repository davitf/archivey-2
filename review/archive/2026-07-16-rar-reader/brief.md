# Brief 1 — Native RAR reader: hostile-input & correctness deep review

Read `review/next/README.md` first (stale-findings list, conventions, deliverable
shape). This brief owns the **native RAR reader**, which did not exist at the old
review baseline.

## Why this review, now

RAR reading was rewritten from scratch to drop `rarfile`: a **native metadata
parser** (RAR3 *and* RAR5 headers, SFX detection, multi-volume, encrypted headers
decrypted natively) plus the external `unrar` binary as the *decompressor only*
(`docs/internal/`, `format-rar` spec, ADR/PLAN Phase 7). Landed in **#74**, hardened
for member-table bombs in **#83**, extended with WinRAR `-ver` file-version members
in **#107**. `rarfile` is now a test oracle only.

This is the single largest unreviewed attack surface in the tree, and it is the
exact shape that gave the old review its richest findings on the 7z parser:
hand-written parsing of attacker-controlled headers with allocations and bounds. The
old review never saw a line of it.

## Files (traced, current line counts)

- `internal/backends/rar_parser.py` (~1595) — the whole header parser: RAR3
  (`_parse_rar3`, `_parse_rar3_file_header`, `_rar3_decrypt_header`), RAR5
  (`_parse_rar5`, `_read_rar5_block`, `_rar5_decrypt_header`), vint/vstr/time
  loaders (`_load_vint`/`_load_vstr`/`_load_windowstime`/…), SFX scan
  (`_find_sfx_header`), split-member merge (`_merge_split_member`), `-ver` split
  (`_rar3_split_file_version`), and the RAR-specific crypto helpers (`_rar3_s2k`,
  `_rar5_s2k`, `_Rar3Sha1` incl. the deliberate `rarbug` path, `_HeaderDecryptStream`).
- `internal/backends/rar_reader.py` (~679) — the `ArchiveReader` backend:
  member materialization, password confirm, the pass/stream driver, error
  translation, `-ver` member exposure.
- `internal/backends/rar_unrar.py` (~111) — the `unrar` subprocess data pipe.
- Specs/design: `openspec/specs/format-rar/spec.md`; in `archivey-dev`,
  `openspec/changes/rar-native-metadata-reader/` + `docs/*-native-reader-design.md`
  (the design this follows). Fuzz targets: the Atheris RAR harness (#81).

## Boundary with Brief 2 (crypto)

RAR ships its own KDF/decrypt code (`_rar3_s2k`, `_rar5_s2k`, `_Rar3Sha1`,
`_HeaderDecryptStream`, `_rar{3,5}_decrypt_header`). **Split of duty:** Brief 2 owns
the *cryptographic correctness* of those primitives (KDF iteration/salt handling,
AES mode/IV, the `_Rar3Sha1` "rarbug" corruption, verification-vs-decrypt ordering,
constant-time concerns). This brief owns their *structural / hostile-input* safety:
what happens when an encrypted header lies about its sizes, when the salt/IV fields
are truncated, when a decrypted header is itself malformed, when a password is
absent/wrong. Coordinate so neither double-reports nor drops the seam.

## What to hunt (ranked by VISION stakes)

### A. Memory-safe hostile-input parsing (VISION claim #2 — top priority)
The 7z analogue here was an unbounded `num_files` pre-allocation (old finding #1).
For every attacker-controlled count/length/offset in the RAR parser, ask "what
bounds it?":
- vint decoding (`_load_vint`) — is the byte count bounded, or can a crafted vint
  spin/overflow? Any `OverflowError` (one was already fixed in #81 — are there more)?
- Per-record name/field lengths (`_load_vstr`, `_decode_name`, `_UnicodeFilename`) —
  bounded before allocating/decoding? RAR3 Unicode filename decode (`_UnicodeFilename`,
  `_fix_rar3_astral_truncation`) is fiddly bit-manipulation over attacker bytes.
- Member-table size — #83 added bomb hardening; confirm it actually bounds member
  **count**, aggregate name bytes, and header size, and that `ListingLimits` /
  `ResourceLimitError` fire on all three for RAR (parity with 7z/ZIP).
- `add_size`/`data_offset`/`_seek_after_packed` arithmetic — can a bad packed size
  seek backward, past EOF, or into a loop across volumes?
- SFX scan (`_find_sfx_header`) — bounded scan window, or unbounded read of a
  non-RAR file?
- Split/merge across volumes (`_merge_split_member`, `parse_rar_volumes`) — can a
  crafted continuation flag cause unbounded volume chaining or mismatched merges?

### B. `unrar` subprocess boundary (`rar_unrar.py`)
The one place native-code (well, external-binary) parsing of member *data* is
delegated. Check: argument construction can't be influenced into flags/path
traversal by a hostile member name; stdout is size-bounded / streamed not buffered
whole; stderr/exit-code maps to the right `ArchiveyError` (wrong password vs
corruption vs missing binary vs truncation); the process is always reaped
(no zombie/hang on a member that never emits EOF); password is passed without
leaking on a process listing where avoidable. Does the data pipe honor the
streaming / single-live-stream contract, and does truncated `unrar` output surface
as a recoverable-per-member error (VISION #3) rather than a wedged reader?

### C. Error contract & translation
The old `deep-simplification.md` S1 finding was tree-wide hand-rolled
translate/stamp/raise. Does `rar_reader` add *another* copy, or use the shared
boundary? Are `unrar` OSErrors, subprocess errors, and native-decrypt failures all
translated (no raw `subprocess.CalledProcessError` / `struct.error` / `UnicodeDecodeError`
escaping)? Is `EncryptionError` distinguished from `CorruptionError` on a
wrong-password header?

### D. Metadata fidelity & the `-ver` feature (#107)
`-ver` exposes WinRAR file-version history as members (`is_file_version_history`,
`_rar3_split_file_version`). Check: version members can't collide with or shadow the
current file during extraction; they're flagged so a naive `extract_all` doesn't
silently write N versions to the same path; the split parse can't misattribute a
normal filename containing `;` to a version. Also timestamp math
(`_parse_dos_time`, `_load_windowstime`, `_parse_rar3_ext_time`) vs the shared
`internal/timestamps.py` — any RAR-local FILETIME/DOS-time duplication or off-by-one.

### E. Concurrency & lifecycle
RAR's pass driver and the `unrar` pipe are new shared-state surfaces. Does the
backend honor the reader-concurrency contract (single-live-stream default,
draining close, materialization election) the way `ReaderState` expects? Is the
subprocess handle released on reader teardown / `BaseException` mid-stream (the old
finding #2 shape)?

## Non-goals / already settled

- Do not propose switching back to `rarfile`, or adding a second RAR path
  (`IDEAS.md` synthetic-single-stream-RAR was explicitly cut in `review/roadmap.md`).
- Do not re-report the listing-bomb *concept* as missing — it exists (#82/#83);
  the job is to verify RAR's coverage is complete and correct.
- `rarfile` as a test *oracle* is intended; don't flag its presence.

## Deliverable
Per README: `SUMMARY.md` (headline + top-findings table), theme files as needed
(suggest `hostile-input.md`, `unrar-boundary.md`, `contract.md`), `QUESTIONS.md`
for maintainer decisions, and a "what's actually fine" section. Every finding
traced to `file:line` with the concrete crafted-input/state that triggers it; note
which dependency config it reproduces in. Prefer an adversarial fixture (or an
Atheris seed) over prose for any hostile-input claim.
