# Brief 01 — Native RAR reader: deep review summary

Branch `claude/rar-reader-review-6nkyy7`, HEAD `e74d227` (tree at #112). Reviewed
the three files the brief owns — `internal/backends/rar_parser.py` (~1595),
`rar_reader.py` (~679), `rar_unrar.py` (~111) — plus the streamtools primitives
they lean on (`solid.py`, `slice.py`, `shared.py`), the shared error boundary in
`base_reader.py`, `password.py`, and the `format-rar` spec.

**Baseline (green):** with RARLAB `unrar` 7.00 installed (per `AGENTS.md`),
`pytest tests/test_rar_reader.py tests/test_rar_oracle.py tests/test_rarfile_corpus.py`
= 44 passed / 4 skipped (before installing `unrar` the data-path tests skipped:
12 passed / 34 skipped). `ARCHIVEY_FUZZ=1` RAR fuzz harness passed;
`test_mutation_fuzz` 208 passed / 44 skipped. `ruff`, `ty`, `pyrefly` all clean.
`rarfile==4.3` present as oracle. The `unrar`-boundary findings (F3/F4) are
**empirically confirmed** — F4 and the `unrar` argv semantics at the CLI level
(`repro.py` F3b), and F3 **end-to-end through archivey** against committed adversarial
fixtures (`tests/fixtures/rar/hostile_argv__.rar` + `__rar4`, built by
`make_hostile_fixtures.py` and pushed by the maintainer): opening the `-inul` /
`@atfile` members raises `CorruptionError` instead of returning their bytes, and the
`@atfile` name is shown driving `unrar` to read an attacker-named local file.

**Headline.** The parser is noticeably more defensive than the 7z parser was at the
last review: vint decoding is length-capped, every count/length is bounds-checked
before slicing, `_seek_after_packed` guards offset arithmetic, the member-count
ceiling fires, mode/attr `OverflowError` is pre-masked, and out-of-range timestamps
are swallowed rather than aborting a listing. The 7z-style unbounded-preallocation
bug does **not** recur. But three real issues survive, all behaviourally
reproduced (F1/F2 in-process, F3 against RARLAB `unrar` 7.00):

1. **Wrong-password header decryption is reported as `CorruptionError`, not
   `EncryptionError`, whenever there is no usable password check value** — always
   for RAR3, and for any RAR5 whose `ENCRYPTION` block omits the check value. This
   escapes the password-candidate retry loop (`password.py` only catches
   `EncryptionError`), so supplying `["wrong", "correct"]` to a RAR3 header-encrypted
   archive aborts with `CorruptionError` and **never tries the correct password**.
2. **The RAR5 header-size vint pre-read is O(n²) and uncapped** — a small all-`0x80`
   input burns quadratic CPU (a few MB → tens of seconds), the one hostile-parsing
   DoS the length-capped `_load_vint` was supposed to prevent but this loop sits in
   front of.
3. **Hostile member names reach the `unrar` argv unescaped** — a member literally
   named `-inul`, `@listfile`, etc. is passed positionally with no `--` end-of-switches
   guard, so `unrar` parses it as a switch or a list-file. This is exactly the surface
   the spec's "Constrain unrar argv by call site" requirement is written to protect,
   and it is unguarded on the *hostile-name* axis. Compounded by an incomplete
   exit-code map (only `unrar` code 11 is translated), a mis-parsed member can yield a
   silent short/empty stream instead of an honest error.

## Implementation status (2026-07-16)

F1–F4 and the Q3 length check are **implemented on this branch with tests**, green in
all three dependency configs (`[all]` 1580 passed, `[all-lowest]` 1579, `[core-only]`
1328), `ruff` / `ty` / `pyrefly` clean:

- **F1** — `rar_parser.py` now maps a wrong header password to `EncryptionError` wherever
  no verifier exists (RAR3 always; RAR5 without a check value), keeping `CorruptionError`
  for a verified-key failure. Candidate iteration works again.
- **F2** — the RAR5 header-size vint pre-read is length-capped (no more O(n²)).
- **F3** — a named `unrar` open passes the member as a `-n./<name>` include mask (never
  positional), with `-ver` added for history rows; a name containing `*`/`?` is refused
  with `UnsupportedFeatureError` (no `unrar` escape exists). Hostile `-inul` / `@atfile`
  members now read their own bytes.
- **F4 + Q3** — `unrar` exit codes 2/3/10 map to typed errors (as a fallback when archivey
  has no hash to verify — a hashed member's CRC is authoritative, avoiding legacy-format
  false positives); length verification is folded into `VerifyingStream` (`expected_size`)
  and applied to **every** RAR member, bounding reads to the declared size (over-long →
  `CorruptionError` at the boundary, short → `TruncatedError`), after the digest check.
  (Consolidated with PR #122's conclusion; the separate `LengthVerifyingStream` was
  removed. See `QUESTIONS.md` Q3.)
- **F5** — a RAR3 `FILE_LARGE` member now skips its full 64-bit packed size
  (HIGH_PACK_SIZE), so the walk no longer under-seeks and misparses the next header on
  a >4 GiB packed member. Covered by a synthetic RAR3 test (a real fixture would be
  >4 GiB — infeasible to commit; `make_large_packed_fixture.py` builds one for optional
  local end-to-end validation).
- **F6** — `_merge_split_member` now rejects a split continuation that names a different
  file or follows a non-split member, so a crafted `split_before` flag can't fold an
  unrelated member's size/CRC into the previous one. Covered by a synthetic matrix
  (mismatched name / missing SPLIT_AFTER / matching merge).

See `QUESTIONS.md` for the two design calls that shaped F3/Q3 (reject `*`/`?`; length
check in `VerifyingStream` for every RAR member, not a global `SlicingStream` change).

## Top findings

| # | Sev | Finding | Where | Repro |
|---|-----|---------|-------|-------|
| F1 | High | Wrong header password → `CorruptionError` (not `EncryptionError`) with no check value; breaks multi-password candidate iteration for RAR3 header encryption and mislabels the error. | `rar_parser.py:598-601,877-883,1197`; `rar_reader.py:317,331`; `password.py:178` | `repro.py` F1 (confirmed) |
| F2 | Med-High | RAR5 header-size vint pre-read loop is uncapped + O(n²) (`start_bytes += b` per continuation byte); a few-MB all-`0x80` input → tens of seconds CPU. VISION #2 (bounded hostile parsing). | `rar_parser.py:1340-1344` | `repro.py` F2 (confirmed quadratic) |
| F3 | Med | Hostile member name passed to `unrar` argv with no `--` guard → leading `-` parsed as a switch (drops the filter, emits **all** members' data — wrong-bytes confusion, exit 0), leading `@` as an **arbitrary local-file read**. Contradicts the spec's argv-constraint intent on the hostile-name axis. `--` fixes the switch case but not `@`. | `rar_unrar.py:78-84`; `rar_reader.py:574-576` | **end-to-end** via committed `hostile_argv__{,.rar4}.rar` (opening `-inul`/`@atfile` → `CorruptionError`); `repro.py` F3b + `@atfile` local-read confirmed |
| F4 | Med | `unrar` exit-code map only handles code 11 (bad password). Codes 2/3/10 (fatal / CRC / no-match) are unmapped; the nonsolid single-member `SlicingStream` never checks it produced `size` bytes, so a mis-parsed/corrupt member without a CRC yields a silent short/empty stream. VISION #3 (honest error on damage). | `rar_reader.py:159-164,578-583` | corrupt→exit 3, no-match→exit 10 confirmed (`repro.py`) |
| F5 | Low-Med | RAR3 `FILE_LARGE` member >4 GiB: `add_size` used for `_seek_after_packed` is only the low 32 bits; `HIGH_PACK_SIZE` extends `compress_size` but not the skip, so the walk under-seeks and misparses every member after a >4 GiB one. | `rar_parser.py:851-854,945` vs `1009-1013` | code-traced (needs >4 GiB fixture) |
| F6 | Low | `_merge_split_member` merges a `split_before` continuation into the previous member with **no name/attribute check**; a crafted continuation flag after an unrelated complete member silently folds sizes/CRC into the wrong member (and can hide a member). | `rar_parser.py:291-295,918-923,1285-1289` | code-traced |

Two smaller notes (masked inner-close exception on the `rc==11` raise; header-encrypted
`CMT` comment decoded from still-encrypted bytes) are in `contract.md`.

## Where I disagree / what is actually fine

- **Member-table bomb hardening (#83) is complete and correct for RAR.** The parser
  ceiling (`_MAX_ARCHIVE_MEMBERS = 1_048_576`) bounds count during the up-front walk,
  and `ResourceLimitError` fires at `members()` for both `max_members` and
  `max_metadata_bytes` — verified live (`max_members=1` → `ResourceLimitError`).
  Aggregate name bytes are ~1:1 with on-disk header bytes (no amplification), so the
  count cap plus the materialization-time metadata cap is adequate; the spec
  explicitly sanctions allocating up to the parser ceiling before listing caps apply.
  Not a finding.
- **vint decoding itself is safe.** `_load_vint` caps at 11 bytes and cannot spin or
  overflow (Python bigint). F2 is a *separate* pre-read loop, not `_load_vint`.
- **`_seek_after_packed` / offset arithmetic is guarded** against negative, past-`_MAX_SEEK`,
  and `OSError`/`OverflowError` seeks. The only gap is the RAR3-LARGE low-32 truncation (F5).
- **SFX scan is bounded** to `SFX_MAX = 2 MiB`.
- **Subprocess lifecycle is sound.** Every `unrar` `Popen` is wrapped in a
  `_track_decompressed`-registered `_UnrarOwnedStream`, so reader teardown /
  `BaseException` mid-stream terminates and reaps it; `terminate_unrar` is idempotent
  (polls first). Solid-pipe truncation surfaces as `TruncatedError` via
  `SolidBlockReader` → `EOFError`. No `shell=True`; argv is a list. The old finding-#2
  "subprocess not released on BaseException" shape does **not** recur.
- **No re-implemented error boundary (S1) or pass driver (S3).** `RarReader` routes its
  solid pass through `SolidBlockReader` and defers translation to the shared
  `_wrap_member_stream` / `_translated_errors` boundary rather than hand-rolling a
  per-site translate/stamp. F1/F4 are *gaps* in what reaches that boundary, not a
  parallel copy of it.
- **`rarfile` as a test oracle** is used only in `tests/`; not flagged.

See `hostile-input.md` (F1, F2, F5, F6), `unrar-boundary.md` (F3, F4), `contract.md`
(error-contract seam + small notes), and `QUESTIONS.md` (three maintainer decisions).
