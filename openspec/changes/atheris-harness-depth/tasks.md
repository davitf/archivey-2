## 1. CI — RAR open actually runs

- [x] 1.1 Install RARLAB `unrar` in `.github/workflows/atheris-fuzz.yml` (mirror `ci.yml`) so `rar_open_available()` is true on ubuntu-latest.
- [ ] 1.2 Confirm on the next main-push / workflow_dispatch log that `rar` is not skipped for missing binary (header + open both run).

## 2. ZIP deepen — headers, content, bounded read

- [ ] 2.1 Extend ZIP CRC/header fixup beyond stored-only where feasible (deflate payload-preserving header edits; keep minority broken-CRC); unit-test in `tests/test_atheris_crc_fixup.py`.
- [ ] 2.2 Change the ZIP Atheris target to list a few members then `open` + bounded `read` (byte/member caps) through `open_archive`, accelerators off.
- [ ] 2.3 Seed ZIP corpus/adversarial fixtures that exercise stored, deflate, and WinZip AES when extras/fixtures exist; try empty/`password` candidates for encrypted seeds.
- [ ] 2.4 Split or rebalance ZIP vs TAR budgets so deepened ZIP does not starve TAR list coverage.

## 3. Stream/codec Atheris targets (all standalone codecs)

- [ ] 3.1 Add parameterized stream targets (or one target per codec) for unix-compress, xz, lzip, gzip, bzip2, lzma-alone, and zlib: `open_codec_stream` with seekable indexing on when supported; seeds from tiny/hostile blobs + corpus; per-input timeout where hang classes exist; accelerators off.
- [ ] 3.2 Register optional-extra stream targets for zstd, brotli, lz4, and deflate64 with skip-clean when the backend is absent.
- [ ] 3.3 Wire all stream targets into `iter_target_specs`, `DEFAULT_BUDGETS` / `TARGET_NAMES`, and atheris-fuzz.yml budget env exports.
- [ ] 3.4 Bump the atheris workflow job `timeout-minutes` to fit the full partition (illustrative ~4–5+ minutes before `budget_scale`).

## 4. Budget + docs

- [ ] 4.1 Set partitioned seconds for every required target (grow total wall time; do not drop stream slices to fit a short ceiling); keep `BUDGET_SCALE` multiplication.
- [ ] 4.2 Sync threat-model / CONTRIBUTING one-liner for the expanded target list (RAR open CI, ZIP read, full stream/codec set).

## 5. Verify

- [ ] 5.1 Unit tests for broadened ZIP fixup; smoke `python -m tests.atheris_fuzz --smoke` covering zip, rar (with unrar), and each required stream codec.
- [ ] 5.2 `openspec validate --strict atheris-harness-depth`
- [ ] 5.3 Manual or CI check: atheris job log shows rar open running and all required stream targets completing (optional extras may skip).
