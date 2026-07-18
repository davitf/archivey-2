## Context

The 7z next-header is read from the file in one shot and CRC-checked
(`read_signature_and_next_header`, `sevenzip_parser.py:212`), producing
`header_data: bytes`. Everything after that — `parse_header_block(header_data)`
and its whole callee tree — parses **in-memory** but does so through
`io.BytesIO`: `parse_header_block` wraps `header_data` in a `BytesIO`, and
`_read_files_info` re-wraps each property payload in its own `BytesIO`
(`sevenzip_parser.py:665`). Per-field access is `BytesIO.read()`; the truncation
pre-check in `_read_exact` historically ran a `tell`/`seek(END)`/`seek(back)`
triple (`_buffer_len`), which #146 reduced to an O(1) `getbuffer().nbytes` for
`BytesIO` but still leaves a Python method call and a `BytesIO.read()` per field.

There is no I/O below `parse_header_block`; the stream abstraction is pure cursor
bookkeeping over bytes that are already resident. The encoded-header path stays
in-memory too: an `EncodedHeader` is decompressed to another `bytes` blob and
re-fed to `parse_header_block`.

**Provenance.** `review/performance/listing-attribution.md` (L1) fixed the 7z
name byte-loop and left the residual as "non-name parse + model build"; the
per-field stream reads (booleans, three time arrays, attributes, CRCs,
start-positions) are the non-name-parse half. The RAR native parser already
demonstrates the target design in this repo: it reads each block header into a
`bytes` buffer, then walks it with `_load_vint`/`_load_byte`/`_load_le32`/
`_load_bytes`/`_load_vstr`(buf, pos) → (value, new_pos) (`rar_parser.py:444`+).

## Goals / Non-Goals

**Goals:**
- Replace the in-memory `BytesIO` header parse with a byte-cursor reader over
  `memoryview(header_data)`, removing per-field stream-method and per-property
  `BytesIO` overhead.
- Keep the parser structure and function boundaries recognizable (one reader
  object threaded through, or `(buf, pos)` returns), consistent with RAR.
- Preserve every observable behavior and every hostile-input bound exactly.

**Non-Goals:**
- The file-level layer (`read_signature_and_next_header`) — it seeks in the real
  file and stays on `BinaryIO`.
- Model build (`materialize_archive`, `SevenZipFileRecord` / `ArchiveMember`
  construction) — the *other* half of the residual, untouched here.
- ZIP / TAR (stdlib-parsed, no archivey byte loop) and RAR (already cursor-based).
- Chasing a specific ratio target; the Q1/Q2 native-band enforcement decision is
  still open. This change removes a known overhead source; it does not promise a
  band.

## Investigations

Where the header-parse time goes after #146 (per `listing_probe.py sevenzip`,
2,000 × 64 B members):

| Layer | Mechanism today | Cursor effect |
|---|---|---|
| Name decode | bulk `decode("utf-16le")` (L1, done) | unchanged |
| Booleans / times / attrs / CRCs / start-pos | one `BytesIO.read(n)` + `_read_exact` bound check per field | `int.from_bytes(mv[p:p+n], …)`; bound is `p+n <= len(buf)` |
| Per-property dispatch | `io.BytesIO(_read_exact(buffer, size, …))` per property | slice `mv[p:p+size]`; no object alloc |
| Truncation check | `_buffer_remaining` (O(1) for BytesIO, still a call) | inline comparison at the read chokepoint |

Reader-shape options considered (see Decision 1):

| Shape | Pattern | Notes |
|---|---|---|
| A. `(buf, pos)` returns | RAR's `_load_*` style | most faithful to RAR; verbose threading of `pos` |
| B. small `_Cursor` class | `cur.u8()`, `cur.read(n)`, `cur.uint64()` mutating `self.pos` | keeps call-site readability of the current `_read_*`; single bounds chokepoint |
| C. keep BytesIO, micro-opt | — | rejected: leaves the per-field method dispatch that is the point |

## Decisions

### 1. A single mutable `_Cursor` over `memoryview` (shape B)

Introduce a tiny internal `_Cursor` wrapping `memoryview(header_data)` with a
mutable `pos` and methods mirroring today's primitives: `read(n)`, `byte()`,
`uint32()`, `real_uint64()`, `uint64()` (7z variable-length), `remaining()`, and
a bounded `slice(n)` returning a sub-view for property payloads. Every `_read_*`
and `_handle_*` takes a `_Cursor` instead of `BinaryIO`.

Chosen over the pure `(buf, pos)` return style (shape A) because the existing 7z
code reads as `x = _read_uint64(buffer)`; a mutating cursor preserves that
call-site shape with the smallest diff and keeps a **single** place that enforces
bounds (`read`/`slice`), which matters for the hostile-input contract. RAR uses
shape A because its fields interleave with control flow that already threads
offsets; 7z does not, so B is the better fit here even though A is the nominal
"match RAR" answer. **Rejected:** C (keep BytesIO) — it preserves exactly the
overhead this change exists to remove.

### 2. `memoryview`, not `bytes` slices

Back the cursor with `memoryview(header_data)` so property payloads and read
windows are non-copying sub-views. The one place that needs real `bytes` is the
bulk name decode (`decode("utf-16le")`), which accepts a memoryview via
`bytes(mv)` / `mv.tobytes()` at that single call. **Rejected:** slicing `bytes`
everywhere — reintroduces per-property copies the BytesIO version already avoided.

### 3. Bounds and error semantics are invariant

`_Cursor.read`/`.slice` raise `CorruptionError` with the same `context` messages
when `pos + n` exceeds `len(buf)` (replacing the `_read_exact` truncation branch),
and the `num_files`/table-count bound in `_read_files_info` keeps checking against
`len(buf)` (was `_buffer_len(buffer)`). Negative/oversized length guards
(`_MAX_NEXT_HEADER_SIZE`, `_MAX_SEEK_OFFSET`, `_MAX_UTF16_CHARS`) move onto the
cursor unchanged. Net: the `format-7z` "Bound 7z header count fields" scenario
matrix and every `CorruptionError`/`UnsupportedFeatureError` path produce
identical outcomes — this is why the change ships **no spec delta**.

### 4. Shared vs. per-format reader: share the format-agnostic primitives only, and keep the hot path method-free

RAR and 7z now both parse an in-memory buffer with a position. It is tempting to
extract one shared reader class exposing `read_bytes(n)` / `read_int()` /
`read_vint()` for both (and maybe the streams layer). Decision: extract only the
**format-agnostic** primitives, prefer free functions over a stateful class on the
hot path, and leave the streams layer alone.

Rationale:
- **The variable-length encodings are not shared.** 7z's `uint64` uses a
  first-byte-mask scheme; RAR5's vint is 7-bits-per-byte continuation. A single
  `read_vint()` cannot serve both — those stay per-format (`read_vint_7z` /
  RAR's existing vint). Only `read_bytes(n)`, `u8`, `le16`/`le32`/`le64`, bounded
  `slice(n)`, and `remaining()` are genuinely common.
- **The win is not "method call beats method call."** Moving off `BytesIO`
  removes per-property object construction, the `tell`/`seek` truncation dance,
  and per-unit loops — not the cost of a call. A general-purpose *stateful* reader
  reintroduces a bound-method dispatch **and** a `self.pos` attribute load+store
  per field; in the hot per-member loop (thousands of fields) that can eat a real
  fraction of the gain. RAR's `_load_*(buf, pos) -> (val, pos)` free functions
  keep `pos` in a caller local precisely for this reason — it is not incidental.
- **Therefore:** hot per-member field reads use free functions with `pos` as a
  local (RAR's proven shape). The `_Cursor` class from Decision 1 stays for the
  *cold / structural* paths (streams-info, folders, one-per-archive fields) where
  readability matters and the call count is small. If a shared primitives module
  is later extracted (e.g. `internal/backends/_bytecursor.py`), it exposes the
  free functions; the class is a thin convenience wrapper over them.
- **Verify, don't assume.** Whether the stateful-class dispatch actually costs
  enough to matter is checkable on `listing_probe.py sevenzip`: prototype the 7z
  per-member loop both ways and compare the census/ratio before committing the
  shape. The probe is already the accept gate.

**Rejected — one shared reader for RAR + 7z + streams:** the streams layer is real
pull-based I/O over possibly-unseekable sources (`streamtools.read_exact`), not an
in-memory buffer; forcing a byte-cursor there would push toward buffering the whole
stream, contradicting the pull-based-streaming vision. The cursor is specifically
for already-materialized header bytes.

**Scope note.** This change is 7z-only (as scoped). It may introduce the primitives
in a form RAR *could* later adopt, but it does **not** rewrite RAR onto them —
extracting a shared module and migrating RAR is a separate follow-up so RAR's
fuzz-tested parser is not disturbed here.

### 5. Encoded-header path reuses the same cursor

The decoded encoded-header bytes are wrapped in a fresh `_Cursor` and re-parsed by
the same `parse_header_block`, exactly as the `BytesIO` version re-parses today.
No separate code path.

## Risks / Trade-offs

- **[Regressing a hostile-input bound during the port]** → Port bounds through the
  single `_Cursor.read`/`.slice` chokepoint; keep the Atheris 7z targets and the
  `format-7z` header-bound tests as the gate; add explicit unit tests that a
  truncated property and an out-of-range count raise `CorruptionError`.
- **[Behavior drift on odd inputs]** → Land behind the full existing 7z reader
  suite + `py7zr` oracle unchanged; the diff must not touch any fixture's parsed
  output. Treat any oracle/fixture diff as a bug in the port, not a spec change.
- **[`memoryview` lifetime / release]** → the cursor only reads; no `BytesIO`
  resize/close semantics apply, and the backing `bytes` outlives the parse. No
  `getbuffer()`-style export to release.
- **[Effort vs. uncertain payoff]** → the win is bounded by the non-name-parse
  slice and does not touch model build; measure with the probe first, and accept
  the change on the `read_exact`/parse-census drop plus a green suite even if the
  ratio stays above the native band (enforcement is Q2, out of scope).

## Open Questions

None blocking. Whether this closes enough of the 7z listing gap to matter is a
measurement outcome, not a design question — the probe census is the accept gate.
