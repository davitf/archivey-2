## 1. Lock the decision

- [ ] 1.1 Maintainer picks Option A–E in `design.md` (close Open Question 1; update Decision 2 from provisional to locked)
- [ ] 1.2 If not Option D: rewrite provisional `specs/format-tar` / `specs/documentation` deltas (and add `archive-reading` / extract deltas as needed) before coding
- [ ] 1.3 Record CLI strict-EOF intent for `cli-v1` (Open Question 2) as a one-line cross-link — do not implement CLI here unless that change is co-applied

## 2. Implement the locked option

- [ ] 2.1 Option A or D: no `ArchiveyConfig` default change; skip code default flip
- [ ] 2.2 Option B: set `strict_archive_eof: bool = True` in `config.py` + `archive-reading` signature; update default-config tests
- [ ] 2.3 Option C: resolve strictness from access mode (`streaming=False` → strict True unless overridden); document the split; update RA vs streaming tests
- [ ] 2.4 Option E: default True for listing/iter plus soft archive-level EOF on `extract_all` per locked design; add report/diagnostic wiring tests
- [ ] 2.5 Sync `tar_reader` / diagnostics only if the locked option needs behavior beyond today’s escalate path

## 3. Docs and open-issues

- [ ] 3.1 Update `docs/formats.md` TAR EOF section for the locked stance (opt-in vs new default / escape hatch)
- [ ] 3.2 When user Gotchas exists (or in the same docs PR): TAR silent-shorten + strict knob + “may improve with native TAR later”; post-v1 ZIP items as current limitations
- [ ] 3.3 Close or reword `docs/internal/open-issues.md` P1 to the locked outcome; point at this change

## 4. Verify

- [ ] 4.1 Targeted tests: `tests/test_tar.py` EOF section, `tests/test_archivey_config.py`, diagnostics IGNORE vs strict (plus any new default/split cases)
- [ ] 4.2 `openspec validate --strict decide-strict-archive-eof-default`
