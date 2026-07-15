## 1. Expose the in-file counter

- [ ] 1.1 Add a `member_bytes` property to `BombTracker` (mirrors `total_bytes`; returns `_member_bytes`)

## 2. Progress payload

- [ ] 2.1 Add `member_bytes_written: int` to `ExtractionProgress` (trailing field; update docstring)

## 3. Emit during the copy loop

- [ ] 3.1 Give `_copy_to_fileobj` a throttled progress emit (pass an `emit_progress` closure / member+counter context; keep it a no-op when `on_progress is None`)
- [ ] 3.2 Emit inside the read loop after `tracker.count(...)`, bounded by the 1 MiB copy chunk
- [ ] 3.3 Keep the terminal per-member report firing with `member_bytes_written == size` (or final byte count when `size is None`)
- [ ] 3.4 Non-FILE members (dir/link/hardlink) emit a single report with `member_bytes_written == 0`

## 4. Tests

- [ ] 4.1 Large FILE member: `on_progress` fires >1× with non-decreasing `member_bytes_written` ending at `size`
- [ ] 4.2 Sub-chunk FILE member: exactly one callback with `member_bytes_written == size`
- [ ] 4.3 Directory / symlink / hardlink: single callback, `member_bytes_written == 0`
- [ ] 4.4 Unknown-`size` member: terminal report equals final observed byte count
- [ ] 4.5 `on_progress is None`: behavior and byte totals unchanged (no regressions in ratio/limits)

## 5. Validate

- [ ] 5.1 `uv run pyrefly check` and `uv run ty check` clean
- [ ] 5.2 `openspec validate --strict extraction-progress-in-file`
