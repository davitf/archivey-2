## 1. Decision (locked — Option F)

- [x] 1.1 Maintainer picked Option F in `design.md` (signal-aware default: rejected header
      raises `CorruptionError` regardless of flag; missing/short trailer warns by default,
      `TruncatedError` under strict)
- [x] 1.2 Deltas rewritten for Option F (`specs/format-tar`, `specs/documentation`); no
      `archive-reading` delta (config default/signature unchanged)
- [x] 1.3 CLI strict-EOF intent recorded for `cli-v1` in `design.md` Open Question 2
      (`archivey test` defaults to strict EOF); not implemented here

## 2. Implement Option F in `tar_reader`

- [x] 2.1 No `ArchiveyConfig` / `config.py` default change (stays `False`)
- [x] 2.2 `_EofProbeStream` read probe wraps the random-access fileobj (plain +
      compressed + stream sources); `_capture_eof_probe` snapshots tarfile's final header
      attempt (`last_read` after the scan) — not `offset_data + roundup(size)` (wrong for
      GNU sparse)
- [x] 2.3 `_verify_tar_eof`: rejected header (non-null stop block, or the trailing-block
      proxy in streaming) → `CorruptionError` unconditionally; missing/short trailer →
      warn by default, `TruncatedError` under strict; valid two-block trailer OK
- [x] 2.4 Extract behavior recorded: random access fails closed (materializes members before
      writing, raises before any write); streaming writes salvageable members then raises
- [x] 2.5 Streaming final-header limitation documented (probe unavailable under `_Stream`)

## 3. Docs and open-issues

- [x] 3.1 `docs/formats.md` TAR EOF section updated (rejected header raises by default;
      strict escalates the missing-trailer residual; streaming caveat)
- [x] 3.2 `docs/gotchas.md`: rejected-header raise + strict knob + streaming final-header
      limitation, framed as today's behavior with "native TAR later"
- [x] 3.3 `docs/internal/known-issues.md` rewritten (probe mechanism + streaming gap);
      `docs/internal/open-issues.md` P1 reworded to "decided + implemented — Option F"

## 4. Verify

- [x] 4.1 Targeted tests in `tests/test_tar.py`: rejected final header → `CorruptionError`
      (random access, default config); rejected mid header → `CorruptionError` (both modes);
      rejected final header streaming → warns (limitation); padded-tar no false positive;
      extract fails closed; GNU sparse last member + corrupt final → `CorruptionError`;
      compressed corrupt final; `strict=True`/IGNORE + nonzero still `CorruptionError`;
      streaming extract salvage-then-raise; existing warn/strict + minimal-trailer cases
      still pass
- [x] 4.2 Suite green in all three dependency configs (`[all]` full 1759 passed; `[all-lowest]`
      and `[core-only]` on the affected areas) after rebasing onto `main` (incl. #157)
- [x] 4.3 `openspec validate --strict decide-strict-archive-eof-default` clean
