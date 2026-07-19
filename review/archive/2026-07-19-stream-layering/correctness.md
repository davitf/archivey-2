# Correctness audit — slice / verify / outer / SharedSource

Part 1 of the brief. Every claim below was traced on PR #136 @ `2a6b91b` and,
where a bug is alleged, reproduced with a concrete input.

## Invariant list a fused stream MUST keep

These are the non-negotiables extracted from the audit. `collapse-design.md`
maps each onto the proposed fused `ArchiveStream`.

1. **`read(0)` / zero-length `readinto` is a no-op** — never triggers EOF
   verification (F1 today violates this).
2. **Read side effects cover every read path** — `readinto` must hash/bound
   identically to `read` (either route through `read`, or implement an
   equivalent `readinto`).
3. **Verify only on clean sequential EOF** (terminal empty read, or
   close-at-clean-EOF). Partial reads never verify. Seek off the sequential
   frontier disables digest/length checks for the rest of the handle's life.
4. **`expected_size` caps delivered bytes**, detects over-long output with a
   bounded one-byte trailing probe (also drains WinZip AES HMAC), and defers
   hashless short errors until **after** inner close so a more specific inner
   error wins. Digest mismatch still beats synthetic short.
5. **Typed inner errors keep precedence** over synthetic truncation/verify
   errors, but close must still close the inner and mark the wrapper closed
   (F2 today can skip teardown).
6. **Locked slice / SharedSource mode** never performs unlocked
   seek/tell/read on the shared handle; every seek+read pair is atomic under
   the shared lock.
7. **Single-consumer slice mode** may use the underlying position directly —
   do not silently switch it to re-seek semantics.
8. **Archivey-owned concurrent byte-range access** goes through
   `SharedSource` / locked views; no backend-local unsynchronized cursor
   scratch (`base_reader.py:488-505`).
9. **One public `ArchiveStream`** after first read; nested translators/stamps/
   rewind warnings compose on collapse. Failed lazy open is single-claim (no
   retry of a half-open backend).
10. **Leases/finalizers release exactly once**; collapsing an unregistered
    nested wrapper must not steal or drop a public lease.
11. **`.size` stays cheap** — explicit metadata first; never open a lazy stream
    solely to answer size.

---

## 1. `readinto` side-effect safety

| Layer | Base | `readinto` behaviour | Verdict |
|-------|------|----------------------|---------|
| `VerifyingStream` | `ReadOnlyIOStream` | → `read()` (`base.py:51-56`) | **OK** — hashes |
| `CountingReader` / `OutputCountingStream` | `DelegatingStream` | override counts filled bytes (`counting.py:54-62`, `:82-88`) | **OK** |
| `LockedStream` | `DelegatingStream` | override holds lock (`locked.py:36-44`) | **OK** |
| `_GzipTruncationCheckStream` | `DelegatingStream` | `readinto_passthrough=False` (`codecs.py:400-402`) | **OK** |
| `_AcceleratorStream`, `_UnrarOwnedStream`, `_PyCdlibStream`, `CloseLockedStream`, `SeekCountingStream` | `DelegatingStream` | no read side effect; passthrough fine | **OK** |
| `ArchiveStream` | `ReadOnlyIOStream` | own `readinto` → `inner.readinto` with translate (`archive_stream.py:329-340`) | **OK** — duplicates lazy-open + `_fail`; no read-side hash of its own today |
| `SlicingStream` / `_MemberSlice` | `ReadOnlyIOStream` | → `read()` | **OK** (but allocates; see collapse-design readinto note) |

No `DelegatingStream` subclass that overrides `read` with a side effect was
found missing `readinto_passthrough=False` or an explicit `readinto`.

**Fusion implication:** a fused `ArchiveStream` that hashes **must not** keep
today's `readinto`→`inner.readinto` bypass unless the fused `readinto` itself
updates the hasher (preferably by hashing a `memoryview` of the caller's
buffer).

---

## 2. `VerifyingStream` EOF / close (`verify.py`)

### State machine (sequential, verify enabled)

```
                 read(n>0) data
    [armed] ─────────────────────► [armed, pos+=n, hash]
       │                                │
       │ read → b"" or pos≥expected       │ seek off frontier
       ▼                                ▼
    [_finish]                      [disarmed]  (no verify ever)
       │
       ├─ trailing probe if pos≥expected_size
       ├─ verify digests
       └─ note short if pos<expected_size → raise at close
```

Confirmed behaviours (with tests in `tests/test_codecs.py`):

- Size cap before inner read (`:219-239`); over-long via trailing `read(1)`
  (`:198-211`).
- Digest before short verdict (`:212-217`); hashless short deferred until after
  inner close (`:319-330`).
- Partial read then close skips verification.
- Accel-raises-instead-of-EOF on close probe → `TruncatedError` when still short
  (`:301-311`); typed `ArchiveyError` that is already
  `CorruptionError`/`TruncatedError` is kept as `finish_exc`.
- WinZip AES HMAC drained by the same trailing probe (`zip_aes.py:163-174`;
  `tests/test_zip_aes.py`).
- **F6 closed:** over-long hashed content whose CRC matches the *bloated*
  payload still raises via `expected_size`
  (`tests/test_codecs.py:619-635`). ZIP/7z/RAR pass `expected_size`.

### F1 — `read(0)` treated as EOF (Medium)

`verify.py:246-254`: any empty `data` with verify still armed runs `_finish`.
There is no `if n == 0: return b""` guard. Concrete triggers:

```python
# hashed: false CorruptionError (empty digest ≠ stored CRC)
VerifyingStream(io.BytesIO(b"abc"), {"crc32": crc32(b"abc")}).read(0)

# hashless + expected_size: false TruncatedError on close
s = VerifyingStream(io.BytesIO(b"abc"), {}, expected_size=3)
s.read(0); s.close()

# mid-stream after partial read — also fires (digest covers only prefix)
s = VerifyingStream(io.BytesIO(b"abc"), {"crc32": crc32(b"abc")})
s.read(1); s.read(0)  # CorruptionError
```

Contrast: `_GzipTruncationCheckStream` explicitly treats `read(0)` as non-EOF
(`tests/test_codecs.py:706-718`). Stdlib `BytesIO.read(0) == b""` without
end-of-stream side effects. Repro: `measurements.py f1`.

### F2 — close can skip teardown on some typed probe errors (Low)

`verify.py:292-300`: on the close-time `read(1)` probe, a non-`CorruptionError`/
`TruncatedError` `ArchiveyError` is re-raised **before** `self._inner.close()` /
`super().close()`. Repro with a custom inner raising `EncryptionError` on the
probe: after `close()`, both `VerifyingStream.closed` and `inner.closed` are
`False`. Shipped paths mostly raise `CorruptionError`/`TruncatedError` here
(AES HMAC, size overrun), so blast radius is limited — but a fused close path
should `try`/`finally` close regardless. Repro: `measurements.py f2`.

---

## 3. `SlicingStream` dual mode (`slice.py`)

| Rule | Where | Verdict |
|------|-------|---------|
| Locked mode: no unlocked `tell`/`seek` at construction when `start` given | `:99-109` | **OK** (tests `test_slice.py:273-304`) |
| Locked `read`: re-seek + read under same guard | `:148-160` | **OK** |
| `SEEK_END` unknown length probes under guard | `:178-185` | **OK** |
| Single-consumer: eager seek at construction; keep underlying in sync | `:110-116`, `:201-205` | **OK** |
| Negative `SEEK_CUR`/`SEEK_END` clamps; negative `SEEK_SET` raises | `:191-198` | **OK** (BytesIO match) |
| `own_source` controls close only; default non-owning | `:211-217` | **OK** |
| `read(0)` returns `b""` without side effects | `:146-147` | **OK** |

`fix_stream_start_position` (`:237-250`) adds a second slice only when a
seekable source is mid-stream — internal/codec use, not the member-boundary
layer.

---

## 4. `ArchiveStream` (`archive_stream.py`)

| Rule | Where | Verdict |
|------|-------|---------|
| Lazy open claimed once under lock; `open_fn` runs outside | `:193-216` | **OK** |
| Failed open leaves `_open_fn is None` (no retry) | `:224-227` | **OK** |
| `_fail`: `ArchiveyError` stamp → closed-file `ValueError` → translator | `:295-317` | **OK** |
| `#136` `_collapse_nested` steals lazy opener or opened inner; composes translate/stamp/rewind; neuters nested finalizer/`on_close` | `:238-293` | **OK** |
| Public `_inner` after first read is not an `ArchiveStream` | pinned `test_member_stream_contract.py` | **OK** |
| Finalizer/lease: attach after `_on_close`; close detaches + releases once | `archive_stream.py:104-156`, `:401-439`; `base_reader.py` register path | **OK** (interpreter-exit caveat documented `:109-115`) |
| `size`: explicit → opened `try_get_size` / `.size`; lazy unopened → `None` | `:167-191` | **OK** |

**Seam that #136 did not close:** collapse only runs when `open_fn()` **returns**
an `ArchiveStream`. The ZIP/7z/RAR hot path returns
`ArchiveStream(VerifyingStream(ArchiveStream(codec)))` from `_open_member`, so
collapse steals `VerifyingStream` and the **codec** `ArchiveStream` remains
underneath. Live dump (STORED `open` / `stream_members`):

```
ArchiveStream                  ← public
  VerifyingStream
    ArchiveStream              ← codec shim (STORED: identity over slice)
      SlicingStream
        BufferedReader
```

---

## 5. `SharedSource` / CONCURRENT — the fusion constraint

`SharedSource.view` (`shared.py:97-137`) mints locked non-owning
`SlicingStream`s; clamps past-EOF views; never closes a caller-owned handle.
ZIP uses an equivalent pattern (`SlicingStream(..., lock=ZipFile._lock)` in
`zip_reader.py:922-928`) without the `SharedSource` type. TAR/ISO use
`LockedStream` around library-owned seek-then-read handles.

**Why this blocks fusing member-boundary slicing into the public handle as a
general rule:** the slice is a *view object* that must be mintable N ways over
one source, with per-view `_pos` and atomic re-seek. The public `ArchiveStream`
is 1:1 with a member lease. Folding the view into the outer stream would either
(a) break multi-view fan-out, or (b) re-implement `SlicingStream` inside
`ArchiveStream` and still need the view type for `SharedSource` /
`fix_stream_start_position` / 7z LZMA caps. Keep the primitive; only the
per-member verify layer is a clean fold into the outer identity.
