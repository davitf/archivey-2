## 1. CI — RAR open actually runs

- [x] 1.1 Install RARLAB `unrar` in `.github/workflows/atheris-fuzz.yml` (mirror `ci.yml`) so `rar_open_available()` is true on ubuntu-latest.
- [ ] 1.2 Confirm on the next main-push / workflow_dispatch log that `rar` is not skipped for missing binary (header + open both run).

## 2. ZIP deepen — headers, content, bounded read

- [ ] 2.1 Extend ZIP CRC/header fixup beyond stored-only where feasible (deflate payload-preserving header edits; keep minority broken-CRC); unit-test in `tests/test_atheris_crc_fixup.py`.
- [ ] 2.2 Change the ZIP Atheris target to list a few members then `open` + bounded `read` (byte/member caps) through `open_archive`, accelerators off.
- [ ] 2.3 Seed ZIP corpus/adversarial fixtures that exercise stored, deflate, and WinZip AES when extras/fixtures exist; try empty/`password` candidates for encrypted seeds.
- [ ] 2.4 Split or rebalance ZIP vs TAR budgets so deepened ZIP does not starve TAR list coverage.

## 3. Stream/codec Atheris targets

- [ ] 3.1 Add `unix_compress` (or `streams`) target: `open_codec_stream(Codec.UNIX_COMPRESS, …)` with seekable indexing on; seeds from tiny/hostile `.Z` blobs + corpus; per-input timeout.
- [ ] 3.2 Wire target into `iter_target_specs`, DEFAULT_BUDGETS, and atheris-fuzz.yml budget env exports.
- [ ] 3.3 Optionally add a second stream slice (xz or lzip) if remaining budget allows after a green run — skip-clean if deferred.

## 4. Budget + docs

- [ ] 4.1 Rebalance partitioned seconds (~150–170s total) per design table; keep `BUDGET_SCALE` multiplication.
- [ ] 4.2 Sync threat-model / CONTRIBUTING one-liner if the target list changes (RAR open CI, stream slice, ZIP read).

## 5. Verify

- [ ] 5.1 Unit tests for broadened ZIP fixup; smoke `python -m tests.atheris_fuzz --smoke` covering zip + unix_compress + rar (with unrar).
- [ ] 5.2 `openspec validate --strict atheris-harness-depth`
- [ ] 5.3 Manual or CI check: atheris job log shows rar open running and new stream target completing without skip-for-missing-unrar.
