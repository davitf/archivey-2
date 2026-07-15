# Seek-index correctness (F1, F5)

## F1 (High) — multi-stream `.xz` seek-point collision crashes on valid input

### What happens

`add_seek_points` merges decoder-emitted `SeekPoint`s into the sorted table. When two
points land on the **same `decompressed_offset`**, `_resolve_same_offset_collision`
decides among skip / forward-refine / abort:

```
# decompressor_stream.py:237
def _resolve_same_offset_collision(self, index: int, point: SeekPoint) -> None:
    existing = self._seek_points[index]
    if (existing.compressed_offset == point.compressed_offset
            and existing.state is point.state):
        return                                    # exact duplicate
    if (point.compressed_offset >= existing.compressed_offset
            and existing.state is point.state):
        self._seek_points[index] = point          # forward refinement, last-wins
        return
    assert False, (                               # <-- decompressor_stream.py:251
        "seek-point collision at the same decompressed_offset with "
        f"differing resume data: existing={existing!r} new={point!r}"
    )
```

The `add_seek_points` docstring states the intended invariant:

> Divergent `state` or a backwards `compressed_offset` still asserts (**xz/lzip should
> filter those away**).

XZ does **not** filter those away. The XZ decoder emits two *different kinds* of point
that can share a `decompressed_offset`:

1. **Progressive stream-start points** (`xz.py:564`), emitted when a whole `.xz`
   stream completes during forward reading:
   ```
   points.append(SeekPoint(stream_decomp_start, stream_comp_start, state=None))
   ```
   Their `compressed_offset` is the stream *start* (the 12-byte stream header).
2. **Block-bounds points** from the backward index scan (`build_index`, `xz.py:615`,
   and the progressive per-stream enrichment `xz.py:583`):
   ```
   SeekPoint(b.decompressed_start, b.compressed_start, state=b)   # state = _XzBlockBounds
   ```
   The **first block** of each stream has `decompressed_start == stream start`, but its
   `compressed_start` is the stream header **+ 12** (blocks begin after the header).

So for stream *k* (k > 1) starting at decompressed offset `D`:
- the progressive path emits `SeekPoint(D, header_start,      state=None)`
- `build_index` emits             `SeekPoint(D, header_start+12, state=<block>)`

Same `decompressed_offset` `D`, **different `state`** (`None` vs `_XzBlockBounds`) **and
a backwards `compressed_offset`** (`header_start < header_start+12`) → the third branch,
`assert False`.

### Why the tests miss it — ordering

The collision needs `build_index` to run **before** the progressive stream boundary is
crossed on the *same* stream object. Every seek test in
`test_seekable_streams.py` reads forward to EOF first
(`test_xz_backward_seek_uses_block_index:79` etc.), which sets `_index_built = True`
(`decompressor_stream.py:282`) so `build_index` never runs afterward — the safe
ordering. The unsafe ordering is the ordinary one for anything that wants the size
first:

- `stream.seek(0, io.SEEK_END)` then rewind and read (size-then-read), **or**
- `stream.try_get_size()` / `ArchiveStream.size` then read on the same handle.

Both call `_ensure_index_built` → `build_index` first (emitting the first-block points),
then a forward read emits the stream-start points → collision.

### Reproduction

```
uv run python review/next/03-stream-decoder-findings/repro.py     # F1 runs last
```

Minimal form:

```python
import io, lzma
from archivey.internal.streams.xz import XzDecompressorStream
data = b"".join(lzma.compress(p, format=lzma.FORMAT_XZ) for p in (b"A"*5000, b"B"*5000))
s = XzDecompressorStream(io.BytesIO(data), seekable=True)
s.seek(0, io.SEEK_END); s.seek(0)
s.read()      # AssertionError: seek-point collision ... existing=SeekPoint(5000, 108, state=_XzBlockBounds...) new=SeekPoint(5000, 96, state=None)
```

The input is a **valid** concatenation of two `.xz` streams — the shape produced by
`cat a.xz b.xz`, and by parallel compressors that emit multiple streams. Hostile input
can obviously trigger it too, but it does not need to be hostile.

### Severity / impact

- **Default CPython:** `AssertionError` propagates as a raw exception — it is not in the
  `ArchiveyError` tree, so it crosses the error boundary uncaught (violates the error
  contract; and it is a crash on valid input, undercutting the "uniform, reliable
  interface" of VISION #1).
- **`python -O`:** the assert is compiled out; the function falls through leaving the
  existing block-bounds point and dropping the stream-start point. In this specific
  case the surviving point still yields correct bytes (a block-chain resume at the
  stream's first block is equivalent to a sequential resume at the stream start), so
  `-O` is *benign here* — but the code is relying on that by accident, and the
  "divergent state" branch was written precisely because the author expected it to be
  unreachable.

### Fix direction (maintainer decision — see QUESTIONS Q1)

Two clean options; they are not equivalent and the maintainer should pick:

- **(a) Make the invariant true.** Have the XZ decoder *not* emit a stream-start
  `state=None` point when it will also produce (or has produced) a block-bounds point at
  that offset — i.e. drop the redundant stream-start point for indexed streams, matching
  the "xz/lzip filter those away" comment. This keeps the assert as a real
  invariant-violation tripwire.
- **(b) Make the merge total.** Redefine `_resolve_same_offset_collision` so a
  divergent-state collision is *resolved* (prefer the richer block-bounds `state`; it
  gives cheaper random access and its `compressed_offset` is the safe resume point)
  rather than asserted. This is the hostile-input-robust choice: an attacker who can
  craft colliding points must never be able to raise a non-`ArchiveyError`.

Recommendation: do **both** — (a) so the layer stops generating the collision, and (b)
so a future decoder (or hostile stream) cannot turn a seek-point collision into a
process crash. The assert as written is a latent DoS on any codec whose points are not
perfectly filtered.

---

## F5 (Medium, test-coverage) — no seek-math property test; old finding #6 still open

The brief's Hunt A asks whether a "seek-math property test (old finding #6, still open)"
now exists. It does not. `grep -rn "given\|hypothesis" tests/test_seekable_streams.py`
is empty; the seekable-stream suite is a set of fixed-sequence example tests, and every
one of them consumes the stream forward-to-EOF before exercising a seek.

That single structural choice is why F1 shipped: the property that actually matters —
*"for any interleaving of `try_get_size` / `SEEK_END` / forward-read / backward-seek, the
decoded bytes at every offset match the ground truth and no non-`ArchiveyError`
escapes"* — is untested. A `hypothesis` test that (1) builds random multi-stream `.xz`
and multi-member `.lz`/`.Z`, (2) drives a random sequence of size-probes and seeks, and
(3) asserts each `read` matches the plaintext slice, would have flagged F1 immediately
and would guard the arithmetic that is the highest-risk part of this layer.

`hypothesis>=6.100.0` is already a dev dependency (`pyproject.toml:112`) and is used
elsewhere (`tests/test_property_safety.py`), so there is no new tooling cost.
