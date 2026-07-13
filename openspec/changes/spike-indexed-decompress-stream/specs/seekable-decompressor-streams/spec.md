## ADDED Requirements

### Requirement: Native indexed codecs share one seek surface

Random access for native indexed single-stream codecs (xz, lzip, unix-compress CLEAR
points, and any later native-indexed codec such as BGZF) SHALL share one seek / EOF /
truncation semantics surface:

- Demand-driven indexes (no seek-point tables or index scans when seekability is undeclared).
- `SEEK_END` uses a known decompressed size from the index when available; otherwise scans
  without buffering the entire remainder in RAM.
- Recoverable index/trailer scan failure emits `SEEK_INDEX_DEGRADED` and falls back to
  sequential decoding (unless diagnostics escalate).
- Backward seeks that resume from format-native seek points MUST NOT emit
  `STREAM_REWIND_REDECOMPRESSES`.

Adding a native-indexed codec MUST NOT introduce a divergent seek/EOF/truncation
implementation for that shared surface. Optional accelerators (rapidgzip) remain outside
this surface and keep their existing contracts.

Existing per-codec requirements (xz/lzip native indexes, unix-compress CLEAR points,
index-less rewind diagnostics, accelerator lifecycle) are unchanged.

#### Scenario: shared surface parity

| Axis | xz / lzip / `.Z` / future native-indexed |
| --- | --- |
| Undeclared seekability | No seek-point table; no footer/trailer/CLEAR index retention |
| `SEEK_END` with index size | Returns size without full redecompress when the index knows it |
| `SEEK_END` without size | Scans to EOF without holding all remaining output in RAM |
| Recoverable index scan failure | `SEEK_INDEX_DEGRADED` + sequential fallback |
| Indexed backward seek | No `STREAM_REWIND_REDECOMPRESSES` |
| Accelerator path (gzip/bzip2) | Unchanged; not required to share this native surface |
