# Tasks — multi-member gzip detection via rapidgzip's index

> Investigation + specs + implementation. Read `design.md` first (index authoritative only
> because the check protects *valid* files; the open rapidgzip-API question gates implementation).
> Run tooling through `uv`. Test in all three dependency configs before pushing.

## 1. Confirm rapidgzip exposes member boundaries

- [ ] 1.1 Determine rapidgzip 0.16's index accessor (`block_offsets()` / `export_index()` / other)
      and whether it distinguishes gzip **member/stream** starts from deflate **block** offsets.
- [ ] 1.2 Confirm the index is fully populated after a sequential read to EOF and that querying it
      forces no extra decode.
- [ ] 1.3 If member boundaries are not derivable, STOP: record the finding, keep the byte scan,
      and mark this change a no-op (document in `known-issues.md`).

## 2. Implement the index query

- [ ] 2.1 Add an accessor from `_GzipTruncationCheckStream` to the wrapped accelerator handle
      (no dependency leak beyond the codec layer).
- [ ] 2.2 Replace `_has_additional_gzip_member` with "index reports ≥2 members?"; keep
      `gzip_has_additional_member` as the fallback when the index is unavailable.
- [ ] 2.3 Preserve the conservative direction: on the ambiguous truncated-mid-second-member case,
      fall back to "further magic ⇒ do not raise" (never false-positive on a valid file).

## 3. Tests

- [ ] 3.1 Valid 2- and 3-member gzip via rapidgzip → no `TruncatedError`, and the byte scan is
      **not** invoked (spy/counter).
- [ ] 3.2 Truncated single-member → `TruncatedError` from the index, no whole-file scan.
- [ ] 3.3 Truncated mid-second-member → conservative fallback; valid sibling never false-flagged.
- [ ] 3.4 Index-unavailable → byte-scan fallback; behavior identical to today.
- [ ] 3.5 `uv run pyrefly check` + `uv run ty check` clean; `uv run ruff format`; full suite in
      `[all]`, `[all-lowest]`, `[core-only]`.

## 4. OpenSpec

- [ ] 4.1 `openspec validate --strict gzip-multimember-detect-via-index` green.
- [ ] 4.2 Note the follow-on: the deferred per-member ISIZE **sum**
      (`rapidgzip-truncation-investigation`) should build on this index accessor.
- [ ] 4.3 Sync the delta into main `seekable-decompressor-streams` when landing (coordinate with
      `gzip-truncation-backstop-any-seekable`, which edits the same requirement).
