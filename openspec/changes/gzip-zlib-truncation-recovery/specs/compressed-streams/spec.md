## MODIFIED Requirements

### Requirement: Each supported codec has a default backend

The system SHALL decompress supported codecs through these default backends:

| Codec | Default backend | Availability |
| --- | --- | --- |
| gzip | `DecompressorStream` + gzip-window `zlib` decoder (`wbits=16+MAX_WBITS`), multi-member chaining with GzipFile parity | core |
| bzip2 | stdlib `bz2` | core |
| xz | native xz stream over stdlib `lzma` | core |
| LZMA Alone | stdlib `lzma` `FORMAT_ALONE` | core |
| LZMA1 / LZMA2 raw | stdlib `lzma` `FORMAT_RAW` | core |
| Delta, BCJ x86/ARM/ARMT/PPC/SPARC/IA64 | `lzma` raw filters | core |
| raw Deflate | stdlib `zlib` (`-15`) via `DecompressorStream` | core |
| Copy/STORED | pass-through | core |
| zstd | stdlib `compression.zstd` (3.14+) / `backports.zstd` (<3.14) | optional `[zstd]` before 3.14; core on 3.14+ |
| lz4 | `lz4` | optional `[lz4]` |
| Brotli | `brotli` | optional `[7z]` |
| unix-compress `.Z` | native LZW `DecompressorStream` | core |
| PPMd var.H | `pyppmd` | optional `[7z]` |
| Deflate64 | `inflate64` | optional `[7z]` |
| AES-256 decrypt stage | wrapped crypto backend | optional `[crypto]` |

LZMA Alone SHALL be a distinct stream-codec descriptor from raw LZMA1/LZMA2
(`FORMAT_RAW` + properties). Alone is standalone (`StreamFormat.LZMA_ALONE`);
raw LZMA1/LZMA2 remain container-only.

The gzip stdlib path MUST NOT use `gzip.GzipFile` / `gzip.open` as the decode
engine. CRC/ISIZE check **outcomes** SHALL remain equivalent to stdlib gzip
(implemented via zlib’s gzip window). Concatenated multi-member gzip SHALL
decompress to the concatenation of member payloads with GzipFile parity: skip
zero padding between members; trailing zeros only end the stream cleanly;
trailing non-gzip junk after a completed member SHALL raise `CorruptionError`.
Optional rapidgzip acceleration remains gated by `seekable-decompressor-streams`
and is unchanged by this requirement.

#### Scenario: backend matrix

| Case | Expected |
| --- | --- |
| Default gzip stream (accelerator off / ineligible) | gzip-window `DecompressorStream`; not `GzipFile` |
| Concatenated multi-member `.gz` | Full concatenated payload |
| Multi-member `.gz` with zero padding between members | Full concatenated payload |
| Valid member then trailing zeros only | Clean EOF after payload |
| Valid member then trailing non-gzip junk | `CorruptionError` |
| Default zstd on Python 3.14+ | stdlib `compression.zstd` |
| Default zstd on Python 3.11-3.13 with `backports.zstd` | `backports.zstd` using the same API |
| Standalone `.lzma` / Alone stream | `lzma` in `FORMAT_ALONE` mode |
| 7z folder LZMA2 raw stream | `lzma` in `FORMAT_RAW` mode |
| Default unix-compress `.Z` stream | native LZW stream; no `uncompresspy` import |
| Core-only install opens `.Z` | Succeeds without optional extras |

### Requirement: Returned streams translate decompression errors

The system SHALL wrap backend streams so decompression failures surface as
Archivey exceptions: corrupt data as `CorruptionError`, unexpected end-of-input
as `TruncatedError`, and source seek requirements as the documented non-seekable
error. No raw backend exception SHALL escape. For zstd specifically,
`compression.zstd.ZstdError` SHALL map to `CorruptionError`, and its truncation
`EOFError` SHALL map to `TruncatedError`.

When a `DecompressorStream`-backed codec reaches compressed EOF while the
decoded stream is truncated — the decoder is not `finished`, **or** it reports
`finished` alongside a pending truncation (the unix-compress leftover-bits case)
— the stream SHALL deliver any recoverable decompressed prefix already produced
for a bounded `read(n)` (including flush leftover and bytes already buffered for
that call) before surfacing `TruncatedError`. The error SHALL be deferred via
`pending_error` and raised on the next empty `read`. `readall` / `read(-1)`
SHALL raise `TruncatedError` when incomplete EOF is discovered (it MUST NOT
return a prefix from that call). Large bounded `read(n)` SHALL recover the same
prefix as a `read(1)` loop (no byte-at-a-time requirement).

Silent success on truncated input remains forbidden across **every** surface:
incomplete EOF MUST NOT publish a clean complete decompressed `_size`. This
includes the raising `readall` / `read(-1)` path, which MUST NOT record a
completed `_size` (e.g. `self._size = self._pos`) *before* it raises — otherwise
a caller that catches the error and then calls `try_get_size` / `seek(SEEK_END)`
would read the prefix length back as a clean complete stream. `seek(SEEK_END)`
and size queries MUST raise the pending truncation or leave the size unknown,
never treat the recoverable prefix as a successful full stream. The rule is
codec-agnostic: a truncated unix-compress `.Z` stream is held to the same
contract (it MUST NOT report its prefix length as a clean complete size, even
though its decoder reports `finished`).

#### Scenario: decompression error matrix

| Case | Expected |
| --- | --- |
| Corrupt compressed stream is read | `CorruptionError` with backend exception as `__cause__` |
| Compressed stream ends mid-data; bounded `read(n)` | Recoverable prefix delivered; `TruncatedError` on next empty `read` |
| Truncated gzip via stdlib path; `read(65536)` | Correct prefix returned; `TruncatedError` on next empty `read` |
| Truncated gzip; `read(1)` loop | Same prefix length/content as large-read path; then `TruncatedError` |
| Truncated gzip; `readall` / `read(-1)` | `TruncatedError` (no prefix returned from that call) |
| Truncated stream; `seek(SEEK_END)` / `try_get_size` after incomplete EOF | `TruncatedError` or size remains unknown — never a silent prefix-as-complete size, never bare `AssertionError` |
| Truncated stream; `readall` raised, then `try_get_size` / `seek(SEEK_END)` on the same handle | Still `TruncatedError` or unknown — the raising `readall` MUST NOT have recorded a clean `_size` |
| Truncated unix-compress `.Z`; `seek(SEEK_END)` / `try_get_size` | `TruncatedError` or unknown — prefix length is never reported as complete despite `finished` |
| Zstd stream ends before end-of-frame marker | `TruncatedError`, not a silent short read |
| Zstd checksum frame is corrupted | `CorruptionError` with backend `ZstdError` as `__cause__` |

### Requirement: Content faults raise from read, never from close

This requirement scopes to the streams this change owns: `DecompressorStream`
(and every codec `Decoder` behind it) and `VerifyingStream` / the fused
`MemberVerifier`. Other backends (the rapidgzip accelerator and its
`_GzipTruncationCheckStream`, and any third-party wrapper) are **out of scope**
here; they already surface content faults from `read` rather than `close`, and
retargeting them is deferred (see the rapidgzip follow-up). The wording below is
a standing rule for the in-scope streams, not a claim that every stream type in
the library has been audited to it.

Decode and verify streams SHALL raise content `TruncatedError` and
`CorruptionError` from `read` / `readall` (and from size/seek paths that would
otherwise report a false clean completion). `close()` MUST NOT raise those
content faults. `close()` MAY still propagate teardown failures (`OSError`,
translated inner-close errors). Bounded `read(n)` MAY deliver recoverable or
valid bytes before a deferred empty-`read` fault; a caller that then closes
without a follow-up empty `read` MAY miss that deferred fault — accepted for
chunked abandons only. Complete-stream `read(-1)` / `readall` MUST raise when
an EOF content fault is known so `read(); close()` cannot silently accept
truncated or CRC-mismatched content. Deliberate partial read then close before
clean EOF remains quiet for digest/length verification (abandon before verdict).

`VerifyingStream` / fused `MemberVerifier` SHALL verify digests (CRC and other
expected hashes) at clean EOF. On bounded `read(n)`, every decompressed byte
SHALL be returned first and `CorruptionError` SHALL raise on the **next**
(terminal empty) `read` — not by dropping the last data chunk, and not from
`finish_on_close`. On `readall` / `read(-1)`, the complete-stream read SHALL
include the EOF verdict and SHALL raise `CorruptionError` on mismatch (and
`TruncatedError` on hash-less short) so `read(); close()` cannot silently
accept bad content. `finish_on_close` SHALL close the inner and MUST NOT
introduce a first content `TruncatedError` / `CorruptionError` solely because
the caller is closing.

On the complete-stream (`readall` / `read(-1)`) path the verifier SHALL drain
the inner to genuine EOF (`inner.read` returning `b""`) in **bounded** steps.
It MUST NOT assume a single `inner.read` returns the whole body: `inner` is an
arbitrary `BinaryIO` and MAY return fewer bytes than requested without being at
EOF (a short read). A single `inner.read(remaining)` therefore under-returns on
any short-reading inner and skips the EOF verdict — the drain loop fixes both.
When a decompressed size is declared, each step SHALL stay capped by the
remaining declared byte count so a corrupt/adversarial **over-long** stream is
stopped at the declared size (raising `CorruptionError`) and never slurped
unbounded into memory. This is why the sized path MUST NOT delegate to
`inner.read(-1)`; the size cap is a decompression-bomb bound, and the code
carrying it SHALL say so inline. The unsized path (no declared size, no cap)
MAY delegate to `inner.read(-1)` and then run the EOF verdict.

#### Scenario: close vs read matrix

| Case | Expected |
| --- | --- |
| Truncated `DecompressorStream`; catch on empty `read`; then `close()` | `close()` succeeds |
| Truncated gzip stdlib path; error already observed on `read`; then `close()` | `close()` succeeds |
| Digest/CRC mismatch; chunked `read(n)` | All content bytes delivered; terminal empty `read` raises `CorruptionError`; `close()` quiet |
| Digest/CRC mismatch; `read()` / `read(-1)` | Raises `CorruptionError` (complete-stream verdict); `close()` alone does not raise the digest fault |
| `read(); close()` with bad CRC | `read()` raises — must not succeed quietly |
| Hash-less short member; `read(-1)` | Raises `TruncatedError` |
| Hash-less short; chunked until empty | All available bytes delivered; terminal empty `read` raises `TruncatedError` |
| `read(-1)` over a short-reading inner (returns `< n`, not EOF) | Full body gathered via bounded drain; EOF verdict fires in that call |
| `read(-1)` over an over-long inner with a declared size | Stopped at the declared size; `CorruptionError`; inner not read unbounded past the cap |
| Partial read then `close` before clean EOF (verify) | No digest/length verdict |
| Inner teardown fails on `close` | Teardown error may propagate |

### Requirement: Decompressed output digests are verified at clean EOF

The verification stage SHALL compute available expected digest algorithms
incrementally over decompressed bytes and raise `CorruptionError` for a
computable mismatch at clean EOF. On bounded reads, the mismatch SHALL surface
on the **read after** all data chunks have been delivered (terminal empty
`read`): every valid byte is returned first; the following empty `read` raises.
On `readall` / `read(-1)`, the complete-stream call SHALL drain the inner to
genuine EOF in bounded steps (capped by the declared size when present; see the
"Content faults" requirement above) and raise on mismatch rather than return
success bytes and defer the verdict to a later `read` or to `close()`.
Partial/random-access reads SHALL NOT produce a digest verdict. `close()` MUST
NOT be the sole surface for a digest or short-length verdict.

Supported computable algorithms SHALL include `crc32` (via `zlib.crc32`),
`adler32` (via `zlib.adler32`), the `hashlib.algorithms_available` set, and
`blake2sp` (the 8-way parallel BLAKE2s tree hash used by RAR5), computed via an
internal zero-dependency hasher. A well-formed member carrying only a `blake2sp`
digest SHALL therefore be verified, not skipped. When an expected `adler32` is
installed on a verifying stream, it SHALL likewise be computed and checked (not
skipped as unknown).

When an expected digest cannot be computed because the algorithm is genuinely unknown
or a backend is missing, the system SHALL emit `DIGEST_UNVERIFIABLE` with algorithm,
non-secret reason, and member identity when available. Diagnostic policy controls
collection, logging/callback delivery, member attachment, and escalation.

#### Scenario: digest matrix

| Case | Expected |
| --- | --- |
| Expected `blake2sp` on a well-formed RAR5 member | Computed and verified; mismatch raises `CorruptionError` |
| Expected `adler32` on a verifying stream | Computed and verified; mismatch raises `CorruptionError` |
| Expected digest under a genuinely-unknown algorithm name | `DIGEST_UNVERIFIABLE` counted/retained/logged; bytes still returned without that check |
| Chunked read reaches EOF with mismatch | All valid chunks delivered; following terminal empty `read` raises |
| `read()` / `read(-1)` with mismatch | Raises `CorruptionError` naming the algorithm |
| `read(); close()` with mismatch | Fault on `read()` — not a quiet success |
| Caller abandons stream before clean EOF | No digest verdict or mismatch exception on `close` |
| Unverifiable digest resolves to `RAISE` | `DiagnosticRaisedError` halts open/read |

### Requirement: Read-only stream wrappers share one internal base

Read-only wrappers in this layer SHALL share an internal base for the read-only
`BinaryIO` surface (`readable`, `writable`, `write`) and canonical `readinto` /
`readall` built from each wrapper's `read`. The public codec-stream path SHALL
return an `ArchiveStream` carrying stream-level presentation metadata; internal
`backend.open()` calls MAY return raw backend streams.

The seekable decompressor path SHALL be a single concrete stream class
(`DecompressorStream`) parameterized by a `Decoder` strategy, not a per-codec
subclass hierarchy. Every codec — forward-only and segmented alike — SHALL plug in
through **one** decoder protocol, which also owns seek-index discovery:

```python
@dataclass
class DecodeOut:
    data: bytes
    points: list[SeekPoint]  # absolute; empty for forward-only codecs

class Decoder(Protocol):
    def recreate(self, point: SeekPoint, inner: BinaryIO) -> Decoder: ...
    def feed(self, chunk: bytes) -> DecodeOut: ...
    def flush(self) -> DecodeOut: ...
    @property
    def finished(self) -> bool: ...
    @property
    def pending_error(self) -> BaseException | None: ...
    def clear_pending_error(self) -> None: ...
    # Default no-op; only index-bearing codecs (xz, lzip, future BGZF) override it.
    def build_index(
        self, inner: BinaryIO, last_known: SeekPoint
    ) -> tuple[list[SeekPoint], int | None]: ...
```

The stream — not the decoder — SHALL own the buffer, position, seek-point table,
and seek algorithm; it SHALL be format-agnostic, storing whatever `SeekPoint`s a
decoder emits. The `Decoder` SHALL choose seek-point placement (member/stream start
vs. post-realignment) and MAY perform progressive index enrichment during `feed`
using the `inner` it retained from `recreate`, restoring `inner`'s position itself.
Forward-only codecs SHALL emit empty `points` and inherit the no-op `build_index`.
Deferred truncation SHALL be uniform across **every** forward path of the shared
engine — unix-compress leftover bits, and incomplete EOF for zlib/deflate, gzip,
xz, and lzip — surfacing through `pending_error` after delivering recoverable
bytes on bounded `read(n)`; the stream SHALL raise it on the next empty `read`,
and SHALL clear it via `clear_pending_error` after raising (and on seek reset).
No `Decoder.flush` SHALL raise a content `TruncatedError` in place of arming
`pending_error` (the rapidgzip accelerator path is out of scope for this change). `readall` / `read(-1)`
SHALL raise when `pending_error` is set after draining rather than returning a
prefix. `close()` SHALL NOT raise `pending_error`. Incomplete EOF SHALL NOT
publish a clean complete `_size`. Adding a codec SHALL add a `Decoder` and MUST NOT
require a new stream subclass or a `SegmentedDecompressorStream` layer.

#### Scenario: wrapper surface matrix

| Case | Expected |
| --- | --- |
| Any read-only stream wrapper is used | Shared base supplies read-only surface and `readinto` / `readall` |
| Public codec stream is opened | Returned object is an `ArchiveStream` with stream presentation metadata |

#### Scenario: decoder composition matrix

| Case | Expected |
| --- | --- |
| Forward-only codec (zlib/deflate, brotli, ppmd, bcj, deflate64) | Implements `recreate`/`feed`/`flush`/`finished`; empty `points`; no-op `build_index` |
| Gzip stdlib decoder | Same protocol; chains concatenated members with GzipFile parity (NUL pad / junk / trailing zeros); `needs_input` stays false while retained post-member `unused_data` remains to drain |
| Gzip NUL padding spanning a `feed` boundary | Zero run buffered across `feed` calls; not prematurely read as clean EOF nor as junk before the next header (or true EOF) is seen |
| Gzip member boundary under `read(1)` | Next member starts correctly; single-byte reads across the boundary lose no bytes |
| Gzip lone trailing partial magic (e.g. `1f` at EOF) | Retained (not decided eagerly); resolved at `flush`/EOF → `CorruptionError`, never a silently dropped second member |
| Segmented boundary codec (lzip, xz stream start) | `feed` emits a `SeekPoint` at the boundary; stream stores it |
| Progressive enrichment (xz block index) | `feed` scans footer via retained `inner`; restores position |
| One-shot / forward walk (xz, lzip; future BGZF) | `build_index` returns points + size; demand-driven per seekable spec |
| Deferred truncation (unix-compress; truncated zlib/gzip/deflate/xz/lzip) | Bounded `read(n)` delivers prefix; `pending_error`; raise on next empty `read`; not on `close` — no `flush` raises in place of arming it |
| `readall` on truncated stream | Raises `TruncatedError` |
| A new codec is added | One `Decoder`; no new stream subclass |
