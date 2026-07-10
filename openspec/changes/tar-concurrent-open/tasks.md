## 1. Locked member-stream primitive

- [ ] 1.1 Add a `streamtools` wrapper that delegates to an inner `BinaryIO` and holds a caller-supplied lock across each data-path `read` / `readinto` (and related methods that would otherwise call the unlocked inner stream)
- [ ] 1.2 Unit-test the wrapper: two fake seek-before-read streams sharing one underlying handle + one lock; single-thread interleave and threaded interleave both return correct bytes
- [ ] 1.3 Re-export from `streamtools` as appropriate

## 2. Wire TAR-RA and ISO

- [ ] 2.1 Give `TarReader` a per-instance lock; wrap `extractfile` results with the helper before `_wrap_member_stream` (random-access path)
- [ ] 2.2 Give `IsoReader` a per-instance lock; wrap pycdlib member streams the same way
- [ ] 2.3 Confirm wrapper placement so buffered `_wrap_member_stream` layers do not bypass the lock
- [ ] 2.4 Leave streaming TAR unchanged

## 3. Specs and docs

- [ ] 3.1 Land after / with `concurrent-open-opt-in` (it owns the `archive-reading` rewrite + opt-in gate); apply this change's `format-tar` and `format-iso` deltas
- [ ] 3.2 Ensure TAR-RA and ISO honor `allow_multiple_open_streams` with no special-case exemption; ABC docstring updates live in `concurrent-open-opt-in`
- [ ] 3.3 Update `docs/parallel-reader.md` TAR-RA and ISO audit rows (seek-before-read + lock wrapper)

## 4. Tests

- [ ] 4.1 Opted-in interleaved open+read for plain TAR-RA
- [ ] 4.2 Opted-in interleaved open+read for compressed TAR-RA (at least `.tar.gz`)
- [ ] 4.3 Sparse TAR member still expands correctly (fixture or skip if none)
- [ ] 4.4 Opted-in interleaved open+read for ISO
- [ ] 4.5 Sequential extract regression for TAR and ISO (uncontended lock path)
- [ ] 4.6 Streaming TAR existing tests still pass

## 5. Verification

- [ ] 5.1 `uv run --no-sync ruff check` on touched paths
- [ ] 5.2 `uv run --no-sync pyrefly check` and `uv run --no-sync ty check` clean
- [ ] 5.3 `uv run --no-sync pytest` for TAR / ISO / streamtools tests (full three-config gate before push per CONTRIBUTING)
