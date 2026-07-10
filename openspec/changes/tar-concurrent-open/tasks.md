## 1. Spike and fixtures

- [ ] 1.1 Confirm seekability of the uncompressed stream for plain TAR and compressed TAR-RA (gz/bz2/xz at minimum) under `TarReader(streaming=False)`; document any non-seekable codec in the PR / `IDEAS.md`
- [ ] 1.2 Inventory sparse / GNU sparse TAR fixtures (or add a minimal one) and record whether `offset_data`+`size` matches `extractfile` output today

## 2. SharedSource wrap in TarReader

- [ ] 2.1 Open plain and compressed TAR-RA so the uncompressed seekable stream is owned by a `SharedSource`; feed `tarfile` a catalog/cursor view (not the raw SharedSource)
- [ ] 2.2 Implement `_open_member` via `SharedSource.view(offset_data, size)` + existing `_wrap_member_stream` for normal FILE members
- [ ] 2.3 Preserve correct sparse behavior (keep `extractfile` for sparse if needed; do not regress)
- [ ] 2.4 Implement forward-cursor view reuse: reuse when seeking forward and the cursor is free; mint a new view when an earlier offset is needed while busy
- [ ] 2.5 Close lifecycle: tarfile close, then SharedSource / owned codec stream; member views non-owning; read-after-close still fails loudly

## 3. Specs, ABC, docs

- [ ] 3.1 Land after `concurrent-open-opt-in` (it owns the `archive-reading` rewrite + the opt-in gate); apply this change's `format-tar` delta
- [ ] 3.2 Ensure TAR-RA honors the `allow_multiple_open_streams` gate (no TAR special-casing); the `_open_member` docstring exemption drop lives in `concurrent-open-opt-in`
- [ ] 3.3 Update `docs/parallel-reader.md` TAR-RA audit row and any "single decoder / exempt" language that these changes supersede

## 4. Tests

- [ ] 4.1 Interleaved concurrent open+read for plain TAR-RA (two members, partial reads alternating)
- [ ] 4.2 Same interleave test for compressed TAR-RA (at least `.tar.gz`) when uncompressed stream is seekable
- [ ] 4.3 Sequential archive-order open/read regression (forward-cursor path; bytes correct)
- [ ] 4.4 Streaming TAR unchanged (no concurrent-open requirement; existing streaming tests still pass)
- [ ] 4.5 Guard: if a codec path is non-seekable, assert documented limitation rather than silent corruption

## 5. Verification

- [ ] 5.1 `uv run --no-sync ruff check` on touched paths
- [ ] 5.2 `uv run --no-sync pyrefly check` and `uv run --no-sync ty check` clean
- [ ] 5.3 `uv run --no-sync pytest` for TAR / concurrent-open related tests (and full suite before push per CONTRIBUTING three-config gate)
