## MODIFIED Requirements

### Requirement: Read-only stream wrappers share one internal base

Read-only wrappers in this layer SHALL share an internal base for the read-only
`BinaryIO` surface (`readable`, `writable`, `write`) and canonical `readinto` /
`readall` built from each wrapper's `read`. The public codec-stream path SHALL
return an `ArchiveStream` carrying stream-level presentation metadata; internal
`backend.open()` calls MAY return raw backend streams.

The seekable decompressor path SHALL be a single concrete stream class
parameterized by a `Decoder` strategy, not a per-codec subclass hierarchy. Every
codec — forward-only and segmented alike — SHALL plug in through **one** decoder
protocol:

```python
Segment = tuple[int, int]  # (decompressed_size, compressed_size)

class Decoder(Protocol):
    def feed(self, data: bytes) -> tuple[bytes, list[Segment]]: ...
    def flush(self) -> tuple[bytes, list[Segment]]: ...
    def is_finished(self) -> bool: ...
    # Default no-op; only index-bearing codecs (xz, lzip) override it.
    def build_index(
        self, inner: BinaryIO, last_known: SeekPoint
    ) -> tuple[list[SeekPoint], int | None]: ...
```

Forward-only codecs SHALL return an empty segment list and inherit the no-op
`build_index`. The stream — not the decoder — SHALL own the buffer, position,
compressed/decompressed cursors, seek-point table, and seek algorithm, deriving
seek points from the segments each `feed`/`flush` reports. Adding a codec SHALL
add a `Decoder`, and MUST NOT require a new stream subclass.

#### Scenario: wrapper surface matrix

| Case | Expected |
| --- | --- |
| Any read-only stream wrapper is used | Shared base supplies read-only surface and `readinto` / `readall` |
| Public codec stream is opened | Returned object is an `ArchiveStream` with stream presentation metadata |

#### Scenario: decoder composition matrix

| Case | Expected |
| --- | --- |
| Forward-only codec (zlib, brotli, ppmd, bcj, deflate64) | Implements `feed`/`flush`/`is_finished`; `feed`/`flush` return empty segments; inherits no-op `build_index` |
| Segmented codec (xz, lzip, unix-compress) | Reports completed `Segment`s; stream derives seek points and advances cursors |
| Index-bearing codec (xz, lzip) | Overrides `build_index`; stream drives it demand-driven per `seekable-decompressor-streams` |
| A new codec is added | One `Decoder` added; no new stream subclass; no `SegmentedDecompressorStream` layer |
